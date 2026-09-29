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
* **Shareable means reservable.** A code is handed out only while
  ``/access/reserve`` would accept one more person with it, by the admission
  service's own rule: its places are ``max_uses`` minus ``uses_count`` minus
  the reservations still holding one (``active`` and ``expires_at`` after
  now). When sign-ups in progress hold every remaining place, the answer is
  ``capacity_reserved`` with no code, and nothing new is minted: the held
  places either finalise, and count against the ceiling, or lapse, and the
  same code is shareable again.
* **No reward.** Nothing is paid, credited, counted publicly or ranked.

Lock order
----------
::

    Account FOR UPDATE -> bound Invite rows FOR UPDATE -> count reservations
    (plain MVCC read, no lock) -> insert

The account row is the serialisation point: two simultaneous requests to
ensure a code queue on it, and the second one reads the invite the first one
committed instead of minting a second live code. ``FOR UPDATE`` is the lock the
repository already uses to order identity transitions on the account row
(``profile.identity.lock_account``); it conflicts with itself and with
``DELETE``. The one weaker account lock, ``FOR KEY SHARE`` against deletion, is
emitted in exactly one place (``identity.service.lock_account_against_delete``)
and is not what this needs, because two holders of it do not exclude each
other. The cost is that a child-row insert on the same account waits for the
few milliseconds an issuance takes.

Account deletion takes the same account row first and the invites second
(:func:`deactivate_referral_invites_for_account`), so the two can never hold
the pair in opposite orders. Whichever commits first wins cleanly: a code
issued first is switched off by the deletion that follows it; a deletion that
started first leaves an account this module refuses to issue for.

Reservations are counted, never locked. Registration locks a reservation and
then the invite; locking reservations after the invite here would be the
opposite order. The count needs no lock: ``/access/reserve`` must take the
invite row before it can add a reservation, and this path already holds it, so
none can appear before commit. A registration finalising at the same moment is
still seen as a held place until it commits — a code withheld for a moment,
never a place sold twice.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.domains.beta_access import service as beta
from app.domains.beta_access.models import Invite, InviteRegistrationReservation
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
#: A usable code exists, somebody could reserve a place with it now, and it
#: is returned.
STATE_AVAILABLE = "available"
#: The current code is live, but sign-ups already in progress hold every place
#: it has left. No code is returned and none is minted: when a held place lapses
#: the same code is shareable again; when it finalises it counts.
STATE_CAPACITY_RESERVED = "capacity_reserved"
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
    STATE_NOT_ACTIVATED, STATE_READY, STATE_AVAILABLE, STATE_CAPACITY_RESERVED,
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


def reservable_places(invite: Invite, *, used: int, held: int) -> int:
    """Admissions somebody could reserve with ``invite`` right now.

    The admission service's own rule — ``max_uses - uses_count`` less the
    places live reservations hold — bounded by what the program has left.
    Never negative.
    """
    remaining = min(
        invite.max_uses - invite.uses_count,
        LIFETIME_SUCCESSFUL_REFERRALS - used,
    )
    return max(0, remaining - held)


def _available(invite: Invite, used: int, held: int) -> ReferralState:
    places = reservable_places(invite, used=used, held=held)
    if places == 0:
        return ReferralState(state=STATE_CAPACITY_RESERVED)
    return ReferralState(
        state=STATE_AVAILABLE, code=invite.code,
        expires_at=invite.expires_at, remaining_uses=places,
    )


def _current(invites: list[Invite], now: datetime) -> Invite | None:
    """The usable code, if any. One at a time is enforced at issue; the oldest
    usable one is the authority should that ever have been broken."""
    return next((invite for invite in invites if _usable(invite, now)), None)


def _decide(invites: list[Invite], now: datetime, *, held: int) -> ReferralState | None:
    """The answer the bound invites already determine, or ``None`` for "issue".

    ``held`` is the number of live reservations against :func:`_current`.
    Shared by the read and the write so the two cannot disagree about what a
    set of invites means.
    """
    used = lifetime_admissions(invites)
    if used >= LIFETIME_SUCCESSFUL_REFERRALS:
        return ReferralState(state=STATE_EXHAUSTED)
    current = _current(invites, now)
    if current is not None:
        return _available(current, used, held)
    if any(_withdrawn(invite, now) for invite in invites):
        return ReferralState(state=STATE_WITHDRAWN)
    return None


async def _held_places(
    session: AsyncSession, invite: Invite | None, now: datetime,
) -> int:
    """Places on ``invite`` held by sign-ups in progress.

    Exactly the reservations ``beta.reserve_invite`` counts: still ``active``
    and not yet past ``expires_at``. A reservation past its time holds nothing
    even before the sweep marks it expired. A plain read — see the lock order
    in the module docstring for why it takes no lock.
    """
    if invite is None:
        return 0
    return int(await session.scalar(
        select(func.count(InviteRegistrationReservation.id)).where(
            InviteRegistrationReservation.invite_id == invite.id,
            InviteRegistrationReservation.status == beta.RESERVATION_STATUS_ACTIVE,
            InviteRegistrationReservation.expires_at > now,
        )
    ) or 0)


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
    invites = await _bound_invites(session, account_id, lock=False)
    held = await _held_places(session, _current(invites, ts), ts)
    decided = _decide(invites, ts, held=held)
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
        .with_for_update()
    )
    if status != ACCOUNT_STATUS_ACTIVE:
        return ReferralState(state=STATE_UNAVAILABLE)
    if not await has_useful_scan(session, account_id):
        return ReferralState(state=STATE_NOT_ACTIVATED)

    invites = await _bound_invites(session, account_id, lock=True)
    # Counted after the invite rows are held, so no reservation can be added
    # between this count and the commit.
    held = await _held_places(session, _current(invites, ts), ts)
    decided = _decide(invites, ts, held=held)
    if decided is not None:
        # Including ``capacity_reserved``: a live code whose places are only
        # held is not replaced.
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
    # Nobody can hold a place on a code that did not exist until now.
    return _available(invite, lifetime_admissions(invites), 0)


async def deactivate_referral_invites_for_account(
    session: AsyncSession, account_id: uuid.UUID,
) -> int:
    """Switch off every invite this account was issued to share. Idempotent.

    Called by the deletion worker before the account row is deleted, because
    the cascade removes the binding and afterwards nothing could say which
    invites were this person's. Takes the account row first — the order
    :func:`ensure_referral` takes — and only then the invites.

    The account lock is ``FOR SHARE``: the weakest mode that still conflicts
    with issuance's ``FOR UPDATE``, so the two serialise in one direction and
    never hold account and invite in opposite orders. It is also the mode of
    Lane A's lifecycle gate (``identity.service.hold_account_active``), so an
    upload or scan already past authentication is not queued behind the rest
    of the deletion: it reads a status that is no longer active and refuses at
    once. ``FOR UPDATE`` here made such a request wait for the whole database
    stage. The account row's ``DELETE`` later in the same transaction still
    waits for any holder, exactly as before Step 15.

    Returns how many invites were switched off. The invite rows themselves
    stay: other people's redemptions point at them.
    """
    await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update(read=True)
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


def referral_history(
    bindings: list[ConsumerReferralInvite], invites: list[Invite],
) -> dict[str, Any]:
    """This account's own referral history, as its privacy export states it.

    ``bindings`` are this account's ``consumer_referral_invites`` rows and
    ``invites`` the invites they name, both already selected in SQL by the
    privacy exporter (``app.domains.privacy.export._growth``) under the
    account's own scope — the read that the export's coverage proof checks.
    This decides only what a person reads back.

    No code — a live code is an access capability, and the owner can read it
    in the app — no invite id, no binding id, and nothing about who was
    admitted: another person's registration is theirs, not the inviter's.

    Every binding names an invite (``invite_id`` is ``NOT NULL`` and
    ``RESTRICT``). One that is missing from ``invites`` is a broken read, and
    the ``KeyError`` fails the export as incomplete rather than dropping a row.
    """
    by_id = {invite.id: invite for invite in invites}
    pairs = [(binding, by_id[binding.invite_id]) for binding in bindings]
    return {
        "program_version": PROGRAM_VERSION,
        "lifetime_limit": LIFETIME_SUCCESSFUL_REFERRALS,
        "lifetime_successful_admissions": lifetime_admissions([invite for _binding, invite in pairs]),
        "issued_codes": [
            {
                "issued_at": binding.created_at.isoformat() if binding.created_at else None,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "max_admissions": invite.max_uses,
                "successful_admissions": invite.uses_count,
                "active": bool(invite.active),
            }
            for binding, invite in pairs
        ],
    }


__all__ = [
    "INVITE_LIFETIME",
    "LIFETIME_SUCCESSFUL_REFERRALS",
    "PROGRAM_VERSION",
    "STATES",
    "STATE_AVAILABLE",
    "STATE_CAPACITY_RESERVED",
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
    "referral_history",
    "reservable_places",
]
