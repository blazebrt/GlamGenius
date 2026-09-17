"""Account-local Family Circle operations and offer policy."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import AGE_BAND_NOT_STATED, AGE_BANDS, RELATION_SELF
from app.domains.profile.identity import lock_account

MAX_PROFILES = 8


class _Unchanged:
    """The absence of a value, told apart from a value that happens to be falsey.

    ``active=False`` and "the caller said nothing about ``active``" are two
    different instructions, and ``None`` cannot carry both. A partial update
    needs to know which fields were actually named.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "UNCHANGED"


UNCHANGED = _Unchanged()


class FamilyProfileError(ValueError):
    pass


def profile_label(profile: FamilyProfile) -> str:
    if profile.relation == RELATION_SELF:
        return "You"
    return f"{profile.relation.capitalize()} profile {profile.position}"


def serialise_profile(profile: FamilyProfile) -> dict[str, object]:
    return {"id": profile.id, "position": profile.position, "relation": profile.relation,
            "label": profile_label(profile), "age_band": profile.age_band,
            "active": profile.active}


async def circle_for(session: AsyncSession, account_id: uuid.UUID, *, create: bool = False) -> FamilyCircle | None:
    """This account's circle, opened on first use.

    ``account_id`` is unique on ``family_circles``, which is the real guard: two
    requests that both find no circle and both insert one cannot both succeed.
    The loser's insert blocks until the winner commits and then fails, so it is
    made inside a savepoint and answered by reading the circle the winner just
    created. Without the savepoint that failure would poison the whole
    transaction and take the rest of the request down with it; without the
    re-read it would be a 500 for somebody who opened the app on two devices.
    """
    circle = await session.scalar(
        select(FamilyCircle).where(FamilyCircle.account_id == account_id)
    )
    if circle is not None or not create:
        return circle

    # First in the documented lock order — Account -> FamilyCircle /
    # FamilyProfile -> AppearanceProfile — and taken before this path decides
    # what exists. Opening a household and adopting the account holder's
    # profile are two halves of one identity transition: if they do not
    # serialise on the same row, one transaction can decide "no household, use
    # the legacy profile" while another is committing the household that would
    # have changed its mind, and the account ends up with two self identities.
    await lock_account(session, account_id)
    circle = await session.scalar(
        select(FamilyCircle).where(FamilyCircle.account_id == account_id)
    )
    if circle is not None:
        return circle
    try:
        async with session.begin_nested():
            circle = FamilyCircle(account_id=account_id)
            session.add(circle)
            await session.flush()
            session.add(FamilyProfile(circle_id=circle.id, position=1, relation=RELATION_SELF))
            await session.flush()
    except IntegrityError:
        circle = await session.scalar(
            select(FamilyCircle).where(FamilyCircle.account_id == account_id)
        )
        if circle is None:
            # The insert failed for a reason that was not this race. Nothing
            # here should invent a circle to cover that up.
            raise
    return circle


async def read_circle(session: AsyncSession, account_id: uuid.UUID) -> dict[str, object]:
    circle = await circle_for(session, account_id)
    if circle is None:
        return {"enabled": False, "max_profiles": MAX_PROFILES, "profiles": [], "shared_shelf": False, "shared_verdicts": False}
    profiles = (await session.scalars(
        select(FamilyProfile).where(FamilyProfile.circle_id == circle.id).order_by(FamilyProfile.position)
    )).all()
    return {"enabled": circle.active, "max_profiles": MAX_PROFILES,
            "profiles": [serialise_profile(profile) for profile in profiles],
            "shared_shelf": circle.active, "shared_verdicts": circle.active}


async def add_profile(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    relation: str,
    age_band: str = AGE_BAND_NOT_STATED,
) -> FamilyProfile:
    """Add one member to this account's circle.

    The circle row is locked before the free position is chosen. Without that,
    two requests adding a member at once both read the same set of positions,
    both pick the same number and the second one violates
    ``uq_family_profile_position`` — a 500 for somebody who tapped add twice.
    The lock makes choosing and taking a position a single serialised step, so
    the second request simply gets the next number.
    """
    if age_band not in AGE_BANDS:
        raise FamilyProfileError("invalid_age_band")
    circle = await circle_for(session, account_id, create=True)
    assert circle is not None
    await session.execute(
        select(FamilyCircle.id).where(FamilyCircle.id == circle.id).with_for_update()
    )
    profiles = (await session.scalars(
        select(FamilyProfile).where(FamilyProfile.circle_id == circle.id).order_by(FamilyProfile.position)
    )).all()
    if len(profiles) >= MAX_PROFILES:
        raise FamilyProfileError("family_circle_full")
    used = {profile.position for profile in profiles}
    position = next(item for item in range(1, MAX_PROFILES + 1) if item not in used)
    profile = FamilyProfile(
        circle_id=circle.id, position=position, relation=relation, age_band=age_band,
    )
    session.add(profile)
    await session.flush()
    return profile


async def update_profile(
    session: AsyncSession,
    account_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    active: bool | _Unchanged = UNCHANGED,
    age_band: str | _Unchanged = UNCHANGED,
) -> FamilyProfile:
    """Change what this household currently says about one of its members.

    Two different kinds of change arrive here, and they are not governed by the
    same rule.

    Whether somebody is still in the household is a decision about the
    household's shape, and the account holder may not take themselves out of
    it: the ``self`` row is what the circle is anchored to.

    How old somebody is, is a *fact about a person*, and facts go out of date.
    A child turns twelve. Somebody taps the wrong band on the way in. Refusing
    to correct that because the first rule exists is what made a stored
    ``under_12`` permanent — and with it the hand-over to a clinician that band
    is supposed to cause only while it is true. An authority nobody can correct
    stops being an authority and becomes a trap.

    So the ``self`` prohibition is narrowed to what it was actually protecting.
    Its shape is fixed; its facts are not.

    Two ordinary corrections racing each other settle as last-write-wins, which
    is what every other partial update in this application does. There is no
    unique constraint on a band and no read-modify-write to lose — the ORM
    issues an UPDATE for the named columns only, so a request changing
    ``active`` and one changing ``age_band`` at the same moment do not overwrite
    each other. A version column here would be machinery Step 11A does not need.
    """
    if active is UNCHANGED and age_band is UNCHANGED:
        raise FamilyProfileError("no_profile_changes_requested")

    circle = await circle_for(session, account_id)
    if circle is None:
        raise FamilyProfileError("family_profile_not_found")
    profile = await session.scalar(
        select(FamilyProfile).where(FamilyProfile.id == profile_id, FamilyProfile.circle_id == circle.id)
    )
    if profile is None:
        # The circle filter above is the authorisation. A profile in somebody
        # else's household and a profile that never existed both arrive here as
        # ``None``, and both leave as the same answer.
        raise FamilyProfileError("family_profile_not_found")

    if not isinstance(active, _Unchanged):
        if profile.relation == RELATION_SELF:
            raise FamilyProfileError("self_profile_cannot_be_changed")
        profile.active = active

    if not isinstance(age_band, _Unchanged):
        # The database CHECK says the same thing. This says it first, so an
        # in-process caller that skipped the schema gets a domain error rather
        # than an integrity failure that takes the transaction with it.
        if age_band not in AGE_BANDS:
            raise FamilyProfileError("invalid_age_band")
        profile.age_band = age_band

    await session.flush()
    return profile


def family_offer(profile_outcomes: set[str]) -> bool:
    """Offer only after a real per-profile outcome conflict."""
    return len(profile_outcomes) > 1
