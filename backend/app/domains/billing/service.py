"""Checkout, webhooks, subscriptions and refunds.

The single rule this file exists to enforce:

> Never grant access based only on a frontend callback.

Access is granted in exactly one function — :func:`_apply_event` — and only for
an event whose signature this server verified against the provider's secret. A
client can start a checkout and can report that the payment sheet closed, and
neither of those changes what anybody is entitled to.

Three failure modes get explicit handling because in production they are
routine, not exotic:

* **Duplicates.** Providers retry until they get a 2xx. Identity is the unique
  constraint on ``(provider, provider_event_id)``; a second delivery collides on
  insert and is answered "already handled".
* **Delay.** An event can arrive minutes or hours late. Nothing here reads the
  clock to decide what a payment means — the event carries its own meaning.
* **Out of order.** A renewal can land before the activation it follows.
  ``Subscription.last_event_epoch`` holds the provider timestamp of the last
  event applied, and an older one is recorded and then ignored, so the state
  machine cannot be walked backwards.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BILLING_GRACE_DAYS, SUBSCRIPTIONS_AVAILABLE
from app.domains.billing import catalogue, entitlements
from app.domains.billing.models import (
    BillingAuditEvent, Order, PaymentEvent, Refund, Subscription,
)
from app.domains.billing.providers import base as provider_base
from app.domains.billing.providers.factory import get_provider
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import (
    NotFoundError, SubscriptionsUnavailableError, ValidationFailedError,
)

logger = logging.getLogger(__name__)

OUTCOME_PROCESSED = "processed"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_OUT_OF_ORDER = "out_of_order"
OUTCOME_UNMATCHED = "unmatched"
OUTCOME_IGNORED = "ignored"

PERIOD_DAYS = {"monthly": 30, "yearly": 365}


# --- Checkout ---------------------------------------------------------------------


async def create_checkout(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    offer_key: str,
    occasion_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a payment. Grants nothing.

    Refused at the first step when billing is switched off — a Phase 1 fix that
    still holds. Inviting somebody to pay and then refusing at confirmation is
    how you produce a "payment failed" message for a payment that was never
    attempted.
    """
    if not SUBSCRIPTIONS_AVAILABLE:
        raise SubscriptionsUnavailableError()

    offer = catalogue.offer_by_key(offer_key)
    if offer is None:
        raise ValidationFailedError(f"'{offer_key}' is not something we sell.", field="offer_key")

    if idempotency_key:
        existing = (await session.execute(
            select(Order).where(
                Order.account_id == account_id,
                Order.idempotency_key == idempotency_key,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return _serialize_order(existing, replayed=True)

    provider = get_provider()
    try:
        checkout = await provider.create_checkout(
            amount_inr=offer.amount_inr,
            currency=offer.currency,
            receipt=f"gg_{uuid.uuid4().hex[:18]}",
            notes={
                "account_id": str(account_id),
                "offer_key": offer.key,
                "plan_key": offer.plan_key,
            },
        )
    except provider_base.ProviderUnavailableError as exc:
        logger.warning("checkout_provider_unavailable reason=%s", exc)
        raise SubscriptionsUnavailableError()

    order = Order(
        account_id=account_id, offer_key=offer.key, plan_key=offer.plan_key,
        provider=checkout.provider, provider_order_id=checkout.provider_order_id,
        amount_inr=offer.amount_inr, currency=offer.currency, status="created",
        simulated=checkout.simulated, occasion_id=occasion_id,
        idempotency_key=idempotency_key,
        notes={"period": offer.period},
    )
    session.add(order)
    await session.flush()

    session.add(BillingAuditEvent(
        account_id=account_id, action="checkout_created", actor="user",
        subject_type="order", subject_id=str(order.id),
        detail={"offer": offer.key, "amount_inr": offer.amount_inr, "provider": checkout.provider},
    ))

    payload = _serialize_order(order)
    payload["checkout"] = checkout.as_dict()
    return payload


def _serialize_order(order: Order, *, replayed: bool = False) -> Dict[str, Any]:
    return {
        "order_id": str(order.id),
        "offer_key": order.offer_key,
        "plan_key": order.plan_key,
        "amount_inr": order.amount_inr,
        "currency": order.currency,
        "status": order.status,
        "provider": order.provider,
        "provider_order_id": order.provider_order_id,
        "simulated": order.simulated,
        "replayed": replayed,
        "note": (
            "Access is granted once our server has confirmed this payment with the provider. "
            "Closing the payment sheet is not by itself proof of payment."
        ),
    }


async def cancel_checkout(
    session: AsyncSession, account_id: uuid.UUID, order_id: uuid.UUID
) -> Dict[str, Any]:
    """The user backed out. Nothing was granted, so nothing is taken away."""
    order = (await session.execute(
        select(Order).where(Order.id == order_id, Order.account_id == account_id)
    )).scalar_one_or_none()
    if order is None:
        raise NotFoundError("We could not find that order.")
    if order.status == "paid":
        raise ValidationFailedError(
            "That payment already went through. Contact support if you want a refund.",
            field="order_id",
        )
    order.status = "cancelled"
    order.cancelled_at = utcnow()
    session.add(BillingAuditEvent(
        account_id=account_id, action="checkout_cancelled", actor="user",
        subject_type="order", subject_id=str(order.id), detail={},
    ))
    return {"order_id": str(order.id), "status": order.status, "message": "Cancelled. Nothing was charged."}


# --- Webhooks --------------------------------------------------------------------------


async def handle_webhook(
    session: AsyncSession, *, raw_body: bytes, signature: Optional[str]
) -> Dict[str, Any]:
    """Verify, record, then apply. In that order, always.

    Verification failure raises before anything is written, so a forged body
    cannot even create an audit row an attacker chose the contents of.
    """
    provider = get_provider()
    payload = provider.verify_webhook(raw_body=raw_body, signature=signature)
    event = provider.parse_event(payload)

    account_id = _account_from_notes(event.notes)

    inserted = (await session.execute(
        pg_insert(PaymentEvent.__table__)
        .values(
            id=uuid.uuid4(), account_id=account_id, provider=provider.name,
            provider_event_id=event.provider_event_id, raw_event_name=event.raw_event_name,
            kind=event.kind, provider_order_id=event.provider_order_id,
            provider_payment_id=event.provider_payment_id,
            provider_subscription_id=event.provider_subscription_id,
            provider_refund_id=event.provider_refund_id, amount_inr=event.amount_inr,
            occurred_at_epoch=event.occurred_at_epoch, signature_verified=True,
            outcome=OUTCOME_PROCESSED, detail="",
        )
        .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
        .returning(PaymentEvent.__table__.c.id)
    )).scalar_one_or_none()

    if inserted is None:
        # Seen before. Providers retry until they get a 2xx, so this is the
        # normal path, not an error.
        logger.info("webhook_duplicate provider=%s event=%s", provider.name, event.raw_event_name)
        return {
            "received": True, "outcome": OUTCOME_DUPLICATE,
            "message": "Already handled.",
        }

    record = await session.get(PaymentEvent, inserted)
    outcome, detail = await _apply_event(session, event, record)
    if record is not None:
        record.outcome = outcome
        record.detail = detail
    return {"received": True, "outcome": outcome, "message": detail}


def _account_from_notes(notes: Dict[str, Any]) -> Optional[uuid.UUID]:
    value = (notes or {}).get("account_id")
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError):
        return None


async def _apply_event(
    session: AsyncSession, event: provider_base.WebhookEvent, record: Optional[PaymentEvent]
) -> tuple[str, str]:
    """The only place in this application that grants or revokes paid access."""
    if event.kind == provider_base.EVENT_UNKNOWN:
        return OUTCOME_IGNORED, f"Nothing to do for '{event.raw_event_name}'."

    if event.kind == provider_base.EVENT_REFUNDED:
        return await _apply_refund(session, event)

    order = await _find_order(session, event)

    if event.kind == provider_base.EVENT_PAYMENT_FAILED:
        if order is not None and order.status == "created":
            order.status = "failed"
        return OUTCOME_PROCESSED, "Payment failed. Nothing was granted."

    if event.kind == provider_base.EVENT_PAYMENT_SUCCEEDED:
        if order is None:
            # Recorded rather than discarded: a payment we cannot match to an
            # order is exactly what support needs to see.
            return OUTCOME_UNMATCHED, "Payment could not be matched to an order."
        return await _apply_payment(session, event, order, record)

    if event.kind in (
        provider_base.EVENT_SUBSCRIPTION_ACTIVATED,
        provider_base.EVENT_SUBSCRIPTION_RENEWED,
        provider_base.EVENT_SUBSCRIPTION_PAYMENT_FAILED,
        provider_base.EVENT_SUBSCRIPTION_CANCELLED,
        provider_base.EVENT_SUBSCRIPTION_COMPLETED,
    ):
        return await _apply_subscription(session, event, order)

    return OUTCOME_IGNORED, "No handler for that event."


async def _find_order(
    session: AsyncSession, event: provider_base.WebhookEvent
) -> Optional[Order]:
    if event.provider_order_id:
        row = (await session.execute(
            select(Order).where(Order.provider_order_id == event.provider_order_id)
        )).scalar_one_or_none()
        if row is not None:
            return row
    order_id = (event.notes or {}).get("order_id")
    if order_id:
        try:
            return await session.get(Order, uuid.UUID(str(order_id)))
        except (ValueError, AttributeError):
            return None
    return None


async def _apply_payment(
    session: AsyncSession,
    event: provider_base.WebhookEvent,
    order: Order,
    record: Optional[PaymentEvent],
) -> tuple[str, str]:
    """A one-off payment succeeded: grant what it bought."""
    if record is not None and record.account_id is None:
        record.account_id = order.account_id

    if order.status == "paid":
        return OUTCOME_DUPLICATE, "That order was already paid."

    # What was charged must match what we asked for. A mismatch is not
    # something to reconcile automatically — it goes to support.
    if event.amount_inr is not None and event.amount_inr != order.amount_inr:
        return OUTCOME_UNMATCHED, (
            f"Amount did not match: expected {order.amount_inr}, provider reported {event.amount_inr}."
        )

    order.status = "paid"
    order.paid_at = utcnow()

    plan = catalogue.PLAN_BY_KEY[order.plan_key]
    today = date.today()

    if plan.recurring:
        period = str(order.notes.get("period") or "monthly")
        days = PERIOD_DAYS.get(period, 30)
        subscription = Subscription(
            account_id=order.account_id, plan_key=order.plan_key, offer_key=order.offer_key,
            provider=order.provider, provider_subscription_id=event.provider_subscription_id,
            status="active", current_period_start=today,
            current_period_end=today + timedelta(days=days),
            last_event_epoch=event.occurred_at_epoch,
        )
        session.add(subscription)
        await session.flush()
        await entitlements.grant_plan(
            session, order.account_id, order.plan_key,
            source=entitlements.SOURCE_PLAN, source_id=subscription.id,
            starts_on=today, expires_on=subscription.current_period_end,
        )
        return OUTCOME_PROCESSED, "Subscription started."

    # A one-off pass. Valid for a fixed window from the day it was bought.
    expires = today + timedelta(days=plan.valid_days or 14)
    await entitlements.grant_plan(
        session, order.account_id, order.plan_key,
        source=entitlements.SOURCE_PASS, source_id=order.id,
        starts_on=today, expires_on=expires,
        period_label=f"pass:{order.id}",
    )
    return OUTCOME_PROCESSED, "Pass activated."


async def _apply_subscription(
    session: AsyncSession, event: provider_base.WebhookEvent, order: Optional[Order]
) -> tuple[str, str]:
    """Move a subscription through its life, refusing to go backwards."""
    subscription = None
    if event.provider_subscription_id:
        subscription = (await session.execute(
            select(Subscription).where(
                Subscription.provider_subscription_id == event.provider_subscription_id
            )
        )).scalar_one_or_none()
    if subscription is None and order is not None:
        subscription = (await session.execute(
            select(Subscription).where(
                Subscription.account_id == order.account_id,
                Subscription.offer_key == order.offer_key,
            ).order_by(Subscription.created_at.desc())
        )).scalars().first()

    if subscription is None:
        return OUTCOME_UNMATCHED, "No subscription matched that event."

    # Out-of-order guard. A renewal can arrive before the activation it
    # follows; applying it would move the period backwards.
    if (
        event.occurred_at_epoch is not None
        and subscription.last_event_epoch is not None
        and event.occurred_at_epoch < subscription.last_event_epoch
    ):
        return OUTCOME_OUT_OF_ORDER, (
            "That event is older than one already applied, so it was recorded and skipped."
        )
    if event.occurred_at_epoch is not None:
        subscription.last_event_epoch = event.occurred_at_epoch

    today = date.today()

    if event.kind == provider_base.EVENT_SUBSCRIPTION_RENEWED:
        days = PERIOD_DAYS.get(str(order.notes.get("period")) if order else "monthly", 30)
        subscription.status = "active"
        subscription.grace_until = None
        subscription.renewal_failures = 0
        subscription.current_period_start = today
        subscription.current_period_end = today + timedelta(days=days)
        await entitlements.grant_plan(
            session, subscription.account_id, subscription.plan_key,
            source=entitlements.SOURCE_PLAN, source_id=subscription.id,
            starts_on=today, expires_on=subscription.current_period_end,
        )
        return OUTCOME_PROCESSED, "Subscription renewed."

    if event.kind == provider_base.EVENT_SUBSCRIPTION_ACTIVATED:
        subscription.status = "active"
        subscription.grace_until = None
        return OUTCOME_PROCESSED, "Subscription active."

    if event.kind == provider_base.EVENT_SUBSCRIPTION_PAYMENT_FAILED:
        # Access continues through the grace window. Cutting somebody off the
        # instant a card is declined is how you lose a customer who would have
        # paid on the retry.
        subscription.renewal_failures += 1
        subscription.status = "grace"
        subscription.grace_until = today + timedelta(days=BILLING_GRACE_DAYS)
        session.add(BillingAuditEvent(
            account_id=subscription.account_id, action="renewal_failed", actor="webhook",
            subject_type="subscription", subject_id=str(subscription.id),
            detail={"failures": subscription.renewal_failures, "grace_until": subscription.grace_until.isoformat()},
        ))
        return OUTCOME_PROCESSED, "Renewal failed. Access continues during the grace period."

    if event.kind == provider_base.EVENT_SUBSCRIPTION_CANCELLED:
        # Cancelled is not the same as ended. Somebody who has paid for this
        # month keeps this month.
        subscription.status = "cancelled"
        subscription.cancel_at_period_end = True
        subscription.cancelled_at = utcnow()
        session.add(BillingAuditEvent(
            account_id=subscription.account_id, action="subscription_cancelled", actor="webhook",
            subject_type="subscription", subject_id=str(subscription.id),
            detail={"access_until": subscription.current_period_end.isoformat()},
        ))
        return OUTCOME_PROCESSED, (
            f"Cancelled. Access continues until {subscription.current_period_end.isoformat()}."
        )

    if event.kind == provider_base.EVENT_SUBSCRIPTION_COMPLETED:
        subscription.status = "expired"
        subscription.ended_at = utcnow()
        await entitlements.revoke_for_source(
            session, subscription.account_id, subscription.id, reason="subscription_ended",
        )
        return OUTCOME_PROCESSED, "Subscription ended."

    return OUTCOME_IGNORED, "No handler for that subscription event."


async def _apply_refund(
    session: AsyncSession, event: provider_base.WebhookEvent
) -> tuple[str, str]:
    """Money went back, so the access it bought comes back too."""
    order = await _find_order(session, event)
    if order is None and event.provider_payment_id:
        order = (await session.execute(
            select(Order).where(Order.provider_order_id == event.provider_payment_id)
        )).scalar_one_or_none()
    if order is None:
        return OUTCOME_UNMATCHED, "Refund could not be matched to an order."

    existing = (await session.execute(
        select(Refund).where(
            Refund.provider == order.provider,
            Refund.provider_refund_id == event.provider_refund_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return OUTCOME_DUPLICATE, "That refund was already processed."

    revoked = await entitlements.revoke_for_source(
        session, order.account_id, order.id, reason="refunded",
    )

    # A refunded subscription ends now, not at period end.
    subscriptions = (await session.execute(
        select(Subscription).where(
            Subscription.account_id == order.account_id,
            Subscription.offer_key == order.offer_key,
            Subscription.status.in_(("active", "grace", "past_due", "cancelled")),
        )
    )).scalars().all()
    for subscription in subscriptions:
        subscription.status = "refunded"
        subscription.ended_at = utcnow()
        revoked += await entitlements.revoke_for_source(
            session, order.account_id, subscription.id, reason="refunded",
        )

    order.status = "refunded"
    session.add(Refund(
        account_id=order.account_id, order_id=order.id, provider=order.provider,
        provider_refund_id=event.provider_refund_id, amount_inr=event.amount_inr,
        reason=str((event.notes or {}).get("reason") or "provider_refund"),
        entitlements_revoked=revoked,
    ))
    return OUTCOME_PROCESSED, f"Refunded. {revoked} entitlement(s) revoked."


# --- Reading state ------------------------------------------------------------------------


async def status(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> Dict[str, Any]:
    """What this account has bought, and what is in force."""
    day = today or date.today()
    await _settle_expired(session, account_id, today=day)

    subscription = (await session.execute(
        select(Subscription).where(Subscription.account_id == account_id)
        .order_by(Subscription.created_at.desc())
    )).scalars().first()

    orders = (await session.execute(
        select(Order).where(Order.account_id == account_id)
        .order_by(Order.created_at.desc()).limit(20)
    )).scalars().all()

    snapshot = await entitlements.snapshot(session, account_id, today=day)
    return {
        "billing_available": SUBSCRIPTIONS_AVAILABLE,
        "plan_key": snapshot["plan_key"],
        "plan_label": snapshot["plan_label"],
        "subscription": snapshot["subscription"],
        "subscription_history": None if subscription is None else {
            "status": subscription.status,
            "renewal_failures": subscription.renewal_failures,
            "ended_at": subscription.ended_at.isoformat() if subscription.ended_at else None,
        },
        "orders": [
            {
                "id": str(row.id), "offer_key": row.offer_key, "status": row.status,
                "amount_inr": row.amount_inr, "currency": row.currency,
                "simulated": row.simulated,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in orders
        ],
        "entitlements": snapshot["features"],
        "cache_seconds": snapshot["cache_seconds"],
        "valid_until": snapshot["valid_until"],
    }


async def _settle_expired(
    session: AsyncSession, account_id: uuid.UUID, *, today: date
) -> None:
    """Close out anything whose time has run out.

    Delegates to ``entitlements`` so there is exactly one implementation, and so
    every read path — not just this route — settles an expired subscription.
    """
    await entitlements.settle_expired_subscriptions(session, account_id, today=today)
    await entitlements.expire_due(session, account_id, today=today)
