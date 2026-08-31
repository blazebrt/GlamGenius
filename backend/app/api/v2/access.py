"""V2 access routes: invite reservation, registration finalisation, admin.

The registration flow is a **two-step** protocol (§1 of the Supabase
hardening spec):

1. ``POST /api/v2/access/reserve`` (unauthenticated) — validate the invite
   BEFORE the Supabase Auth sign-up. Reserves a short-lived slot bound to
   the caller's email and returns a single-use registration challenge.
2. The client performs the Supabase Auth sign-up (client-side, with
   Supabase JS). Supabase may require email confirmation.
3. ``POST /api/v2/access/register`` (authenticated with the new Supabase
   token) — presents the challenge, atomically consumes the reservation,
   redeems the invite, and creates the ``accounts`` row.

This eliminates the previous invite bypass where a rejected invite would
leave an orphan Supabase identity: the identity is only ever created
after step 1 has already accepted the invite.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INVITE_REQUIRED
from app.domains.beta_access import service as beta
from app.domains.beta_access.entitlements import beta_entitlement_matrix
from app.domains.beta_access.models import Invite, InviteRedemption
from app.domains.identity import service as identity
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account
from app.shared.security.supabase_auth import (
    SupabaseUser,
    client_ip,
    get_current_admin,
    get_current_supabase_user,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Simple in-process rate limiter for unauthenticated ``/access/reserve``.
#
# A real deployment should sit behind a proper edge rate limiter, but we
# guarantee a floor here so a leaked endpoint cannot be enumerated at
# millions of RPS from one host.
# ---------------------------------------------------------------------------
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_MAX_PER_WINDOW = 10  # per IP + per email
_rate_state: dict[str, deque[float]] = defaultdict(deque)


def _hit_rate_limit(key: str) -> bool:
    now = time.monotonic()
    bucket = _rate_state[key]
    while bucket and now - bucket[0] > _RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX_PER_WINDOW:
        return True
    bucket.append(now)
    return False


def _reservation_error(exc: beta.InviteReservationError) -> HTTPException:
    """Map service errors to a uniform 400 body — no code enumeration.

    Any invite failure surfaces as ``invite_invalid`` unless the caller
    already has a live reservation (``reservation_expired`` is legitimate
    for the mobile app to distinguish so it can prompt for a fresh
    reservation). We deliberately do NOT reveal whether an email is
    already registered.
    """
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": exc.code, "message": exc.message, "retryable": False},
    )


# ---------------------------------------------------------------------------
# Step 1: Reserve
# ---------------------------------------------------------------------------

class ReserveRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=64)
    email: EmailStr


class ReserveResponse(BaseModel):
    challenge: str
    reservation_id: str
    expires_at: datetime


@router.post("/access/reserve", response_model=ReserveResponse)
async def reserve(
    body: ReserveRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ReserveResponse:
    """Reserve an invite slot BEFORE the Supabase Auth sign-up."""
    if not INVITE_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
        )

    caller_ip = client_ip(request) or "unknown"
    email_key = body.email.lower()
    for key in (f"ip:{caller_ip}", f"email:{email_key}"):
        if _hit_rate_limit(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "rate_limited",
                    "message": (
                        "Too many reservation attempts. Please wait a "
                        "moment and try again."
                    ),
                    "retryable": True,
                },
            )

    try:
        reservation, challenge = await beta.reserve_invite(
            session, code=body.invite_code, email=body.email
        )
    except beta.InviteReservationError as exc:
        # Do not commit anything; the transaction is safe to leave to
        # FastAPI dependency teardown.
        raise _reservation_error(exc) from exc

    await session.commit()
    return ReserveResponse(
        challenge=challenge,
        reservation_id=str(reservation.id),
        expires_at=reservation.expires_at,
    )


# ---------------------------------------------------------------------------
# Step 3: Register (finalise)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """The finalisation payload.

    ``registration_challenge`` is the token returned by ``/access/reserve``.
    When ``INVITE_REQUIRED=false`` (dev/tests only), the challenge is
    optional and finalisation degrades to creating the ``accounts`` row for
    the caller's Supabase UUID.
    """

    registration_challenge: str | None = Field(default=None, max_length=256)


@router.post("/access/register")
async def register(
    body: RegisterRequest,
    supabase_user: SupabaseUser = Depends(get_current_supabase_user),
    session: AsyncSession = Depends(get_session),
):
    """Finalise an invite-gated account for a Supabase-authenticated user.

    Idempotent: repeated calls after a successful finalisation return the
    existing account snapshot and do not touch the invite or reservation.
    """
    account_id = supabase_user.id

    existing = await identity.get_account(session, account_id)
    if existing is not None:
        return {
            "account": {
                "id": str(existing.id),
                "status": existing.status,
                "created_at": (
                    existing.created_at.isoformat() if existing.created_at else None
                ),
            },
            "invite_redeemed": False,
        }

    if INVITE_REQUIRED:
        challenge = (body.registration_challenge or "").strip()
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "registration_challenge_required",
                    "message": (
                        "Registration requires a reservation. Please "
                        "reserve your invite first."
                    ),
                    "retryable": False,
                },
            )
        if not supabase_user.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "email_required",
                    "message": (
                        "This Supabase session does not carry a verified "
                        "email. Please confirm your email and try again."
                    ),
                    "retryable": False,
                },
            )
        try:
            # Fix: create the account row before consuming the
            # reservation so the ``invite_redemptions.account_id`` FK to
            # ``accounts.id`` is satisfied at insert time. Both operations
            # run in the same transaction — a subsequent failure rolls the
            # accounts row back too.
            account = await identity.register_account(session, account_id)
            invite = await beta.consume_reservation(
                session,
                challenge=challenge,
                email=supabase_user.email,
                supabase_user_id=account_id,
            )
        except beta.InviteReservationError as exc:
            await session.rollback()
            raise _reservation_error(exc) from exc
    else:
        invite = None
        account = await identity.register_account(session, account_id)

    await session.commit()

    return {
        "account": {
            "id": str(account.id),
            "status": account.status,
            "created_at": (
                account.created_at.isoformat() if account.created_at else None
            ),
        },
        "invite_redeemed": invite is not None,
    }


# ---------------------------------------------------------------------------
# Beta usage summary
# ---------------------------------------------------------------------------

@router.get("/access/usage")
async def get_usage(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Neutral beta usage summary. No plans, no upgrade CTA, no pricing."""
    return await beta.usage_summary(session, account_id=current.account_id)


@router.get("/access/beta-entitlements")
async def get_beta_entitlements(
    current: CurrentAccount = Depends(get_current_account),
) -> dict[str, object]:
    """Current beta access, deliberately separate from usage cost controls."""
    del current
    return beta_entitlement_matrix()


# ---------------------------------------------------------------------------
# Admin invite management
# ---------------------------------------------------------------------------

class InviteCreateRequest(BaseModel):
    label: str = ""
    max_uses: int = 1
    expires_at: datetime | None = None
    code: str | None = None


@router.post("/access/admin/invites")
async def admin_create_invite(
    body: InviteCreateRequest,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await beta.create_invite(
        session,
        label=body.label,
        max_uses=body.max_uses,
        expires_at=body.expires_at,
        code=body.code,
        created_by=admin.id,
    )
    await session.commit()
    return beta.serialise_invite(invite)


@router.get("/access/admin/invites")
async def admin_list_invites(
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invites = await beta.list_invites(session)
    return {"invites": [beta.serialise_invite(inv) for inv in invites]}


@router.post("/access/admin/invites/{invite_id}/deactivate")
async def admin_deactivate_invite(
    invite_id: uuid.UUID,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await beta.deactivate_invite(session, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    return beta.serialise_invite(invite)


@router.get("/access/admin/invites/{invite_id}")
async def admin_get_invite(
    invite_id: uuid.UUID,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    invite = await session.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    redemptions = (
        await session.execute(
            select(InviteRedemption)
            .where(InviteRedemption.invite_id == invite_id)
            .order_by(InviteRedemption.created_at.desc())
        )
    ).scalars().all()
    payload = beta.serialise_invite(invite)
    payload["redemptions"] = [
        {
            "account_id": str(r.account_id),
            "redeemed_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in redemptions
    ]
    return payload


# ---------------------------------------------------------------------------
# Reservation metrics (admin) — visibility into private-beta throughput
# ---------------------------------------------------------------------------

@router.get("/access/admin/reservations/stats")
async def admin_reservation_stats(
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    """Live vs consumed vs expired reservation counts, plus the top invites
    with active reservations right now.

    ``active`` counts reservations whose ``expires_at`` has not passed and
    whose ``status`` is still ``active`` — the row hasn't been swept yet by
    the housekeeping job.

    ``expired`` covers both explicitly-swept ``expired`` rows **and** rows
    that are still nominally ``active`` but whose ``expires_at`` has
    elapsed — the two are semantically the same from an operator's view
    and merging them avoids under-reporting between housekeeping runs.
    """
    from app.domains.beta_access.models import InviteRegistrationReservation
    from app.domains.beta_access.service import (
        RESERVATION_STATUS_ACTIVE,
        RESERVATION_STATUS_CONSUMED,
        RESERVATION_STATUS_EXPIRED,
        _now,
    )

    now = _now()

    counts_row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(
                    case(
                        (
                            (
                                InviteRegistrationReservation.status
                                == RESERVATION_STATUS_ACTIVE
                            )
                            & (InviteRegistrationReservation.expires_at > now),
                            1,
                        ),
                        else_=0,
                    )
                ).label("active"),
                func.sum(
                    case(
                        (
                            InviteRegistrationReservation.status
                            == RESERVATION_STATUS_CONSUMED,
                            1,
                        ),
                        else_=0,
                    )
                ).label("consumed"),
                func.sum(
                    case(
                        (
                            (
                                InviteRegistrationReservation.status
                                == RESERVATION_STATUS_EXPIRED
                            )
                            | (
                                (
                                    InviteRegistrationReservation.status
                                    == RESERVATION_STATUS_ACTIVE
                                )
                                & (InviteRegistrationReservation.expires_at <= now)
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("expired"),
            )
        )
    ).one()

    total = int(counts_row.total or 0)
    active = int(counts_row.active or 0)
    consumed = int(counts_row.consumed or 0)
    expired = int(counts_row.expired or 0)

    # Top invites currently holding live reservations.
    top_stmt = (
        select(
            Invite.id,
            Invite.code,
            Invite.label,
            Invite.max_uses,
            Invite.uses_count,
            func.count(InviteRegistrationReservation.id).label("live"),
        )
        .join(
            InviteRegistrationReservation,
            InviteRegistrationReservation.invite_id == Invite.id,
        )
        .where(
            InviteRegistrationReservation.status == RESERVATION_STATUS_ACTIVE,
            InviteRegistrationReservation.expires_at > now,
        )
        .group_by(Invite.id)
        .order_by(func.count(InviteRegistrationReservation.id).desc())
        .limit(10)
    )
    top_rows = (await session.execute(top_stmt)).all()

    return {
        "totals": {
            "total": total,
            "active": active,
            "consumed": consumed,
            "expired": expired,
        },
        "active_by_invite": [
            {
                "invite_id": str(r.id),
                "code": r.code,
                "label": r.label,
                "max_uses": r.max_uses,
                "uses_count": r.uses_count,
                "live_reservations": int(r.live),
            }
            for r in top_rows
        ],
        "generated_at": now.isoformat(),
    }

@router.get('/access/admin/queue/metrics')
async def admin_queue_metrics(
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import func, select

    from app.domains.privacy.models import AccountDeletionJob

    stmt = select(AccountDeletionJob.state, func.count(AccountDeletionJob.id)).group_by(AccountDeletionJob.state)
    result = await session.execute(stmt)
    state_counts = dict(result.all())

    return {
        'queues': {
            'account_deletion': state_counts
        }
    }

