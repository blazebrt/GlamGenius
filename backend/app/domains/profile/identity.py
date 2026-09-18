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

**The principal and the subject are two different facts.**
Who is allowed to reach this data, and whose body a decision is about, are not
two spellings of one thing. They are carried separately, and the first is never
read off the second.

:class:`~app.domains.family.subject.ResolvedSubject` is an ordinary public
dataclass. Any caller can construct one — including a *complete and internally
consistent* identity belonging to somebody else, where ``account_id`` and
``subject_id`` agree with each other perfectly and the only thing wrong with
the pair is that it is not the caller's. Re-resolving the subject under its own
``account_id`` would confirm that forgery rather than catch it. An
``isinstance`` check is weaker still: it proves the shape, never that the server
agreed.

So every entry point here takes ``principal_account_id`` as a separate keyword
argument, sourced from authenticated context that the caller does not own, and
:func:`canonical_subject` requires the subject to agree with it before anything
is read. The claimed ``kind``, ``relation`` and ``age_band`` are then discarded
and rebuilt from the stored row. That last part is why the age band cannot be
talked down: a request can name a real under-twelve member of its *own*
household and claim they are an adult, and the band that reaches the
hard-handoff gate is still the stored one.

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
first creating anything.

The *mode* matters as much as the order, so each path is spelled out. And a
path never takes only the locks its code asks for: PostgreSQL's referential
integrity takes its own, and leaving those out of the account is how an order
that reads correctly deadlocks in production.

**Account-holder write** — ``resolve_self_profile_for_write``::

    Account FOR UPDATE
    -> AppearanceProfile (insert or update)

It takes no ``family_profiles`` lock. The ``self`` row cannot be deactivated —
``update_profile`` refuses it — so there is nothing to hold still, and taking
it after the account row is the only order that would not invert the one above.

**Non-self member write** — ``canonical_subject_for_write``::

    Account FOR KEY SHARE
    -> FamilyProfile FOR UPDATE
    -> AppearanceProfile (insert or update)
    -> ProfileAttribute and other child rows

The account lock here is the subtle one, and it exists for a reason that is
invisible in the application code. ``appearance_profiles.account_id`` is an
immediate ``ON DELETE CASCADE`` foreign key, so the *first* insert for a member
makes PostgreSQL check the parent with its own ``SELECT 1 FROM accounts WHERE
id = ... FOR KEY SHARE``. Without the explicit lock, the real order would be
``FamilyProfile -> Account``: the reverse of what account deletion does, and a
textbook deadlock cycle. A Care write would hold the member row and wait for
the account; a concurrent deletion would hold the account and wait, through its
cascade, for that same member row. Taking the account first, explicitly, puts
the database's implicit lock in the same order as the application's authority.

``FOR KEY SHARE`` and not ``FOR UPDATE``, deliberately. It is the weakest mode
that still blocks a ``DELETE`` of the parent, and two of them are compatible
with each other — so two people in one household can be written at the same
moment, each holding only their own member row. ``FOR UPDATE`` here would
serialise every member write in a household against every other for no gain.

**Opening a household** — ``circle_for(create=True)``::

    Account FOR UPDATE
    -> FamilyCircle / FamilyProfile (insert)

**Account deletion** — the deletion worker::

    DELETE accounts (exclusive row lock)
    -> ON DELETE CASCADE into family_circles / family_profiles /
       appearance_profiles and everything below them

Every path therefore takes the account row first, and no path takes a member
row and then reaches back for the account. Nothing upgrades an account lock
either: the non-self path never asks for ``FOR UPDATE`` after holding
``FOR KEY SHARE``, because two writers doing that could deadlock on the
upgrade itself.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.subject import (
    HouseholdInvariantError,
    ResolvedSubject,
    SubjectNotFound,
    resolve_subject,
    resolve_subject_for_write,
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
    "canonical_subject_for_write",
    "lock_account",
    "protect_account_from_delete",
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
    session: AsyncSession, *, principal_account_id: uuid.UUID, subject: object,
) -> ResolvedSubject:
    """Re-derive a subject under the authenticated principal. Nothing claimed survives.

    ``principal_account_id`` is the security principal: who is allowed to reach
    this data. ``subject`` is a claim about whose body a decision concerns. They
    are separate arguments because they are separate facts, and because the
    first must come from somewhere the caller does not control — the
    authenticated request, a worker's own scope — rather than being read back
    out of the claim it is meant to check.

    Taking ``subject.account_id`` as the principal, which this used to do, is
    exactly as strong as taking nothing at all. A forged
    ``ResolvedSubject(account_id=B, subject_id=B_MEMBER)`` is internally
    consistent: re-resolving it under its own account finds the row, agrees
    with itself, and hands account B's member to whoever asked. The two halves
    checking each other is not a check.

    So the principal is compared first, and only then is the subject read:

    * a subject naming another account refuses, whether or not its own fields
      agree with each other;
    * a subject id belonging to another household refuses;
    * a deactivated member refuses;
    * a forged ``kind=account_holder`` on an ordinary member does not delegate
      to the self path, because ``kind`` is recomputed from the stored relation;
    * a forged ``age_band`` is discarded, and the stored band is what reaches
      the hard-handoff gate;
    * a synthesised "no household" subject is only accepted while the principal
      genuinely has no household — once one exists it resolves to the stored
      ``self`` row instead, along with whatever that row says about age.

    Every refusal is the same :class:`~app.domains.family.subject.SubjectNotFound`
    with no detail, because saying "that account exists but is not yours" and
    "no such member" apart would confirm which accounts and members are real.
    :class:`~app.domains.family.subject.HouseholdInvariantError` is raised when
    the principal's household exists but has no single account holder.
    """
    if not isinstance(subject, ResolvedSubject):
        raise ValueError("subject must be a ResolvedSubject")
    if subject.account_id != principal_account_id:
        # Answered exactly like an unknown subject: which of the two halves was
        # wrong, and whether either named anything real, is not the caller's to
        # learn.
        raise SubjectNotFound("subject_not_found")
    return await resolve_subject(
        session, account_id=principal_account_id, subject_id=subject.subject_id,
    )


async def canonical_subject_for_write(
    session: AsyncSession, *, principal_account_id: uuid.UUID, subject: object,
) -> ResolvedSubject:
    """Canonicalise, and keep the answer true for the rest of the transaction.

    The plain :func:`canonical_subject` re-reads; this one re-reads and holds.
    For a named household member the ``family_profiles`` row is locked, so a
    concurrent deactivation cannot land between "this member is active" and the
    facts being written about them.

    Two locks, in this order, and only for a named member:

        Account FOR KEY SHARE -> FamilyProfile FOR UPDATE

    The account comes first so that the implicit foreign-key lock the coming
    ``appearance_profiles`` insert will take lands in the same order as this
    code's own authority rather than after the member row is already held. See
    the module docstring for why the reverse deadlocks against account deletion.

    The account holder takes neither. Their row cannot be deactivated, so there
    is nothing to hold still, and their write path takes the account row
    ``FOR UPDATE`` on its own. Deciding self from non-self happens *before* any
    lock is taken, so the self path never holds ``FOR KEY SHARE`` and then asks
    to upgrade — two writers doing that would deadlock on the upgrade.
    """
    subject = await canonical_subject(
        session, principal_account_id=principal_account_id, subject=subject,
    )
    if subject.is_account_holder or subject.subject_id is None:
        return subject

    await protect_account_from_delete(session, principal_account_id)
    return await resolve_subject_for_write(
        session, account_id=principal_account_id, subject_id=subject.subject_id,
    )


async def protect_account_from_delete(
    session: AsyncSession, account_id: uuid.UUID,
) -> None:
    """Hold the account against deletion, without holding it against anybody else.

    ``FOR KEY SHARE`` is the weakest row lock PostgreSQL offers that still
    conflicts with ``DELETE``. That is exactly the guarantee this path needs and
    no more: while a member's facts are being written, the account they hang off
    must not disappear — but another member of the same household is welcome to
    be written at the same moment, and two ``FOR KEY SHARE`` holders do not
    block each other.

    It is taken *before* the member row so that the application's order matches
    the one PostgreSQL will take anyway. Inserting the first
    ``appearance_profiles`` row runs an immediate foreign-key check against
    ``accounts``, and that check takes this same lock. Without the explicit call
    it would be taken second — after ``family_profiles`` — which is the reverse
    of account deletion's order and deadlocks against it.

    Refuses cleanly, as an unknown subject, when the account row has already
    gone: the cascades have taken the household and every profile with it, so
    there is genuinely no such member any more, and that is the same answer a
    foreign or invented id gets. Nothing is created.

    The lock itself lives in the domain that owns ``accounts``. This function is
    the identity boundary's sentence for the same fact, not a second lock — two
    places emitting almost the same SQL is how an ordering quietly stops being
    one.
    """
    from app.domains.identity.service import lock_account_against_delete

    if await lock_account_against_delete(session, account_id) is None:
        raise SubjectNotFound("subject_not_found")


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
    session: AsyncSession,
    subject: ResolvedSubject,
    *,
    principal_account_id: uuid.UUID,
) -> AppearanceProfile | None:
    """A checked subject's profile. Reads only.

    Canonicalises under the principal first, so a caller that constructed its
    own subject — even a complete and self-consistent one belonging to another
    account — reaches the stored row or nothing. Then delegates for the account
    holder, so that naming yourself and naming nobody cannot reach different
    rows.
    """
    subject = await canonical_subject(
        session, principal_account_id=principal_account_id, subject=subject,
    )
    if subject.is_account_holder:
        return await _self_profile_for_read(session, subject)
    assert subject.subject_id is not None
    return await _profile_for_subject(
        session, account_id=principal_account_id, subject_id=subject.subject_id,
    )


async def resolve_subject_profile_for_write(
    session: AsyncSession,
    subject: ResolvedSubject,
    *,
    principal_account_id: uuid.UUID,
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
    subject = await canonical_subject_for_write(
        session, principal_account_id=principal_account_id, subject=subject,
    )
    if subject.is_account_holder:
        return await resolve_self_profile_for_write(session, principal_account_id)

    assert subject.subject_id is not None
    existing = await _profile_for_subject(
        session, account_id=principal_account_id, subject_id=subject.subject_id,
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
            account_id=principal_account_id,
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
        session, account_id=principal_account_id, subject_id=subject.subject_id,
    )
    if profile is None:
        raise ProfileIdentityError("subject_profile_could_not_be_created")
    return profile
