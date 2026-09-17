"""Who a personalised decision is for.

Most of this application was written when one account meant one person, so
``account_id`` served as the answer to three different questions at once: who
is allowed to make this request, whose data is being read, and whose body the
answer is about. A household breaks that. This module separates them.

* **The security principal** is the authenticated account. It never changes
  here, and nothing in this module widens what an account may reach.
* **The subject** is the human being a personalised interpretation is *about*.
  It is a row in ``family_profiles``, which belongs to a circle, which belongs
  to exactly one account.
* **Global Product Truth** is neither. What a pack contains does not depend on
  who is asking, and nothing in this module touches it.

The subject is always resolved on the server from the authenticated account.
A subject identifier arriving in a request is treated as a claim to be checked,
never as an instruction to be followed: an id belonging to somebody else's
household resolves to nothing at all, with the same answer as an id that never
existed, because telling the difference would confirm that the other household
has a member with that id.

One word, two meanings
----------------------
``subject_id`` already exists in this codebase and does not mean this. On
``/api/v2/routines/experience-feedback`` it names *the thing being reviewed* —
a product, or a step in a routine — alongside a ``subject_type`` that says
which. Here it names a *person*. The two are unrelated and must not be wired
together: passing a household member id where that route expects a product id,
or the reverse, would be a category error that the type system cannot catch
because both are UUIDs. Anything that needs both concepts in one place should
name them apart rather than assume they are the same field.

Why an age band lives here
--------------------------
The constitution's hardest rule is that the product must not advise about a
child under 12 — it states the fact and hands over. Until now the only way the
gate could learn that was a flag the client volunteered on each request, which
is fine as a disclosure and useless as an authority: the same client can simply
not send it. Once a household exists, how old somebody is becomes a fact the
server holds about them, and :func:`safety_for` makes that fact
non-negotiable. A request may *add* to what the gate knows. It can never
subtract from it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle, FamilyProfile
from app.shared.errors.exceptions import IdentityInvariantError

# --- Age, to the only precision the product needs ----------------------------

AGE_BAND_UNDER_12 = "under_12"
AGE_BAND_TEEN = "teen_12_17"
AGE_BAND_ADULT = "adult_18_plus"
AGE_BAND_NOT_STATED = "not_stated"

AGE_BANDS: tuple[str, ...] = (
    AGE_BAND_UNDER_12, AGE_BAND_TEEN, AGE_BAND_ADULT, AGE_BAND_NOT_STATED,
)

# The age the gate is told when a band is all we have. It is the *top* of each
# band, which is the cautious direction: claiming somebody is the oldest they
# could be never fires a handoff that should not have fired, and claiming they
# are younger than they are would. ``under_12`` is the one that matters, and 11
# is below ``hard_handoff.MINIMUM_AGE``.
_BAND_CEILING: dict[str, int | None] = {
    AGE_BAND_UNDER_12: 11,
    AGE_BAND_TEEN: 17,
    AGE_BAND_ADULT: None,
    AGE_BAND_NOT_STATED: None,
}

# --- What the subject is -----------------------------------------------------

SUBJECT_ACCOUNT_HOLDER = "account_holder"
SUBJECT_HOUSEHOLD_MEMBER = "household_member"

#: The relation the circle gives its first row. There is exactly one per
#: household and it is the account holder. It is a constant rather than a
#: literal because three separate places have to agree on what it is: the code
#: that creates it, the code that refuses to deactivate it, and the code below
#: that finds it again when a request names nobody.
RELATION_SELF = "self"


class SubjectNotFound(LookupError):
    """The named subject is not one this account may ask about."""


class HouseholdInvariantError(IdentityInvariantError):
    """A household exists but its account holder's row does not.

    Also raised when a circle somehow holds more than one active account
    holder. Both are structurally unreachable: the circle and its ``self`` row
    are created in one transaction, no route deletes a profile, ``update_profile``
    refuses to deactivate that row, and no route can create a second one. If
    either happens anyway the answer is to stop, not to invent an identity —
    synthesising a subject here would silently hand back ``not_stated`` for
    somebody the household may have recorded as a child, and picking one of two
    rows would decide whose body a decision is about by insertion order.

    It is an :class:`~app.shared.errors.exceptions.IdentityInvariantError` so
    that every route reaching it answers the same governed 503 rather than a
    bare 500 from whichever one happened to touch identity first. Step 11B
    added many such routes — Care, the shelf, ``/profile``, onboarding — and a
    per-route mapping would have been one more place to forget.
    """


@dataclass(frozen=True, slots=True)
class ResolvedSubject:
    """A subject the server has checked against the authenticated account."""

    kind: str
    account_id: uuid.UUID
    subject_id: uuid.UUID | None
    relation: str
    age_band: str

    @property
    def is_account_holder(self) -> bool:
        """Is this the signed-in person themselves?

        One source of truth. This used to also accept ``relation == "self"`` as
        a second way of being the account holder, which meant a resolver that
        classified the ``self`` row wrongly would still read correctly here and
        no test could see the mistake. ``kind`` is now set from the relation in
        exactly one place, so getting it wrong is visible.
        """
        return self.kind == SUBJECT_ACCOUNT_HOLDER

    @property
    def stated_age(self) -> int | None:
        """The age to hand the gate, or nothing when nobody has said."""
        return _BAND_CEILING.get(self.age_band)

    @property
    def is_child(self) -> bool:
        """Below the age at which this product stops answering."""
        return self.age_band == AGE_BAND_UNDER_12

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "subject_id": str(self.subject_id) if self.subject_id else None,
            "relation": self.relation,
            "age_band": self.age_band,
            "is_account_holder": self.is_account_holder,
        }


def account_holder_subject(account_id: uuid.UUID) -> ResolvedSubject:
    """The signed-in person, for an account that has never opened a household.

    There is no row to read, so nothing is claimed: ``not_stated`` is exactly
    what the product knew about that person before this module existed. Reading
    a decision must not create a household as a side effect, so this is
    synthesised rather than stored — and it is only ever reached when no circle
    exists at all.
    """
    return ResolvedSubject(
        kind=SUBJECT_ACCOUNT_HOLDER,
        account_id=account_id,
        subject_id=None,
        relation=RELATION_SELF,
        age_band=AGE_BAND_NOT_STATED,
    )


def _subject_from(profile: FamilyProfile, account_id: uuid.UUID) -> ResolvedSubject:
    """One stored row, one subject — however the request arrived at it.

    ``kind`` is derived from the relation here and nowhere else, so the ``self``
    row resolves identically whether a request named it or named nobody. Two
    spellings of the same human that produced two different subjects would be a
    seam for a later slice to attach two sets of Decision Memory to.
    """
    return ResolvedSubject(
        kind=(
            SUBJECT_ACCOUNT_HOLDER
            if profile.relation == RELATION_SELF
            else SUBJECT_HOUSEHOLD_MEMBER
        ),
        account_id=account_id,
        subject_id=profile.id,
        relation=profile.relation,
        age_band=profile.age_band,
    )


async def _stored_account_holder(
    session: AsyncSession, account_id: uuid.UUID,
) -> ResolvedSubject:
    """The default subject, read rather than assumed.

    Omitting ``subject_id`` is the request shape every client used before
    households existed, and it means "me". Once a household exists, what the
    server knows about "me" lives in the ``self`` row — including an age band
    the household may have deliberately recorded.

    Synthesising ``not_stated`` here instead, as this used to, made the optional
    Step 11 field the thing that decided whether stored authority applied. An
    account could record its holder as under twelve, watch the hand-over fire
    when the row was named explicitly, and then get an ordinary answer by simply
    not sending the field. An authority a client can skip by omission is not an
    authority.

    Reads only. An account with no circle gets the synthesised subject and no
    row is written, because asking a question is not opening a household.
    """
    circle_id = await session.scalar(
        select(FamilyCircle.id).where(FamilyCircle.account_id == account_id)
    )
    if circle_id is None:
        return account_holder_subject(account_id)

    # ``scalar_one_or_none`` rather than ``scalar``: a circle has exactly one
    # active account holder, and this asserts that rather than assuming it.
    # Taking the first row a broadened query happened to return would pick a
    # child in the same household and call them the account holder, silently
    # and depending on nothing more than insertion order.
    try:
        profile = (
            await session.execute(
                select(FamilyProfile).where(
                    FamilyProfile.circle_id == circle_id,
                    FamilyProfile.relation == RELATION_SELF,
                    FamilyProfile.active.is_(True),
                )
            )
        ).scalar_one_or_none()
    except MultipleResultsFound as exc:
        raise HouseholdInvariantError("household_has_multiple_self_profiles") from exc
    if profile is None:
        # A household whose account holder is missing. Falling back to the
        # synthesised subject would answer with weaker authority than this
        # household may have asked for, which is the failure this whole function
        # exists to prevent, so it stops instead.
        raise HouseholdInvariantError("household_self_profile_missing")
    return _subject_from(profile, account_id)


async def resolve_subject(
    session: AsyncSession, *, account_id: uuid.UUID, subject_id: uuid.UUID | None,
) -> ResolvedSubject:
    """Turn a claimed subject into a checked one, or refuse.

    The join to ``family_circles`` is the authorisation. A profile is only
    reachable through the circle its account owns, so a subject belonging to
    another household cannot be resolved however well-formed its id is, and an
    inactive member is refused for the same reason a removed one is: they are
    not somebody this account is currently asking about.

    Naming nobody is not a weaker version of naming yourself. Both paths end at
    the same stored row when there is one, and produce the same subject.
    """
    if subject_id is None:
        return await _stored_account_holder(session, account_id)

    profile = await session.scalar(
        select(FamilyProfile)
        .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
        .where(
            FamilyProfile.id == subject_id,
            FamilyProfile.active.is_(True),
            FamilyCircle.account_id == account_id,
        )
    )
    if profile is None:
        raise SubjectNotFound("subject_not_found")
    return _subject_from(profile, account_id)


async def resolve_subject_for_write(
    session: AsyncSession, *, account_id: uuid.UUID, subject_id: uuid.UUID,
) -> ResolvedSubject:
    """Resolve a named member and hold their membership still until commit.

    :func:`resolve_subject` answers "was this member active a moment ago". For a
    read that is the whole question. For a write it is not: a concurrent
    deactivation can land between the check and the row being written, and the
    write would then record facts about somebody the household had already
    taken out — claiming, in the same transaction, an authority that no longer
    existed when it committed.

    So the member row is taken ``FOR UPDATE`` and re-read under the lock.
    Deactivation is an ``UPDATE`` of that same row, so one of the two waits and
    the pair settles into a single order: either the write completes and the
    deactivation follows it, or the deactivation commits first and this refuses
    like any other inactive member.

    Lock order — this is the ``FamilyProfile`` step of the documented
    ``Account -> FamilyCircle / FamilyProfile -> AppearanceProfile`` order, and
    it stays inside it. No account-wide lock is taken to write about one member:
    that would serialise a household against itself for people the write has
    nothing to do with. The account holder's own path is the one that locks the
    account, and it does so *before* anything here, never after.
    """
    profile = await session.scalar(
        select(FamilyProfile)
        .join(FamilyCircle, FamilyCircle.id == FamilyProfile.circle_id)
        .where(
            FamilyProfile.id == subject_id,
            FamilyProfile.active.is_(True),
            FamilyCircle.account_id == account_id,
        )
        .with_for_update(of=FamilyProfile)
    )
    if profile is None:
        raise SubjectNotFound("subject_not_found")
    return _subject_from(profile, account_id)


def safety_for(
    subject: ResolvedSubject,
    *,
    stated_age: int | None = None,
    subject_is_child: bool = False,
) -> tuple[int | None, bool]:
    """Combine what the server knows with what the request disclosed.

    The rule is one-directional: a request may add to what the gate is told and
    may never take anything away. A stored ``under_12`` band stays ``under_12``
    however the request describes that person, and a request that volunteers a
    younger age than the stored band still counts, because somebody correcting
    us downwards about a child is exactly the case that must be heard.

    Returned as plain values rather than a personal-lens object so that the
    household domain does not have to import the lens it is an input to.
    """
    ages = [value for value in (subject.stated_age, stated_age) if value is not None]
    return (min(ages) if ages else None, subject.is_child or subject_is_child)


__all__ = [
    "AGE_BANDS",
    "AGE_BAND_ADULT",
    "AGE_BAND_NOT_STATED",
    "AGE_BAND_TEEN",
    "AGE_BAND_UNDER_12",
    "RELATION_SELF",
    "SUBJECT_ACCOUNT_HOLDER",
    "SUBJECT_HOUSEHOLD_MEMBER",
    "HouseholdInvariantError",
    "ResolvedSubject",
    "SubjectNotFound",
    "account_holder_subject",
    "resolve_subject",
    "resolve_subject_for_write",
    "safety_for",
]
