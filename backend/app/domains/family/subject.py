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
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle, FamilyProfile

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


class SubjectNotFound(LookupError):
    """The named subject is not one this account may ask about."""


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
        """Is this the signed-in person themselves?"""
        return self.kind == SUBJECT_ACCOUNT_HOLDER or self.relation == "self"

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
    """The default subject: the signed-in person, with nothing claimed.

    An account that has never opened a household still has a subject — itself —
    and it carries ``not_stated``, which is exactly what the product knew about
    that person before this module existed. Reading a decision must not create
    a household as a side effect, so this is synthesised rather than stored.
    """
    return ResolvedSubject(
        kind=SUBJECT_ACCOUNT_HOLDER,
        account_id=account_id,
        subject_id=None,
        relation="self",
        age_band=AGE_BAND_NOT_STATED,
    )


async def resolve_subject(
    session: AsyncSession, *, account_id: uuid.UUID, subject_id: uuid.UUID | None,
) -> ResolvedSubject:
    """Turn a claimed subject into a checked one, or refuse.

    The join to ``family_circles`` is the authorisation. A profile is only
    reachable through the circle its account owns, so a subject belonging to
    another household cannot be resolved however well-formed its id is, and an
    inactive member is refused for the same reason a removed one is: they are
    not somebody this account is currently asking about.
    """
    if subject_id is None:
        return account_holder_subject(account_id)

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
    return ResolvedSubject(
        kind=SUBJECT_HOUSEHOLD_MEMBER,
        account_id=account_id,
        subject_id=profile.id,
        relation=profile.relation,
        age_band=profile.age_band,
    )


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
    "SUBJECT_ACCOUNT_HOLDER",
    "SUBJECT_HOUSEHOLD_MEMBER",
    "ResolvedSubject",
    "SubjectNotFound",
    "account_holder_subject",
    "resolve_subject",
    "safety_for",
]
