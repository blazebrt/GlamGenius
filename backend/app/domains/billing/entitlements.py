"""What an account may do, right now.

Two rules shape everything here.

**Free is a real plan, not a locked door.** Every account gets Free
entitlements without paying, granted lazily on first read. Somebody who never
pays has a working app.

**Nothing is granted by a client.** Every function that creates an entitlement
takes a ``source`` and a ``source_id`` pointing at an order or subscription that
a verified webhook produced. There is no code path from an HTTP request body to
a grant.

Consumption is recorded *before* the expensive work runs and carries an
idempotency key, so a retried request cannot spend somebody's allowance twice.
If the work then fails, the caller refunds the usage explicitly — a failed
answer must not cost a person one of their decisions.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ENTITLEMENT_CACHE_SECONDS
from app.domains.billing import catalogue
from app.domains.billing.models import (
    BillingAuditEvent, Entitlement, EntitlementUsage, Subscription,
)
from app.shared.database.base import utcnow

SOURCE_FREE = "free"
SOURCE_PLAN = "plan"
SOURCE_PASS = "event_pass"
SOURCE_GRANT = "support_grant"


def period_key(day: Optional[date] = None) -> str:
    """YYYY-MM. Matches the V1 scan month key so the two reconcile."""
    now = day or date.today()
    return f"{now.year:04d}-{now.month:02d}"


def _period_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end


async def ensure_free(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> List[Entitlement]:
    """Grant this month's Free entitlements if they are not there yet.

    Idempotent by construction: the unique constraint on the grant means a
    second call collides and does nothing rather than doubling somebody's
    allowance.
    """
    day = today or date.today()
    key = period_key(day)
    start, end = _period_bounds(day)

    for feature, limit in catalogue.FREE.limits.items():
        statement = (
            pg_insert(Entitlement.__table__)
            .values(
                id=uuid.uuid4(), account_id=account_id, feature=feature,
                plan_key=catalogue.PLAN_FREE, period_key=key, limit_value=limit,
                used=0, source=SOURCE_FREE, source_id=None, grant_key=SOURCE_FREE,
                starts_on=start, expires_on=end,
            )
            .on_conflict_do_nothing(
                index_elements=["account_id", "feature", "period_key", "grant_key"]
            )
        )
        await session.execute(statement)

    return await active_for(session, account_id, today=day)


async def active_for(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> List[Entitlement]:
    """Every entitlement in force today.

    Filters revoked and expired rows at the query level, so a caller cannot
    forget to check either.
    """
    day = today or date.today()
    rows = (await session.execute(
        select(Entitlement).where(
            Entitlement.account_id == account_id,
            Entitlement.revoked_at.is_(None),
            Entitlement.starts_on <= day,
        )
    )).scalars().all()
    return [row for row in rows if row.expires_on is None or row.expires_on >= day]


async def grant_plan(
    session: AsyncSession,
    account_id: uuid.UUID,
    plan_key: str,
    *,
    source: str,
    source_id: uuid.UUID,
    starts_on: date,
    expires_on: Optional[date],
    period_label: Optional[str] = None,
) -> List[Entitlement]:
    """Grant everything a plan includes.

    Only ever called from the webhook handler, after a signature has been
    verified. ``source_id`` is what makes a later refund able to revoke exactly
    what this payment bought.
    """
    plan = catalogue.PLAN_BY_KEY.get(plan_key)
    if plan is None:
        raise ValueError(f"'{plan_key}' is not a plan.")

    key = period_label or period_key(starts_on)
    granted: List[Entitlement] = []
    for feature, limit in plan.limits.items():
        statement = (
            pg_insert(Entitlement.__table__)
            .values(
                id=uuid.uuid4(), account_id=account_id, feature=feature,
                plan_key=plan_key, period_key=key, limit_value=limit, used=0,
                source=source, source_id=source_id, grant_key=str(source_id),
                starts_on=starts_on, expires_on=expires_on,
            )
            .on_conflict_do_nothing(
                index_elements=["account_id", "feature", "period_key", "grant_key"]
            )
            .returning(Entitlement.__table__.c.id)
        )
        inserted = (await session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            row = await session.get(Entitlement, inserted)
            if row is not None:
                granted.append(row)

    session.add(BillingAuditEvent(
        account_id=account_id, action="entitlements_granted", actor="webhook",
        subject_type=source, subject_id=str(source_id),
        detail={
            "plan": plan_key, "features": len(plan.limits),
            "newly_granted": len(granted), "period_key": key,
            "starts_on": starts_on.isoformat(),
            "expires_on": expires_on.isoformat() if expires_on else None,
        },
    ))
    return granted


async def revoke_for_source(
    session: AsyncSession, account_id: uuid.UUID, source_id: uuid.UUID, *, reason: str
) -> int:
    """Revoke exactly what one payment bought. Used by refunds.

    Scoped to ``source_id`` on purpose: refunding an Event Pass must not touch
    a Plus subscription the same person is also paying for.
    """
    rows = (await session.execute(
        select(Entitlement).where(
            Entitlement.account_id == account_id,
            Entitlement.source_id == source_id,
            Entitlement.revoked_at.is_(None),
        )
    )).scalars().all()

    for row in rows:
        row.revoked_at = utcnow()
        row.revoked_reason = reason[:120]

    session.add(BillingAuditEvent(
        account_id=account_id, action="entitlements_revoked", actor="webhook",
        subject_type="source", subject_id=str(source_id),
        detail={"reason": reason, "revoked": len(rows)},
    ))
    # Flushed explicitly. This project builds sessions with ``autoflush=False``,
    # so a later read in the same request would otherwise still see these rows
    # as active — which is precisely the bug where a refunded plan keeps
    # working until the next request.
    await session.flush()
    return len(rows)


async def expire_due(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> int:
    """Close out anything whose period has ended.

    Expiry is enforced at read time as well (``active_for`` filters on dates),
    so a job that never runs cannot leave somebody with access they should not
    have. This just makes the state tidy and auditable.
    """
    day = today or date.today()
    rows = (await session.execute(
        select(Entitlement).where(
            Entitlement.account_id == account_id,
            Entitlement.revoked_at.is_(None),
            Entitlement.expires_on.is_not(None),
            Entitlement.expires_on < day,
        )
    )).scalars().all()
    for row in rows:
        row.revoked_at = utcnow()
        row.revoked_reason = "expired"
    if rows:
        session.add(BillingAuditEvent(
            account_id=account_id, action="entitlements_expired", actor="system",
            detail={"expired": len(rows), "as_of": day.isoformat()},
        ))
        # See ``revoke_for_source``: autoflush is off, so this must be explicit.
        await session.flush()
    return len(rows)


# --- Checking and consuming ---------------------------------------------------------


class EntitlementDecision:
    """Whether an action is allowed, and what to tell the user if not."""

    def __init__(
        self,
        allowed: bool,
        feature: str,
        *,
        limit: Optional[int] = None,
        used: int = 0,
        plan_key: str = catalogue.PLAN_FREE,
        entitlement: Optional[Entitlement] = None,
        reason: str = "",
    ) -> None:
        self.allowed = allowed
        self.feature = feature
        self.limit = limit
        self.used = used
        self.plan_key = plan_key
        self.entitlement = entitlement
        self.reason = reason

    @property
    def remaining(self) -> Optional[int]:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "feature": self.feature,
            "allowed": self.allowed,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "plan_key": self.plan_key,
            "reason": self.reason,
            "label": catalogue.FEATURE_LABELS.get(self.feature, self.feature),
        }


async def check(
    session: AsyncSession, account_id: uuid.UUID, feature: str, *, today: Optional[date] = None
) -> EntitlementDecision:
    """Can this account do this, right now?

    Where several entitlements cover the same feature — Free plus an Event
    Pass, say — the most generous wins. Somebody who has paid should not be
    held to the free limit.
    """
    day = today or date.today()
    await ensure_free(session, account_id, today=day)
    rows = [row for row in await active_for(session, account_id, today=day) if row.feature == feature]

    if not rows:
        return EntitlementDecision(
            False, feature,
            reason=f"{catalogue.FEATURE_LABELS.get(feature, feature)} is not part of your plan.",
        )

    # Unmetered beats any number.
    unmetered = next((row for row in rows if row.limit_value is None), None)
    if unmetered is not None:
        return EntitlementDecision(
            True, feature, limit=None, used=unmetered.used,
            plan_key=unmetered.plan_key, entitlement=unmetered,
        )

    best = max(rows, key=lambda row: (row.limit_value or 0) - row.used)
    remaining = (best.limit_value or 0) - best.used
    if remaining > 0:
        return EntitlementDecision(
            True, feature, limit=best.limit_value, used=best.used,
            plan_key=best.plan_key, entitlement=best,
        )

    return EntitlementDecision(
        False, feature, limit=best.limit_value, used=best.used, plan_key=best.plan_key,
        entitlement=best,
        reason=(
            f"You have used all {best.limit_value} of your "
            f"{catalogue.FEATURE_LABELS.get(feature, feature).lower()} this month."
        ),
    )


async def consume(
    session: AsyncSession,
    account_id: uuid.UUID,
    feature: str,
    *,
    quantity: int = 1,
    idempotency_key: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> EntitlementDecision:
    """Spend allowance, once.

    Returns the decision. When ``allowed`` is false nothing was spent, so a
    caller can refuse the request without having charged for it.

    The idempotency key is enforced by a unique constraint: a client retrying
    the same request finds its usage already recorded and is not charged again.
    """
    day = today or date.today()
    decision = await check(session, account_id, feature, today=day)
    if not decision.allowed:
        return decision

    if idempotency_key:
        existing = (await session.execute(
            select(EntitlementUsage).where(
                EntitlementUsage.account_id == account_id,
                EntitlementUsage.feature == feature,
                EntitlementUsage.idempotency_key == idempotency_key,
            )
        )).scalar_one_or_none()
        if existing is not None:
            # Already paid for. Allowed, and not charged twice.
            return decision

    row = decision.entitlement
    if row is not None and row.limit_value is not None:
        row.used += quantity

    session.add(EntitlementUsage(
        account_id=account_id, entitlement_id=row.id if row else None,
        feature=feature, quantity=quantity, period_key=period_key(day),
        idempotency_key=idempotency_key, detail=detail or {},
    ))
    await session.flush()

    decision.used = row.used if row is not None else decision.used + quantity
    return decision


async def refund_usage(
    session: AsyncSession,
    account_id: uuid.UUID,
    feature: str,
    *,
    quantity: int = 1,
    reason: str = "work_failed",
    today: Optional[date] = None,
) -> None:
    """Give back allowance the user did not get value for.

    Called when the work a consumption paid for then failed. A failed answer
    must not cost somebody one of their decisions.
    """
    day = today or date.today()
    rows = [row for row in await active_for(session, account_id, today=day) if row.feature == feature]
    row = next((candidate for candidate in rows if candidate.used > 0), None)
    if row is None:
        return
    row.used = max(0, row.used - quantity)
    session.add(EntitlementUsage(
        account_id=account_id, entitlement_id=row.id, feature=feature,
        quantity=-quantity, period_key=period_key(day),
        detail={"reason": reason},
    ))


# --- Presentation --------------------------------------------------------------------


async def settle_expired_subscriptions(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> int:
    """End subscriptions whose period (and grace) have run out, and revoke them.

    This lives here rather than in ``service.py`` so that **every** read path
    settles. It was originally only on the billing-status route, which meant
    ``GET /entitlements`` kept reporting Plus for an expired subscription — the
    app would have shown paid features to somebody whose plan had ended.
    """
    day = today or date.today()
    rows = (await session.execute(
        select(Subscription).where(
            Subscription.account_id == account_id,
            Subscription.status.in_(("active", "grace", "cancelled", "past_due")),
        )
    )).scalars().all()

    ended = 0
    for subscription in rows:
        deadline = subscription.grace_until or subscription.current_period_end
        if deadline >= day:
            continue
        subscription.status = "expired"
        subscription.ended_at = utcnow()
        await revoke_for_source(session, account_id, subscription.id, reason="expired")
        session.add(BillingAuditEvent(
            account_id=account_id, action="subscription_expired", actor="system",
            subject_type="subscription", subject_id=str(subscription.id),
            detail={"expired_on": deadline.isoformat()},
        ))
        ended += 1

    if ended:
        await session.flush()
    return ended


async def snapshot(
    session: AsyncSession, account_id: uuid.UUID, *, today: Optional[date] = None
) -> Dict[str, Any]:
    """Everything the client needs to render access state — including offline.

    ``cache_seconds`` and ``valid_until`` are the offline contract: the app may
    trust this snapshot for that long without a network, and must re-check
    afterwards. Bounded on purpose — a cached entitlement that never expires is
    a refunded subscription that keeps working.
    """
    day = today or date.today()
    await ensure_free(session, account_id, today=day)
    await settle_expired_subscriptions(session, account_id, today=day)
    await expire_due(session, account_id, today=day)
    rows = await active_for(session, account_id, today=day)

    by_feature: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        current = by_feature.get(row.feature)
        better = (
            current is None
            or row.limit_value is None
            or (current["limit"] is not None and (row.limit_value - row.used) > (current["limit"] - current["used"]))
        )
        if better:
            by_feature[row.feature] = {
                "feature": row.feature,
                "label": catalogue.FEATURE_LABELS.get(row.feature, row.feature),
                "limit": row.limit_value,
                "used": row.used,
                "remaining": None if row.limit_value is None else max(0, row.limit_value - row.used),
                "plan_key": row.plan_key,
                "expires_on": row.expires_on.isoformat() if row.expires_on else None,
            }

    subscription = (await session.execute(
        select(Subscription).where(
            Subscription.account_id == account_id,
            # "cancelled" belongs here. Cancelling stops the next renewal; it
            # does not take away the month somebody has already paid for, so
            # the app must still show them their subscription.
            Subscription.status.in_(("active", "grace", "past_due", "cancelled")),
        ).order_by(Subscription.current_period_end.desc())
    )).scalars().first()

    plan_keys = {row.plan_key for row in rows}
    effective = (
        catalogue.PLAN_PLUS if catalogue.PLAN_PLUS in plan_keys
        else catalogue.PLAN_EVENT_PASS if catalogue.PLAN_EVENT_PASS in plan_keys
        else catalogue.PLAN_FREE
    )

    return {
        "plan_key": effective,
        "plan_label": catalogue.PLAN_BY_KEY[effective].label,
        "features": sorted(by_feature.values(), key=lambda row: row["feature"]),
        "subscription": None if subscription is None else {
            "status": subscription.status,
            "plan_key": subscription.plan_key,
            "current_period_end": subscription.current_period_end.isoformat(),
            "grace_until": subscription.grace_until.isoformat() if subscription.grace_until else None,
            "cancel_at_period_end": subscription.cancel_at_period_end,
        },
        "as_of": day.isoformat(),
        "cache_seconds": ENTITLEMENT_CACHE_SECONDS,
        "valid_until": (utcnow() + timedelta(seconds=ENTITLEMENT_CACHE_SECONDS)).isoformat(),
        "offline_note": (
            "The app may rely on this for a short while without a connection. After that it "
            "checks again, so a cancelled or refunded plan cannot keep working indefinitely."
        ),
    }
