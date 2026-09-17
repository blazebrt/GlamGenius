"""Which ``AppearanceProfile`` describes which human.

Before households, this question had one answer per account and the schema said
so with ``UNIQUE(account_id)``. Every caller could write ``get_profile(account_id)``
and be right. Once two people can share an account that sentence stops being
true, and — worse — the old lookups do not merely become vague. They used
``scalar_one_or_none()``, which *raises* on a second row, so the first household
to record a second person would have turned Care, the shelf and the
recommendation context into 500s for that account.

So the generic account-only lookup is gone rather than redefined, and four
resolvers take its place. Each one names both the human it is about and whether
it is allowed to write, because those are the two things a caller was
previously able to leave implicit and get wrong.

    resolve_self_profile_for_read       the signed-in person, never writes
    resolve_self_profile_for_write      the signed-in person, may adopt/create
    resolve_subject_profile_for_read    a named subject, never writes
    resolve_subject_profile_for_write   a named subject, may create

Three things hold this together, and each exists because assuming it was not
enough.

**The subject is re-resolved from the database, not trusted.**
:class:`~app.domains.family.subject.ResolvedSubject` is an ordinary public
dataclass. Any caller can construct one with an arbitrary ``account_id``,
``subject_id``, ``relation`` or ``age_band``, so an ``isinstance`` check proves
only that the shape is right — never that the server agreed. Every entry point
here therefore passes what it is given through :func:`canonical_subject`, which
throws the claimed fields away and rebuilds them from the stored row under the
authenticated account. That is also why the age band cannot be talked down: a
request can name a real under-twelve member and claim they are an adult, and
the band that reaches the hard-handoff gate is still the stored one.

**Ownership is two columns, not one.** A profile bound to a household subject is
only this account's profile when ``account_id`` agrees as well. The foreign key
proves the subject row exists; nothing in the schema proves it belongs to the
same account, because that would need a composite key the household tables do
not carry. So every subject-bound lookup filters on both, and a row that matches
one but not the other is not quietly skipped — skipping it would hide the
corruption and then insert a second profile on top of it. It stops.

**The canonical ``self`` row is Step 11A's, not a second opinion.** Asking "who
is the account holder?" has exactly one answer in this codebase:
:func:`~app.domains.family.subject.resolve_subject` with no subject named. It
returns a synthesised subject when no household exists, the stored ``self`` row
when one does, and refuses when a household holds none or holds two. A local
first-row query here would have silently disagreed with it in exactly those two
cases — picking a human by insertion order — so there is no local query.

Lock order, obeyed by every write path here and in :mod:`app.domains.family.service`:

    Account -> FamilyCircle / FamilyProfile -> AppearanceProfile

The account row is the serialisation point because it always exists — its id is
the Supabase user id, written at registration — so it can be locked without
first creating anything. Household creation and self profile adoption both take
it before they look at what exists, which is what makes them agree about a
household that is being created at the same moment.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.subject import (
    HouseholdInvariantError,
    ResolvedSubject,
    resolve_subject,
    safety_for,
)
from app.domains.identity.models import Account
from app.domains.profile.models import AppearanceProfile
from app.shared.errors.exceptions import IdentityInvariantError

#: Re-exported so the Personal Lens can name a checked subject, canonicalise it
#: and fold in its stored age without importing the family domain directly.
#: ``personal_lens`` is deliberately fenced off from ``app.domains.family`` by an
#: import guard, and that fence is worth keeping: the lens should depend on
#: profile resolution, not on how households are structured. Profile resolution
#: is precisely the thing that legitimately knows about both.
SubjectRef = ResolvedSubject

__all__ = [
    "HouseholdInvariantError",
    "ProfileIdentityError",
    "SubjectRef",
    "canonical_subject",
    "lock_account",
    "resolve_self_profile_for_read",
    "resolve_self_profile_for_write",
    "resolve_subject_profile_for_read",
    "resolve_subject_profile_for_write",
    "safety_for",
]


class ProfileIdentityError(IdentityInvariantError):
    """Stored profile identity is in a state no route can produce.

    Two shapes reach here. One account holding both a legacy NULL-subject
    profile and an adopted one for the same human — choosing between them would
    silently pick whose body facts count, and merging them would invent a
    person. And a profile bound to a household subject while naming a different
    account, which no route can create and which, left alone, would let one
    account read and then overwrite another account's body facts.

    Neither is a customer state, so both stop rather than guess. It carries a
    fixed customer-safe sentence and a reason for the log; no account id, no
    profile id, no subject id and no conflicting value ever reaches the caller.
    """


async def canonical_subject(
    session: AsyncSession, subject: object,
) -> ResolvedSubject:
    """Re-derive a subject from the database. Nothing claimed survives.

    The type is not the guard, because ``ResolvedSubject`` is a public dataclass
    with a public constructor. A caller can build one naming somebody else's
    household member, or naming a real under-twelve member of their own
    household as an adult, and the shape is indistinguishable from one this
    server produced. Only a read decides.

    So the only field taken at face value is ``account_id`` — which is not a
    claim, it is the authenticated principal established before this call — and
    everything else is re-read under it. ``kind``, ``relation`` and ``age_band``
    come back from the stored row, so:

    * a subject id belonging to another household refuses;
    * a deactivated member refuses;
    * a forged ``kind=account_holder`` on an ordinary member does not delegate
      to the self path, because ``kind`` is recomputed from the stored relation;
    * a forged ``age_band`` is discarded, and the stored band is what reaches
      the hard-handoff gate;
    * a synthesised "no household" subject is only accepted while the account
      genuinely has no household — once one exists it resolves to the stored
      ``self`` row instead, along with whatever that row says about age.

    Raises :class:`~app.domains.family.subject.SubjectNotFound` for a subject
    this account may not ask about, and
    :class:`~app.domains.family.subject.HouseholdInvariantError` when the
    household exists but has no single account holder.
    """
    if not isinstance(subject, ResolvedSubject):
        raise ValueError("subject must be a ResolvedSubject")
    return await resolve_subject(
        session, account_id=subject.account_id, subject_id=subject.subject_id,
    )


async def lock_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Take the account row lock that orders every identity transition.

    First in the documented order, and taken before the caller decides what
    exists. A decision made before this lock is a decision about a past that
    another transaction may already have changed.
    """
    await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update()
    )


async def _canonical_self_subject(
    session: AsyncSession, account_id: uuid.UUID,
) -> ResolvedSubject:
    """Step 11A's answer to "who is the account holder?", not a second one.

    ``subject_id`` is ``None`` on the result exactly when the account has no
    household at all. When it is set, it is the one active ``self`` row — and
    zero or two of those raise rather than resolving, which is the whole reason
    this delegates instead of running its own query.
    """
    return await resolve_subject(session, account_id=account_id, subject_id=None)


async def _legacy_profile(
    session: AsyncSession, account_id: uuid.UUID,
) -> AppearanceProfile | None:
    """The account-level profile written before this account had a household."""
    return await session.scalar(
        select(AppearanceProfile).where(
            AppearanceProfile.account_id == account_id,
            AppearanceProfile.household_subject_id.is_(None),
        )
    )


async def _profile_for_subject(
    session: AsyncSession, *, account_id: uuid.UUID, subject_id: uuid.UUID,
) -> AppearanceProfile | None:
    """The profile for one subject, or nothing — never somebody else's.

    Looked up by subject alone first, deliberately. Filtering on both columns in
    one query would make a cross-account row indistinguishable from no row, and
    "no row" is an answer this module acts on: it goes on to insert. Inserting
    on top of corruption would either fail on the unique index for reasons
    nobody could read, or — if the corrupt row were ever removed — leave two
    profiles for one human.

    So the mismatch is *detected*. It cannot happen through any route; the
    foreign key to ``family_profiles`` proves the subject exists but says
    nothing about whose account it belongs to, and no composite key spans the
    two stores of identity. If it happens anyway, this is the line that stops
    one account from reading, and then writing over, another account's body
    facts.
    """
    profile = await session.scalar(
        select(AppearanceProfile).where(
            AppearanceProfile.household_subject_id == subject_id
        )
    )
    if profile is None:
        return None
    if profile.account_id != account_id:
        raise ProfileIdentityError("profile_subject_account_mismatch")
    return profile


async def _self_profile_for_read(
    session: AsyncSession, self_subject: ResolvedSubject,
) -> AppearanceProfile | None:
    """The account holder's profile, given the canonical self already resolved.

    Reads only. A household that exists while the profile is still NULL-subject
    is a perfectly ordinary state — it simply means nobody has written since the
    household was opened. The row is *interpreted* as the account holder's
    without being touched, because a read that quietly rewrote identity would
    make every GET a migration.
    """
    account_id = self_subject.account_id
    legacy = await _legacy_profile(session, account_id)
    if self_subject.subject_id is None:
        # No household at all. The legacy row is the whole answer, and there is
        # no subject for anything to be bound to.
        return legacy

    adopted = await _profile_for_subject(
        session, account_id=account_id, subject_id=self_subject.subject_id,
    )
    if adopted is not None and legacy is not None:
        raise ProfileIdentityError("account_has_dual_self_profiles")
    return adopted or legacy


async def resolve_self_profile_for_read(
    session: AsyncSession, account_id: uuid.UUID,
) -> AppearanceProfile | None:
    """The signed-in person's profile. Reads only; never adopts, never creates."""
    return await _self_profile_for_read(
        session, await _canonical_self_subject(session, account_id),
    )


async def resolve_self_profile_for_write(
    session: AsyncSession, account_id: uuid.UUID,
) -> AppearanceProfile:
    """The signed-in person's profile, created or adopted if it is time.

    Adoption is one column on the row that already exists. The profile keeps its
    primary key, so every historical attribute, observation, goal, preference
    and change event stays exactly where it is — nothing is cloned, nothing is
    copied forward, and no history is rewritten. The row does not become a
    different profile; it becomes the same profile with a name attached.

    A household whose canonical ``self`` row is missing or doubled does not
    reach the creation paths below. It raises, because the alternatives are
    writing a second legacy profile for somebody who already has one and
    picking a human by insertion order.
    """
    await lock_account(session, account_id)

    # After the lock, so the answer is about the household as it is now rather
    # than as it was when another transaction started committing one.
    self_subject = await _canonical_self_subject(session, account_id)
    legacy = await _legacy_profile(session, account_id)

    if self_subject.subject_id is None:
        if legacy is not None:
            return legacy
        profile = AppearanceProfile(account_id=account_id)
        session.add(profile)
        await session.flush()
        return profile

    adopted = await _profile_for_subject(
        session, account_id=account_id, subject_id=self_subject.subject_id,
    )
    if adopted is not None and legacy is not None:
        raise ProfileIdentityError("account_has_dual_self_profiles")
    if adopted is not None:
        return adopted
    if legacy is not None:
        legacy.household_subject_id = self_subject.subject_id
        await session.flush()
        return legacy

    profile = AppearanceProfile(
        account_id=account_id, household_subject_id=self_subject.subject_id,
    )
    session.add(profile)
    await session.flush()
    return profile


async def resolve_subject_profile_for_read(
    session: AsyncSession, subject: ResolvedSubject,
) -> AppearanceProfile | None:
    """A checked subject's profile. Reads only.

    Canonicalises first, so a caller that constructed its own subject reaches
    the stored one or nothing. Then delegates for the account holder, so that
    naming yourself and naming nobody cannot reach different rows.
    """
    subject = await canonical_subject(session, subject)
    if subject.is_account_holder:
        return await _self_profile_for_read(session, subject)
    assert subject.subject_id is not None
    return await _profile_for_subject(
        session, account_id=subject.account_id, subject_id=subject.subject_id,
    )


async def resolve_subject_profile_for_write(
    session: AsyncSession, subject: ResolvedSubject,
) -> AppearanceProfile:
    """A checked subject's profile, created on first write.

    The generic create path below is for **non-self members only** — the
    delegation above is what keeps it that way. Without it, naming the account
    holder explicitly would look for a subject-bound profile, find none while a
    legacy NULL-subject row sat right there, and insert a second profile for the
    same person.

    Two devices writing for the same new member both find nothing and both
    insert. ``uq_appearance_profile_household_subject`` decides between them,
    and the loser re-reads rather than guessing: a conflict on its own is not
    evidence that the right row exists, only that *some* row does.

    This path deliberately does not take the account lock. It does not decide
    anything about the account holder's identity — it either finds this
    member's profile or makes it — so the index is the whole authority, and
    holding an account-wide lock to create a second member's profile would
    serialise a household against itself for no gain.
    """
    subject = await canonical_subject(session, subject)
    if subject.is_account_holder:
        return await resolve_self_profile_for_write(session, subject.account_id)

    assert subject.subject_id is not None
    existing = await _profile_for_subject(
        session, account_id=subject.account_id, subject_id=subject.subject_id,
    )
    if existing is not None:
        return existing

    # Two devices can both find nothing and both insert. ``ON CONFLICT`` has to
    # name the index predicate as well as the column: PostgreSQL will not infer
    # a *partial* unique index from ``(household_subject_id)`` alone, and an
    # inference that matches no index is an error rather than a fallback.
    #
    # ``DO NOTHING`` returns no row to the loser, and that is the useful part. A
    # conflict says some row exists; it does not say the row that exists is this
    # subject's. So the answer is never the insert's own result — it is always a
    # fresh read afterwards.
    await session.execute(
        pg_insert(AppearanceProfile.__table__)
        .values(
            account_id=subject.account_id,
            household_subject_id=subject.subject_id,
        )
        .on_conflict_do_nothing(
            index_elements=[AppearanceProfile.__table__.c.household_subject_id],
            index_where=text("household_subject_id IS NOT NULL"),
        )
    )
    # Whichever way it went, the answer is read back rather than assumed — and
    # read back under *both* columns, so a conflict caused by another account's
    # corrupt row raises instead of being mistaken for this account's profile
    # having been created.
    profile = await _profile_for_subject(
        session, account_id=subject.account_id, subject_id=subject.subject_id,
    )
    if profile is None:
        raise ProfileIdentityError("subject_profile_could_not_be_created")
    return profile
