"""Billing, entitlement, support and try-on routes.

Two routes here are unlike anything else in the application, and both
deliberately.

``POST /billing/webhook`` takes **no authentication** — it is called by the
payment provider, not by a user — and reads the **raw request body** rather
than a parsed model. Both are required: the signature is computed over the
exact bytes sent, and re-serialising a parsed body changes whitespace and key
order and breaks verification. The signature is what authenticates it.

``GET /billing/offers`` is readable while billing is switched off, so the app
can show what is coming without offering to take money. Every other billing
route refuses until ``SUBSCRIPTIONS_AVAILABLE`` is true.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SUBSCRIPTIONS_AVAILABLE
from app.domains.billing import catalogue, entitlements, operations, service
from app.domains.billing.providers import base as provider_base
from app.domains.billing.providers.factory import provider_status
from app.domains.billing.schemas import (
    CheckoutCancel, CheckoutRequest, EventPassRequest, StylistReviewRequest,
    SupportRequest, TryOnRequest,
)
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import (
    NotFoundError, SubscriptionsUnavailableError, ValidationFailedError,
)
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_billing"))])


# --- The paywall -------------------------------------------------------------------


@router.get("/billing/offers")
async def list_offers(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What is on sale, priced from configuration, described as outcomes.

    Readable even when billing is off, so the app can be honest about what
    exists rather than hiding it and surprising people later.
    """
    variants = await operations.assignments_for(session, current.account_id)
    await session.commit()
    return {
        "billing_available": SUBSCRIPTIONS_AVAILABLE,
        "offers": [row.as_dict() for row in catalogue.offers()],
        "catalogue": catalogue.as_dict(),
        "provider": provider_status(),
        "experiments": variants,
        "message": None if SUBSCRIPTIONS_AVAILABLE else (
            "GlamGenius is in a private beta, so there is nothing to pay for yet. "
            "This is what will be available."
        ),
    }


@router.post("/billing/checkout")
async def create_checkout(
    body: CheckoutRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Start a payment. Grants nothing on its own."""
    result = await service.create_checkout(
        session, current.account_id,
        offer_key=body.offer_key, occasion_id=body.occasion_id,
        idempotency_key=body.idempotency_key,
    )
    await session.commit()
    return result


@router.post("/billing/checkout/cancel")
async def cancel_checkout(
    body: CheckoutCancel,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The user backed out of the payment sheet."""
    result = await service.cancel_checkout(session, current.account_id, body.order_id)
    await session.commit()
    return result


@router.post("/billing/webhook")
async def billing_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_razorpay_signature: Optional[str] = Header(default=None, alias="X-Razorpay-Signature"),
    x_signature: Optional[str] = Header(default=None, alias="X-Signature"),
):
    """The provider telling us what happened. **This is what grants access.**

    Unauthenticated by design — the signature is the authentication. The raw
    body is passed through untouched, because the signature covers those exact
    bytes.
    """
    raw_body = await request.body()
    signature = x_razorpay_signature or x_signature

    try:
        result = await service.handle_webhook(session, raw_body=raw_body, signature=signature)
    except provider_base.SignatureInvalidError as exc:
        # Nothing is written. A forged body must not be able to create even an
        # audit row whose contents an attacker chose.
        await session.rollback()
        raise ValidationFailedError(
            "That webhook could not be verified.", field="signature",
        ) from exc
    except provider_base.ProviderUnavailableError as exc:
        await session.rollback()
        raise SubscriptionsUnavailableError() from exc

    await session.commit()
    return result


@router.get("/billing/status")
async def billing_status(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What this account has bought, and what is in force right now."""
    result = await service.status(session, current.account_id)
    await session.commit()
    return result


# --- Entitlements ----------------------------------------------------------------------


@router.get("/entitlements")
async def get_entitlements(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Everything this account may do, and for how long it may be cached."""
    result = await entitlements.snapshot(session, current.account_id)
    await session.commit()
    return result


@router.post("/event-passes")
async def buy_event_pass(
    body: EventPassRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Buy a pass for one occasion.

    Ownership of the occasion is checked here rather than trusted, so a pass
    cannot be attached to somebody else's event.
    """
    from sqlalchemy import select

    from app.domains.recommendation.models import OccasionRecord

    occasion = (await session.execute(
        select(OccasionRecord).where(
            OccasionRecord.id == body.occasion_id,
            OccasionRecord.account_id == current.account_id,
        )
    )).scalar_one_or_none()
    if occasion is None:
        raise NotFoundError("We could not find that occasion.")

    result = await service.create_checkout(
        session, current.account_id, offer_key="event_pass",
        occasion_id=body.occasion_id, idempotency_key=body.idempotency_key,
    )
    await session.commit()
    result["occasion_id"] = str(body.occasion_id)
    return result


@router.get("/account/usage")
async def account_usage(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What has been used this period, itemised.

    Itemised on purpose: "you have used three" is only fair if a person can see
    which three.
    """
    from sqlalchemy import select

    from app.domains.billing.models import EntitlementUsage

    snapshot = await entitlements.snapshot(session, current.account_id)
    rows = (await session.execute(
        select(EntitlementUsage)
        .where(
            EntitlementUsage.account_id == current.account_id,
            EntitlementUsage.period_key == entitlements.period_key(),
        )
        .order_by(EntitlementUsage.created_at.desc())
        .limit(200)
    )).scalars().all()
    await session.commit()

    return {
        "period_key": entitlements.period_key(),
        "plan_key": snapshot["plan_key"],
        "features": snapshot["features"],
        "history": [
            {
                "feature": row.feature,
                "label": catalogue.FEATURE_LABELS.get(row.feature, row.feature),
                "quantity": row.quantity,
                "at": row.created_at.isoformat() if row.created_at else None,
                "detail": row.detail,
                # Negative quantities are allowance given back when work failed.
                "credit": row.quantity < 0,
            }
            for row in rows
        ],
        "note": "Anything we could not deliver is credited back and shows here as a credit.",
    }


# --- Support and professional review -------------------------------------------------------


@router.post("/support")
async def open_support_case(
    body: SupportRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Ask for help. Your billing state is attached so you need not explain it."""
    result = await operations.open_case(
        session, current.account_id,
        category=body.category, subject=body.subject, message=body.message,
    )
    await session.commit()
    return result


@router.post("/stylist-review")
async def request_stylist_review(
    body: StylistReviewRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Ask a human stylist to look at something."""
    result = await operations.request_stylist_review(
        session, current.account_id,
        subject_type=body.subject_type, subject_id=body.subject_id, question=body.question,
    )
    await session.commit()
    return result


# --- Virtual try-on ---------------------------------------------------------------------------


@router.get("/tryon/status")
async def tryon_status(
    current: CurrentAccount = Depends(get_current_account),
):
    """Whether try-on exists yet. It does not, and this says so plainly."""
    return operations.tryon_status()


@router.post("/tryon/jobs")
async def create_tryon_job(
    body: TryOnRequest,
    current: CurrentAccount = Depends(get_current_account),
):
    """Would start a try-on job. Refuses honestly while no provider is chosen."""
    provider = operations.get_tryon_provider()
    if not provider.is_configured():
        raise NotFoundError(
            "Virtual try-on is not available yet. We would rather show you nothing than a "
            "made-up picture of you in clothes you have never worn."
        )
    return await provider.create_job(
        source_media_id=body.source_media_id, item_ids=[str(row) for row in body.item_ids],
    )
