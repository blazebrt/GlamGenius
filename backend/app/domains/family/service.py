"""Account-local Family Circle operations and offer policy."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.models import FamilyCircle, FamilyProfile

MAX_PROFILES = 8


class FamilyProfileError(ValueError):
    pass


def profile_label(profile: FamilyProfile) -> str:
    if profile.relation == "self":
        return "You"
    return f"{profile.relation.capitalize()} profile {profile.position}"


def serialise_profile(profile: FamilyProfile) -> dict[str, object]:
    return {"id": profile.id, "position": profile.position, "relation": profile.relation,
            "label": profile_label(profile), "active": profile.active}


async def circle_for(session: AsyncSession, account_id: uuid.UUID, *, create: bool = False) -> FamilyCircle | None:
    circle = await session.scalar(
        select(FamilyCircle).where(FamilyCircle.account_id == account_id)
    )
    if circle is not None or not create:
        return circle
    circle = FamilyCircle(account_id=account_id)
    session.add(circle)
    await session.flush()
    session.add(FamilyProfile(circle_id=circle.id, position=1, relation="self"))
    await session.flush()
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


async def add_profile(session: AsyncSession, account_id: uuid.UUID, *, relation: str) -> FamilyProfile:
    circle = await circle_for(session, account_id, create=True)
    assert circle is not None
    profiles = (await session.scalars(
        select(FamilyProfile).where(FamilyProfile.circle_id == circle.id).order_by(FamilyProfile.position)
    )).all()
    if len(profiles) >= MAX_PROFILES:
        raise FamilyProfileError("family_circle_full")
    used = {profile.position for profile in profiles}
    position = next(item for item in range(1, MAX_PROFILES + 1) if item not in used)
    profile = FamilyProfile(circle_id=circle.id, position=position, relation=relation)
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
