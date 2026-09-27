"""Beta access services: invite CRUD, atomic redemption, usage limiter.

Everything scoped to a Supabase Auth user UUID.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, text, update
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
    BetaUsageReservation,
    Invite,
    InviteRedemption,
    InviteRegistrationReservation,
)

# ---------------------------------------------------------------------------
# Feature name constants — used both by counted-write and by the summary API.
# ---------------------------------------------------------------------------
FEATURE_SCAN = "scan.analyse"
FEATURE_STYLE = "style.recommendations"
FEATURE_SHOPPING = "shopping.evaluate"
FEATURE_AI_REQUEST = "ai.request"


_MONTH_FEATURES: dict[str, int] = {
    FEATURE_SCAN: BETA_SCAN_LIMIT_PER_MONTH,
    FEATURE_STYLE: BETA_STYLE_LIMIT_PER_MONTH,
    FEATURE_SHOPPING: BETA_SHOPPING_CHECK_LIMIT_PER_MONTH,
}

_HOUR_FEATURES: dict[str, int] = {
    FEATURE_AI_REQUEST: BETA_AI_REQUESTS_PER_HOUR,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _month_key(dt: datetime | None = None) -> str:
    d = dt or _now()
    return f"{d.year:04d}-{d.month:02d}"


def _hour_key(dt: datetime | None = None) -> str:
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
    expires_at: datetime | None = None,
    created_by: uuid.UUID | None = None,
    code: str | None = None,
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


async def list_invites(session: AsyncSession) -> list[Invite]:
    result = await session.execute(
        select(Invite).order_by(Invite.created_at.desc())
    )
    return list(result.scalars().all())


async def deactivate_invite(session: AsyncSession, invite_id: uuid.UUID) -> Invite | None:
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
# Registration reservations (§1 — invite reservation before Supabase sign-up)
# ---------------------------------------------------------------------------

RESERVATION_TTL_SECONDS = 30 * 60  # 30 minutes — long enough for email confirm
RESERVATION_STATUS_ACTIVE = "active"
RESERVATION_STATUS_CONSUMED = "consumed"
RESERVATION_STATUS_EXPIRED = "expired"


class InviteReservationError(Exception):
    """Base class for invite reservation failures.

    Callers translate this to a 400 response with a stable ``code`` field so
    the mobile client can present accurate copy without probing back-end
    internals. Deliberately uniform for missing/expired/exhausted invites so
    unauthenticated reservation probing cannot enumerate valid codes.
    """

    default_code = "invite_invalid"

    def __init__(self, code: str | None = None, message: str = "") -> None:
        super().__init__(message or "The invite cannot be reserved.")
        self.code = code or self.default_code
        self.message = message or "The invite cannot be reserved."


class InviteNotReservable(InviteReservationError):
    """The invite is missing, inactive, expired, or fully redeemed."""

    default_code = "invite_invalid"


class ReservationChallengeInvalid(InviteReservationError):
    """The challenge/email pair does not match any live reservation."""

    default_code = "reservation_invalid"


class ReservationExpired(InviteReservationError):
    """The reservation was found but is past ``expires_at`` or already consumed."""

    default_code = "reservation_expired"


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_challenge(challenge: str) -> str:
    return hashlib.sha256(challenge.encode("utf-8")).hexdigest()


def _generate_challenge() -> str:
    """32-byte URL-safe secret. Never persisted — only its SHA-256 hash is."""
    return secrets.token_urlsafe(32)


async def reserve_invite(
    session: AsyncSession,
    *,
    code: str,
    email: str,
    now: datetime | None = None,
) -> tuple[InviteRegistrationReservation, str]:
    """Atomically reserve a spot for ``email`` against invite ``code``.

    Returns the ORM row **and** the raw challenge string. Only the hash is
    persisted; the challenge is handed to the caller and must be presented
    to ``POST /api/v2/access/register`` to finalise.

    Race safety: the check-and-reserve runs inside a transaction with
    ``SELECT ... FOR UPDATE`` on the invite row plus a bounded count of live
    reservations, so two concurrent reservers cannot exceed the invite cap.
    """
    normalised_code = (code or "").strip().upper()
    normalised_email = _normalise_email(email)
    if not normalised_code:
        raise InviteNotReservable(message="An invite code is required.")
    if "@" not in normalised_email:
        raise InviteReservationError(
            code="email_invalid", message="A valid email address is required."
        )

    ts = now or _now()

    invite = (
        await session.execute(
            select(Invite)
            .where(Invite.code == normalised_code)
            .with_for_update(skip_locked=False)
        )
    ).scalar_one_or_none()
    if invite is None:
        raise InviteNotReservable()
    if not invite.active:
        raise InviteNotReservable()
    if invite.expires_at is not None and invite.expires_at <= ts:
        raise InviteNotReservable()

    # An idempotent retry: if the same email already has an active,
    # unexpired reservation for the same invite, hand back a fresh
    # challenge that supersedes the previous one. The previous
    # challenge is invalidated because ``challenge_hash`` is unique.
    existing = (
        await session.execute(
            select(InviteRegistrationReservation)
            .where(
                and_(
                    InviteRegistrationReservation.invite_id == invite.id,
                    InviteRegistrationReservation.email_normalised == normalised_email,
                    InviteRegistrationReservation.status == RESERVATION_STATUS_ACTIVE,
                    InviteRegistrationReservation.expires_at > ts,
                )
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    live_count_stmt = select(func.count(InviteRegistrationReservation.id)).where(
        and_(
            InviteRegistrationReservation.invite_id == invite.id,
            InviteRegistrationReservation.status == RESERVATION_STATUS_ACTIVE,
            InviteRegistrationReservation.expires_at > ts,
        )
    )
    live_count = int(
        (await session.execute(live_count_stmt)).scalar_one() or 0
    )
    # If we are refreshing an existing reservation, it already occupies
    # a slot — do not double-count it.
    outstanding = live_count if existing is None else max(0, live_count - 1)
    if invite.uses_count + outstanding >= invite.max_uses:
        raise InviteNotReservable()

    challenge = _generate_challenge()
    challenge_hash = _hash_challenge(challenge)
    expires_at = ts + timedelta(seconds=RESERVATION_TTL_SECONDS)

    if existing is not None:
        existing.challenge_hash = challenge_hash
        existing.expires_at = expires_at
        reservation = existing
    else:
        reservation = InviteRegistrationReservation(
            invite_id=invite.id,
            email_normalised=normalised_email,
            challenge_hash=challenge_hash,
            status=RESERVATION_STATUS_ACTIVE,
            expires_at=expires_at,
        )
        session.add(reservation)
    await session.flush()
    return reservation, challenge


async def consume_reservation(
    session: AsyncSession,
    *,
    challenge: str,
    email: str,
    supabase_user_id: uuid.UUID,
    now: datetime | None = None,
) -> Invite:
    """Atomically consume a reservation and redeem the underlying invite.

    Called from ``POST /api/v2/access/register`` inside the same
    transaction that creates the ``accounts`` row. Behaviour:

    * The challenge/email pair must match a live reservation.
    * The reservation must not be expired, consumed, or bound to another
      Supabase user.
    * A conditional UPDATE flips ``status`` from ``active`` to ``consumed``
      only if the row is still active — a second finalisation on the same
      challenge fails without redeeming a second invite slot.
    * Once the reservation is consumed the invite ``uses_count`` is
      incremented; if the invite is full the whole finalisation is rolled
      back by the caller.
    """
    normalised_email = _normalise_email(email)
    if not challenge:
        raise ReservationChallengeInvalid()
    if not normalised_email:
        raise ReservationChallengeInvalid()

    ts = now or _now()
    challenge_hash = _hash_challenge(challenge)

    reservation = (
        await session.execute(
            select(InviteRegistrationReservation)
            .where(InviteRegistrationReservation.challenge_hash == challenge_hash)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise ReservationChallengeInvalid()
    if reservation.email_normalised != normalised_email:
        raise ReservationChallengeInvalid()
    if reservation.status != RESERVATION_STATUS_ACTIVE:
        raise ReservationExpired()
    if reservation.expires_at <= ts:
        reservation.status = RESERVATION_STATUS_EXPIRED
        await session.flush()
        raise ReservationExpired()

    # Atomic consume: single row UPDATE conditioned on the current active
    # state, so two racing finalisations cannot both succeed.
    result = await session.execute(
        update(InviteRegistrationReservation)
        .where(
            and_(
                InviteRegistrationReservation.id == reservation.id,
                InviteRegistrationReservation.status == RESERVATION_STATUS_ACTIVE,
            )
        )
        .values(
            status=RESERVATION_STATUS_CONSUMED,
            consumed_at=ts,
            supabase_user_id=supabase_user_id,
        )
        .returning(InviteRegistrationReservation.id)
    )
    if result.first() is None:
        raise ReservationExpired()

    invite = await session.get(Invite, reservation.invite_id)
    if invite is None:
        raise InviteNotReservable()

    bump = await session.execute(
        update(Invite)
        .where(
            and_(
                Invite.id == invite.id,
                Invite.active.is_(True),
                (Invite.expires_at.is_(None)) | (Invite.expires_at > ts),
                Invite.uses_count < Invite.max_uses,
            )
        )
        .values(uses_count=Invite.uses_count + 1)
        .returning(Invite.id)
    )
    if bump.first() is None:
        raise InviteNotReservable()

    session.add(
        InviteRedemption(invite_id=invite.id, account_id=supabase_user_id)
    )
    await session.flush()
    await session.refresh(invite)
    return invite


async def expire_stale_reservations(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Mark active reservations whose ``expires_at`` has passed as expired.

    Called from a scheduled job. Returns the number of rows updated.
    """
    ts = now or _now()
    result = await session.execute(
        update(InviteRegistrationReservation)
        .where(
            and_(
                InviteRegistrationReservation.status == RESERVATION_STATUS_ACTIVE,
                InviteRegistrationReservation.expires_at <= ts,
            )
        )
        .values(status=RESERVATION_STATUS_EXPIRED)
        .returning(InviteRegistrationReservation.id)
    )
    return len(result.all())


# ---------------------------------------------------------------------------
# Beta usage limiter — non-financial
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


def _tracked(feature: str, now: datetime) -> tuple[int, str, str] | None:
    """``(limit, period, period_key)`` for a tracked feature, else ``None``."""
    if feature in _MONTH_FEATURES:
        return _MONTH_FEATURES[feature], "month", _month_key(now)
    if feature in _HOUR_FEATURES:
        return _HOUR_FEATURES[feature], "hour", _hour_key(now)
    return None


async def _held_quantity(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
    period_key: str,
    now: datetime,
) -> int:
    """Units held by live reservations. An expired one holds nothing."""
    row = await session.execute(
        select(func.coalesce(func.sum(BetaUsageReservation.quantity), 0)).where(
            BetaUsageReservation.account_id == account_id,
            BetaUsageReservation.feature == feature,
            BetaUsageReservation.period_key == period_key,
            BetaUsageReservation.expires_at > now,
        )
    )
    return int(row.scalar_one() or 0)


async def check_limit(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
) -> dict[str, Any]:
    """Raise ``UsageExceeded`` if the account is at or above the limit for
    ``feature``. Returns the summary otherwise.

    A read, not an authority: two callers can both pass it. Cost-bearing work
    takes a unit with :func:`reserve_usage` instead. Live reservations count
    here too, so this and a reservation never disagree about what is left.
    """
    now = _now()
    tracked = _tracked(feature, now)
    if tracked is None:
        # An untracked feature has no limit. Log-shape parity with tracked
        # features so the caller can render the same UI.
        return {"feature": feature, "used": 0, "limit": None, "period_key": None}
    limit, period, period_key = tracked

    used = await _current_usage(
        session, account_id=account_id, feature=feature, period_key=period_key
    ) + await _held_quantity(
        session, account_id=account_id, feature=feature, period_key=period_key, now=now,
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


# ---------------------------------------------------------------------------
# Reservations: take the unit before spending it
# ---------------------------------------------------------------------------

#: How long an unsettled reservation keeps its unit — the case where a process
#: died between reserving and settling. Long enough for the slowest normal
#: request (the provider timeout, ``AI_TIMEOUT_SECONDS``, plus the database
#: work either side); short enough that a crash costs one unit for minutes,
#: never for the rest of the hour or month. Nothing needs to sweep: every
#: count ignores an expired reservation.
RESERVATION_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class UsageReservation:
    """One held unit of allowance: plain values, safe past the session that took it."""

    id: uuid.UUID
    account_id: uuid.UUID
    feature: str
    period_key: str
    quantity: int


async def reserve_usage(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
    quantity: int = 1,
    now: datetime | None = None,
) -> UsageReservation | None:
    """Take ``quantity`` units of ``feature`` now, or raise ``UsageExceeded``.

    The check and the take are one step. A transaction-scoped advisory lock
    keyed by account, feature and period serialises every reservation for that
    budget, and under it successful usage plus live reservations plus this one
    must fit the limit — so of any number of simultaneous requests for the last
    unit, exactly one gets it. Returns ``None`` for an untracked feature.

    **The caller commits before spending.** The lock ends with that commit, and
    the unit stays held by the row, not by a transaction: nothing is locked or
    left open while a provider is called. A database failure raises here, so
    no provider is ever called without a reservation — the budget fails closed.
    """
    now = now or _now()
    tracked = _tracked(feature, now)
    if tracked is None:
        return None
    limit, period, period_key = tracked

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"beta-usage:{account_id}:{feature}:{period_key}"},
    )
    used = await _current_usage(
        session, account_id=account_id, feature=feature, period_key=period_key
    )
    held = await _held_quantity(
        session, account_id=account_id, feature=feature, period_key=period_key, now=now,
    )
    if used + held + quantity > limit:
        raise UsageExceeded(feature=feature, limit=limit, period=period)

    # Opportunistic tidy-up, under the same lock: this budget's expired holds.
    await session.execute(
        delete(BetaUsageReservation).where(
            BetaUsageReservation.account_id == account_id,
            BetaUsageReservation.feature == feature,
            BetaUsageReservation.expires_at <= now,
        )
    )
    row = BetaUsageReservation(
        account_id=account_id, feature=feature, period_key=period_key,
        quantity=quantity, created_at=now, expires_at=now + RESERVATION_TTL,
    )
    session.add(row)
    await session.flush()
    return UsageReservation(
        id=row.id, account_id=account_id, feature=feature,
        period_key=period_key, quantity=quantity,
    )


async def settle_usage(
    session: AsyncSession,
    reservation: UsageReservation,
    *,
    idempotency_key: str | None = None,
) -> None:
    """The work succeeded: count it, and let go of the hold, in one transaction.

    The event is written to the period the unit was reserved from, and keeps
    ``record_usage``'s idempotency: a key already counted is not counted twice,
    and the reservation is released either way. The caller commits.
    """
    stmt = pg_insert(BetaUsageEvent).values(
        account_id=reservation.account_id,
        feature=reservation.feature,
        idempotency_key=idempotency_key,
        period_key=reservation.period_key,
        quantity=reservation.quantity,
        created_at=_now(),
    )
    if idempotency_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["account_id", "feature", "idempotency_key"]
        )
    await session.execute(stmt)
    await session.execute(
        delete(BetaUsageReservation).where(BetaUsageReservation.id == reservation.id)
    )


async def release_usage(session: AsyncSession, reservation: UsageReservation) -> None:
    """The work failed without a result: give the unit back. The caller commits.

    A failed run never consumes an allowance, and nothing is counted here. A
    release that itself fails leaves the reservation to expire.
    """
    await session.execute(
        delete(BetaUsageReservation).where(BetaUsageReservation.id == reservation.id)
    )


async def record_usage(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    feature: str,
    idempotency_key: str | None = None,
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
) -> dict[str, Any]:
    """Public shape read by ``GET /api/v2/access/usage``. Neutral language,
    no plans, no upgrade CTAs."""
    out: dict[str, Any] = {"month": _month_key(), "hour": _hour_key(), "features": []}
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


def serialise_invite(invite: Invite) -> dict[str, Any]:
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
