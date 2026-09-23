"""Consumer referral, v1: give somebody access to something you found useful.

A referral code **is** an :class:`~app.domains.beta_access.models.Invite`. It
is created through the same ``beta_access.create_invite`` an admin's invite is,
it is reserved through ``POST /api/v2/access/reserve``, the new identity is
created by Supabase Auth, and the account row is finalised by
``POST /api/v2/access/register`` — the same three steps, the same uniform
refusal, the same rate limits, reservation capacity, email and challenge
binding, one-time consumption and atomic use count. Nothing in this module
touches any of that, and nothing anywhere asks whether an invite is a referral
before deciding how securely to treat it.

What this module adds is *who may be handed one*, and how many people it can
ever admit:

* **Eligibility.** A registered, active account with at least one useful scan
  (:mod:`app.domains.growth.activation`). Signing up is not enough.
* **Lifetime ceiling.** Three successful admissions per account, ever. The
  count is the sum of ``uses_count`` over every invite bound to the account —
  the invite's own monotonic counter, so an invitee who later deletes their
  account does not hand the inviter a fourth place.
* **One usable code at a time.** A new invite is issued only when no bound
  invite is still usable, and it carries ``max_uses`` = the capacity that
  remains. An expired, partly used code may be replaced; the replacement can
  only ever admit what is left.
* **No reward.** Nothing is paid, credited, counted publicly or ranked.

Lock order
----------
::

    Account FOR NO KEY UPDATE -> bound Invite rows FOR UPDATE -> insert

The account row is the serialisation point: two simultaneous requests to
ensure a code queue on it, and the second one reads the invite the first one
committed instead of minting a second live code. ``FOR NO KEY UPDATE`` rather
than ``FOR UPDATE`` because it still conflicts with itself and with ``DELETE``,
but not with the ``FOR KEY SHARE`` every child-row insert on this account takes,
so unrelated writes are not queued behind a referral.

Account deletion takes the same account row first and the invites second
(:func:`deactivate_referral_invites_for_account`), so the two can never hold
the pair in opposite orders. Whichever commits first wins cleanly: a code
issued first is switched off by the deletion that follows it; a deletion that
started first leaves an account this module refuses to issue for.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite
from app.domains.growth.activation import has_useful_scan
from app.domains.growth.models import PROGRAM_VERSION, ConsumerReferralInvite
from app.domains.identity.models import ACCOUNT_STATUS_ACTIVE, Account
from app.shared.database.base import utcnow

#: Successful admissions one account can ever produce under this program.
LIFETIME_SUCCESSFUL_REFERRALS = 3
#: How long one referral code stays usable.
INVITE_LIFETIME = timedelta(days=30)
#: Attempts at an unused random code before giving up. The code space is
#: 32**10; a collision is not expected, and a loop that could spin is not kept.
_CODE_ATTEMPTS = 5

# --- The states a caller can be told -----------------------------------------
#: No useful scan yet. Signing up does not earn a code.
STATE_NOT_ACTIVATED = "not_activated"
#: Eligible, with capacity, and no code issued yet. Read-only answer; ensuring
#: moves it to ``available``.
STATE_READY = "ready"
#: A usable code exists and is returned.
STATE_AVAILABLE = "available"
#: Three people have been admitted. There will never be another code.
STATE_EXHAUSTED = "exhausted"
#: An operator switched the live code off before it expired. It is not
#: replaced until the date it would have expired anyway, so withdrawing a code
#: cannot be undone by asking for another one.
STATE_WITHDRAWN = "withdrawn"
#: The account is being deleted or is gone, or the beta is not invite-gated
#: (``INVITE_REQUIRED`` off, so ``/access/reserve`` does not exist and a code
#: would be a capability for nothing). Nothing is issued.
STATE_UNAVAILABLE = "unavailable"

STATES: tuple[str, ...] = (
    STATE_NOT_ACTIVATED, STATE_READY, STATE_AVAILABLE,
    STATE_EXHAUSTED, STATE_WITHDRAWN, STATE_UNAVAILABLE,
)


class ReferralIssueFailed(Exception):
    """A code could not be minted. A controlled failure, never a partial one."""


@dataclass(frozen=True)
class ReferralState:
    state: str
    code: str | None = None
    expires_at: datetime | None = None
    remaining_uses: int | None = None

    def as_payload(self) -> dict[str, Any]:
        """What the inviter is told. Never an invite id, a redemption, a
        reservation, an email or a challenge.

        The code sits inside ``referral`` rather than at the top level so that
        observability scrubbing, which redacts the ``referral`` container by
        name, covers it wherever the payload travels.
        """
        referral = None
        if self.state == STATE_AVAILABLE and self.code is not None:
            referral = {
                "code": self.code,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "remaining_uses": self.remaining_uses,
            }
        return {"program_version": PROGRAM_VERSION, "state": self.state, "referral": referral}


def _live(invite: Invite, now: datetime) -> bool:
    """Not yet expired and not yet full. Says nothing about ``active``."""
    return (invite.expires_at is None or invite.expires_at > now) and invite.uses_count < invite.max_uses


def _usable(invite: Invite, now: datetime) -> bool:
    return bool(invite.active) and _live(invite, now)


def _withdrawn(invite: Invite, now: datetime) -> bool:
    """Switched off while it still had time and room left.

    This module only ever switches off invites that are already expired or
    full, so an invite that is off while still live was switched off by
    someone else — an operator, or account deletion.
    """
    return not invite.active and _live(invite, now)


def lifetime_admissions(invites: list[Invite]) -> int:
    """Successful admissions across every code this account was ever issued."""
    return sum(int(invite.uses_count or 0) for invite in invites)


def _available(invite: Invite, used: int) -> ReferralState:
    remaining = min(
        invite.max_uses - invite.uses_count,
        LIFETIME_SUCCESSFUL_REFERRALS - used,
    )
    return ReferralState(
        state=STATE_AVAILABLE, code=invite.code,
        expires_at=invite.expires_at, remaining_uses=max(0, remaining),
    )


def _decide(invites: list[Invite], now: datetime) -> ReferralState | None:
    """The answer the bound invites already determine, or ``None`` for "issue".

    Shared by the read and the write so the two cannot disagree about what a
    set of invites means.
    """
    used = lifetime_admissions(invites)
    if used >= LIFETIME_SUCCESSFUL_REFERRALS:
        return ReferralState(state=STATE_EXHAUSTED)
    usable = [invite for invite in invites if _usable(invite, now)]
    if usable:
        # One at a time is enforced at issue; the oldest usable one is the
        # authority should that ever have been broken.
        return _available(usable[0], used)
    if any(_withdrawn(invite, now) for invite in invites):
        return ReferralState(state=STATE_WITHDRAWN)
    return None


async def _bound_invites(
    session: AsyncSession, account_id: uuid.UUID, *, lock: bool,
) -> list[Invite]:
    stmt = (
        select(Invite)
        .join(ConsumerReferralInvite, ConsumerReferralInvite.invite_id == Invite.id)
        .where(ConsumerReferralInvite.inviter_account_id == account_id)
        .order_by(ConsumerReferralInvite.created_at, Invite.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=Invite)
    return list((await session.execute(stmt)).scalars().all())


async def read_referral(
    session: AsyncSession, *, account_id: uuid.UUID, now: datetime | None = None,
) -> ReferralState:
    """What this account could share right now. Reads only; takes no lock."""
    ts = now or utcnow()
    if not config.INVITE_REQUIRED:
        return ReferralState(state=STATE_UNAVAILABLE)
    status = await session.scalar(select(Account.status).where(Account.id == account_id))
    if status != ACCOUNT_STATUS_ACTIVE:
        return ReferralState(state=STATE_UNAVAILABLE)
    if not await has_useful_scan(session, account_id):
        return ReferralState(state=STATE_NOT_ACTIVATED)
    decided = _decide(await _bound_invites(session, account_id, lock=False), ts)
    return decided if decided is not None else ReferralState(state=STATE_READY)


async def _issue_invite(
    session: AsyncSession, *, max_uses: int, expires_at: datetime,
) -> Invite:
    """One new invite through the ordinary invite path, with a fresh code.

    ``created_by`` stays ``None``: that column is admin provenance, and the
    inviter is recorded by the binding row instead of on the invite that
    outlives them. The label names the program, never the person.
    """
    for _ in range(_CODE_ATTEMPTS):
        try:
            async with session.begin_nested():
                return await beta.create_invite(
                    session,
                    label=PROGRAM_VERSION,
                    max_uses=max_uses,
                    expires_at=expires_at,
                    created_by=None,
                )
        except IntegrityError:
            continue
    raise ReferralIssueFailed()


async def ensure_referral(
    session: AsyncSession, *, account_id: uuid.UUID, now: datetime | None = None,
) -> ReferralState:
    """The code this account may share now, issuing one only if it must.

    Logically idempotent: repeated and concurrent calls converge on the same
    usable invite. The caller commits.
    """
    ts = now or utcnow()
    if not config.INVITE_REQUIRED:
        return ReferralState(state=STATE_UNAVAILABLE)
    # First in the lock order, and taken before anything is decided.
    status = await session.scalar(
        select(Account.status)
        .where(Account.id == account_id)
        .with_for_update(key_share=True)  # FOR NO KEY UPDATE
    )
    if status != ACCOUNT_STATUS_ACTIVE:
        return ReferralState(state=STATE_UNAVAILABLE)
    if not await has_useful_scan(session, account_id):
        return ReferralState(state=STATE_NOT_ACTIVATED)

    invites = await _bound_invites(session, account_id, lock=True)
    decided = _decide(invites, ts)
    if decided is not None:
        return decided

    remaining = LIFETIME_SUCCESSFUL_REFERRALS - lifetime_admissions(invites)
    # Everything bound here is already expired or full, or ``_decide`` would
    # have answered. Switching them off as well is belt and braces: a
    # superseded code stays dead even if its expiry were ever edited.
    for invite in invites:
        if invite.active:
            invite.active = False
    # Written before the savepoint below, so a code collision retried inside it
    # cannot roll this back with it.
    await session.flush()
    invite = await _issue_invite(session, max_uses=remaining, expires_at=ts + INVITE_LIFETIME)
    session.add(ConsumerReferralInvite(
        inviter_account_id=account_id,
        invite_id=invite.id,
        program_version=PROGRAM_VERSION,
    ))
    await session.flush()
    return _available(invite, lifetime_admissions(invites))


async def deactivate_referral_invites_for_account(
    session: AsyncSession, account_id: uuid.UUID,
) -> int:
    """Switch off every invite this account was issued to share. Idempotent.

    Called by the deletion worker before the account row is deleted, because
    the cascade removes the binding and afterwards nothing could say which
    invites were this person's. Takes the account row first — the order
    :func:`ensure_referral` takes — and only then the invites.

    Returns how many invites were switched off. The invite rows themselves
    stay: other people's redemptions point at them.
    """
    await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update()
    )
    result = await session.execute(
        update(Invite)
        .where(
            Invite.id.in_(
                select(ConsumerReferralInvite.invite_id)
                .where(ConsumerReferralInvite.inviter_account_id == account_id)
            ),
            Invite.active.is_(True),
        )
        .values(active=False)
        .returning(Invite.id)
        .execution_options(synchronize_session=False)
    )
    count = len(result.all())
    await session.flush()
    return count


async def referral_export(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """This account's own referral history, for its privacy export.

    No code — a live code is an access capability, and the owner can read it
    in the app — no invite id, and nothing about who was admitted: another
    person's registration is theirs, not the inviter's.
    """
    rows = (await session.execute(
        select(ConsumerReferralInvite, Invite)
        .join(Invite, Invite.id == ConsumerReferralInvite.invite_id)
        .where(ConsumerReferralInvite.inviter_account_id == account_id)
        .order_by(ConsumerReferralInvite.created_at, Invite.id)
    )).all()
    invites = [invite for _binding, invite in rows]
    return {
        "program_version": PROGRAM_VERSION,
        "lifetime_limit": LIFETIME_SUCCESSFUL_REFERRALS,
        "lifetime_successful_admissions": lifetime_admissions(invites),
        "issued_codes": [
            {
                "issued_at": binding.created_at.isoformat() if binding.created_at else None,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "max_admissions": invite.max_uses,
                "successful_admissions": invite.uses_count,
                "active": bool(invite.active),
            }
            for binding, invite in rows
        ],
    }


__all__ = [
    "INVITE_LIFETIME",
    "LIFETIME_SUCCESSFUL_REFERRALS",
    "PROGRAM_VERSION",
    "STATES",
    "STATE_AVAILABLE",
    "STATE_EXHAUSTED",
    "STATE_NOT_ACTIVATED",
    "STATE_READY",
    "STATE_UNAVAILABLE",
    "STATE_WITHDRAWN",
    "ReferralIssueFailed",
    "ReferralState",
    "deactivate_referral_invites_for_account",
    "ensure_referral",
    "lifetime_admissions",
    "read_referral",
    "referral_export",
]
