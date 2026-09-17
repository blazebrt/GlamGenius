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

The two subject resolvers **delegate to the self resolvers** whenever the
subject is the account holder. That is not a convenience; it is the invariant
that stops one human from acquiring two profiles. Without it, naming the
canonical ``self`` row explicitly would look for a subject-bound profile, find
none while a legacy NULL-subject row sat right there, and insert a second
profile for the same person.

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

from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import RELATION_SELF, ResolvedSubject
from app.domains.identity.models import Account
from app.domains.profile.models import AppearanceProfile

#: Re-exported so the Personal Lens can name a checked subject without
#: importing the family domain directly. ``personal_lens`` is deliberately
#: fenced off from ``app.domains.family`` by an import guard, and that fence is
#: worth keeping: the lens should depend on profile resolution, not on how
#: households are structured. Profile resolution is precisely the thing that
#: legitimately knows about both.
SubjectRef = ResolvedSubject


def require_resolved_subject(subject: object) -> ResolvedSubject:
    """Refuse anything that has not been through ``resolve_subject()``.

    The type is the guard. A raw ``household_subject_id`` cannot be turned into
    one of these by a caller, so a forged id cannot reach a profile lookup by
    being passed along as a plausible-looking value.
    """
    if not isinstance(subject, ResolvedSubject):
        raise ValueError("subject must be a checked ResolvedSubject")
    return subject


class ProfileIdentityError(RuntimeError):
    """One account appears to hold two identities for the same human.

    Reachable only through corruption or a bug: a legacy NULL-subject profile
    and a subject-bound profile for the canonical ``self`` row, at the same
    time. Choosing one would silently pick whose body facts count, and merging
    them would invent a person, so personalised interpretation stops here until
    somebody repairs it.
    """


async def lock_account(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Take the account row lock that orders every identity transition.

    First in the documented order, and taken before the caller decides what
    exists. A decision made before this lock is a decision about a past that
    another transaction may already have changed.
    """
    await session.execute(
        select(Account.id).where(Account.id == account_id).with_for_update()
    )


async def _canonical_self_row(
    session: AsyncSession, account_id: uuid.UUID,
) -> FamilyProfile | None:
    """This account's active ``self`` family row, if a household exists."""
    return await session.scalar(
        select(FamilyProfile)
        .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
        .where(
            FamilyCircle.account_id == account_id,
            FamilyProfile.relation == RELATION_SELF,
            FamilyProfile.active.is_(True),
        )
    )


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


async def _profile_bound_to(
    session: AsyncSession, subject_id: uuid.UUID,
) -> AppearanceProfile | None:
    return await session.scalar(
        select(AppearanceProfile).where(
            AppearanceProfile.household_subject_id == subject_id
        )
    )


async def resolve_self_profile_for_read(
    session: AsyncSession, account_id: uuid.UUID,
) -> AppearanceProfile | None:
    """The signed-in person's profile. Reads only; never adopts, never creates.

    A household that exists while the profile is still NULL-subject is a
    perfectly ordinary state — it simply means nobody has written since the
    household was opened. The row is *interpreted* as the account holder's
    without being touched, because a read that quietly rewrote identity would
    make every GET a migration.
    """
    # One statement. Every appearance profile this account owns, with the
    # household row each is bound to where there is one, so the legacy row and
    # an adopted row are seen in the same snapshot rather than in two reads
    # that another transaction could commit between.
    rows = (await session.execute(
        select(AppearanceProfile, FamilyProfile)
        .outerjoin(FamilyProfile, FamilyProfile.id == AppearanceProfile.household_subject_id)
        .where(AppearanceProfile.account_id == account_id)
    )).all()

    legacy = next((p for p, member in rows if p.household_subject_id is None), None)
    adopted = next(
        (
            p for p, member in rows
            if member is not None
            and member.relation == RELATION_SELF
            and member.active
        ),
        None,
    )
    if adopted is not None and legacy is not None:
        raise ProfileIdentityError("account_has_dual_self_profiles")
    return adopted or legacy


async def resolve_self_profile_for_write(
    session: AsyncSession, account_id: uuid.UUID,
) -> AppearanceProfile:
    """The signed-in person's profile, created or adopted if it is time.

    Adoption is one column on the row that already exists. The profile keeps its
    primary key, so every historical attribute, observation, goal, preference
    and change event stays exactly where it is — nothing is cloned, nothing is
    copied forward, and no history is rewritten. The row does not become a
    different profile; it becomes the same profile with a name attached.
    """
    await lock_account(session, account_id)

    legacy = await _legacy_profile(session, account_id)
    self_row = await _canonical_self_row(session, account_id)

    if self_row is None:
        if legacy is not None:
            return legacy
        profile = AppearanceProfile(account_id=account_id)
        session.add(profile)
        await session.flush()
        return profile

    adopted = await _profile_bound_to(session, self_row.id)
    if adopted is not None and legacy is not None:
        raise ProfileIdentityError("account_has_dual_self_profiles")
    if adopted is not None:
        return adopted
    if legacy is not None:
        legacy.household_subject_id = self_row.id
        await session.flush()
        return legacy

    profile = AppearanceProfile(account_id=account_id, household_subject_id=self_row.id)
    session.add(profile)
    await session.flush()
    return profile


async def resolve_subject_profile_for_read(
    session: AsyncSession, subject: ResolvedSubject,
) -> AppearanceProfile | None:
    """A checked subject's profile. Reads only.

    Delegates for the account holder so that naming yourself and naming nobody
    cannot reach different rows.
    """
    if subject.is_account_holder:
        return await resolve_self_profile_for_read(session, subject.account_id)
    assert subject.subject_id is not None
    return await _profile_bound_to(session, subject.subject_id)


async def resolve_subject_profile_for_write(
    session: AsyncSession, subject: ResolvedSubject,
) -> AppearanceProfile:
    """A checked subject's profile, created on first write.

    The generic create path below is for **non-self members only** — the
    delegation above is what keeps it that way. ``subject`` has already been
    checked against the authenticated account by ``resolve_subject()``; a
    foreign, inactive or invented id never reaches here.

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
    if subject.is_account_holder:
        return await resolve_self_profile_for_write(session, subject.account_id)

    assert subject.subject_id is not None
    existing = await _profile_bound_to(session, subject.subject_id)
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
    # Whichever way it went, the answer is read back rather than assumed. Both
    # outcomes look the same from here — no row returned — so the read is the
    # only thing that can tell "mine" from "somebody else got there first", and
    # it is the only thing that returns a row this session can actually write
    # through.
    profile = await _profile_bound_to(session, subject.subject_id)
    if profile is None:
        raise ProfileIdentityError("subject_profile_could_not_be_created")
    return profile


__all__ = [
    "ProfileIdentityError",
    "SubjectRef",
    "require_resolved_subject",
    "lock_account",
    "resolve_self_profile_for_read",
    "resolve_self_profile_for_write",
    "resolve_subject_profile_for_read",
    "resolve_subject_profile_for_write",
]
