"""Beta access services: invite CRUD, atomic redemption, usage limiter.

Everything scoped to a Supabase Auth user UUID.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    BETA_AI_REQUESTS_PER_HOUR,
    BETA_SCAN_LIMIT_PER_MONTH,
    BETA_SHOPPING_CHECK_LIMIT_PER_MONTH,
    BETA_STYLE_LIMIT_PER_MONTH,
)
from app.domains.beta_access.models import (
    BetaUsageEvent,
    Invite,
    InviteRedemption,
)


# ---------------------------------------------------------------------------
# Feature name constants — used both by counted-write and by the summary API.
# ---------------------------------------------------------------------------
FEATURE_SCAN = "scan.analyse"
FEATURE_STYLE = "style.recommendations"
FEATURE_SHOPPING = "shopping.evaluate"
FEATURE_AI_REQUEST = "ai.request"


_MONTH_FEATURES: Dict[str, int] = {
    FEATURE_SCAN: BETA_SCAN_LIMIT_PER_MONTH,
    FEATURE_STYLE: BETA_STYLE_LIMIT_PER_MONTH,
    FEATURE_SHOPPING: BETA_SHOPPING_CHECK_LIMIT_PER_MONTH,
}

_HOUR_FEATURES: Dict[str, int] = {
    FEATURE_AI_REQUEST: BETA_AI_REQUESTS_PER_HOUR,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _month_key(dt: Optional[datetime] = None) -> str:
    d = dt or _now()
    return f"{d.year:04d}-{d.month:02d}"


def _hour_key(dt: Optional[datetime] = None) -> str:
    d = dt or _now()
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d} {d.hour:02d}"


# ---------------------------------------------------------------------------
# Invite management
# ---------------------------------------------------------------------------

def _generate_code(length: int = 10) -> str:
    """URL-safe random invite code, uppercase A-Z + digits, no lookalikes."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def create_invite(
    session: AsyncSession,
    *,
    label: str = "",
    max_uses: int = 1,
    expires_at: Optional[datetime] = None,
    created_by: Optional[uuid.UUID] = None,
    code: Optional[str] = None,
) -> Invite:
    if max_uses < 1:
        raise ValueError("max_uses must be at least 1")

    resolved_code = (code or _generate_code()).strip().upper()
    invite = Invite(
        code=resolved_code,
        label=label.strip()[:120],
        max_uses=max_uses,
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(invite)
    await session.flush()
    return invite


async def list_invites(session: AsyncSession) -> List[Invite]:
    result = await session.execute(
        select(Invite).order_by(Invite.created_at.desc())
    )
    return list(result.scalars().all())


async def deactivate_invite(session: AsyncSession, invite_id: uuid.UUID) -> Optional[Invite]:
    invite = await session.get(Invite, invite_id)
    if invite is None:
        return None
    invite.active = False
    await session.flush()
    return invite


async def redeem_invite(
    session: AsyncSession,
    *,
    code: str,
    account_id: uuid.UUID,
) -> Invite:
    """Atomically redeem an invite for the given account.

    The atomic step is the conditional UPDATE: it succeeds only if the invite
    is active, unexpired, and has spare uses. If the UPDATE affects zero rows
    the code is rejected — no race window.
    """
    normalised = (code or "").strip().upper()
    if not normalised:
        raise ValueError("invite code is required")

    invite = (
        await session.execute(
            select(Invite).where(Invite.code == normalised).with_for_update(skip_locked=False)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise ValueError("invite not found")

    now = _now()
    result = await session.execute(
        update(Invite)
        .where(
            and_(
                Invite.id == invite.id,
                Invite.active.is_(True),
                (Invite.expires_at.is_(None)) | (Invite.expires_at > now),
                Invite.uses_count < Invite.max_uses,
            )
        )
        .values(uses_count=Invite.uses_count + 1)
        .returning(Invite.id)
    )
    if result.first() is None:
        raise ValueError("invite is not usable")

    try:
        session.add(InviteRedemption(invite_id=invite.id, account_id=account_id))
        await session.flush()
    except IntegrityError:
        # Same account trying to redeem the same invite twice. Roll the count
        # back and refuse.
        await session.rollback()
        raise ValueError("invite already redeemed by this account")

    await session.refresh(invite)
    return invite


# ---------------------------------------------------------------------------
# Beta usage limiter — non-billing
# ---------------------------------------------------------------------------

class UsageExceeded(Exception):
    """Raised when a beta cost/abuse cap has been reached."""

    def __init__(self, feature: str, limit: int, period: str) -> None:
        super().__init__(f"beta limit reached: {feature}")
        self.feature = feature
        self.limit = limit
        self.period = period


async def _current_usage(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
    period_key: str,
) -> int:
    row = await session.execute(
        select(func.coalesce(func.sum(BetaUsageEvent.quantity), 0)).where(
            BetaUsageEvent.account_id == account_id,
            BetaUsageEvent.feature == feature,
            BetaUsageEvent.period_key == period_key,
        )
    )
    return int(row.scalar_one() or 0)


async def check_limit(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
) -> Dict[str, Any]:
    """Raise ``UsageExceeded`` if the account is at or above the limit for
    ``feature``. Returns the summary otherwise.
    """
    if feature in _MONTH_FEATURES:
        limit = _MONTH_FEATURES[feature]
        period_key = _month_key()
        period = "month"
    elif feature in _HOUR_FEATURES:
        limit = _HOUR_FEATURES[feature]
        period_key = _hour_key()
        period = "hour"
    else:
        # An untracked feature has no limit. Log-shape parity with tracked
        # features so the caller can render the same UI.
        return {"feature": feature, "used": 0, "limit": None, "period_key": None}

    used = await _current_usage(
        session, account_id=account_id, feature=feature, period_key=period_key
    )
    if used >= limit:
        raise UsageExceeded(feature=feature, limit=limit, period=period)

    return {
        "feature": feature,
        "used": used,
        "limit": limit,
        "period_key": period_key,
        "remaining": max(0, limit - used),
    }


async def record_usage(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
    idempotency_key: Optional[str] = None,
    quantity: int = 1,
) -> None:
    """Append a usage event. Idempotent by ``(account, feature, key)``.

    **Called only after the underlying work has succeeded.** Failed AI runs
    must not consume the beta allowance.
    """
    now = _now()
    if feature in _HOUR_FEATURES:
        period_key = _hour_key(now)
    else:
        period_key = _month_key(now)

    stmt = pg_insert(BetaUsageEvent).values(
        account_id=account_id,
        feature=feature,
        idempotency_key=idempotency_key,
        period_key=period_key,
        quantity=quantity,
        created_at=now,
    )
    if idempotency_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["account_id", "feature", "idempotency_key"]
        )
    await session.execute(stmt)


async def usage_summary(
    session: AsyncSession, *, account_id: uuid.UUID
) -> Dict[str, Any]:
    """Public shape read by ``GET /api/v2/access/usage``. Neutral language,
    no plans, no upgrade CTAs."""
    out: Dict[str, Any] = {"month": _month_key(), "hour": _hour_key(), "features": []}
    for feature, limit in _MONTH_FEATURES.items():
        used = await _current_usage(
            session,
            account_id=account_id,
            feature=feature,
            period_key=_month_key(),
        )
        out["features"].append(
            {
                "feature": feature,
                "period": "month",
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
            }
        )
    for feature, limit in _HOUR_FEATURES.items():
        used = await _current_usage(
            session,
            account_id=account_id,
            feature=feature,
            period_key=_hour_key(),
        )
        out["features"].append(
            {
                "feature": feature,
                "period": "hour",
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
            }
        )
    return out


def serialise_invite(invite: Invite) -> Dict[str, Any]:
    return {
        "id": str(invite.id),
        "code": invite.code,
        "label": invite.label,
        "max_uses": invite.max_uses,
        "uses_count": invite.uses_count,
        "active": invite.active,
        "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
        "created_at": invite.created_at.isoformat() if invite.created_at else None,
    }
