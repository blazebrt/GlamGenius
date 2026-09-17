"""Account-local Family Circle operations and offer policy."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle, FamilyProfile
from app.domains.family.subject import AGE_BAND_NOT_STATED, AGE_BANDS

MAX_PROFILES = 8


class FamilyProfileError(ValueError):
    pass


def profile_label(profile: FamilyProfile) -> str:
    if profile.relation == "self":
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
    try:
        async with session.begin_nested():
            circle = FamilyCircle(account_id=account_id)
            session.add(circle)
            await session.flush()
            session.add(FamilyProfile(circle_id=circle.id, position=1, relation="self"))
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


async def set_profile_active(session: AsyncSession, account_id: uuid.UUID, profile_id: uuid.UUID, *, active: bool) -> FamilyProfile:
    circle = await circle_for(session, account_id)
    if circle is None:
        raise FamilyProfileError("family_profile_not_found")
    profile = await session.scalar(
        select(FamilyProfile).where(FamilyProfile.id == profile_id, FamilyProfile.circle_id == circle.id)
    )
    if profile is None:
        raise FamilyProfileError("family_profile_not_found")
    if profile.relation == "self":
        raise FamilyProfileError("self_profile_cannot_be_changed")
    profile.active = active
    await session.flush()
    return profile


def family_offer(profile_outcomes: set[str]) -> bool:
    """Offer only after a real per-profile outcome conflict."""
    return len(profile_outcomes) > 1
