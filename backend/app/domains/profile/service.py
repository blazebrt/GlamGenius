"""Profile creation, precedence, projections and version history."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile.models import (
    AppearanceGoal, AppearanceProfile, FitPreference, LifestyleContext,
    ProfileAttribute, ProfileChangeEvent, StylePreference, UserConstraint,
)
from app.domains.profile.registry import ATTRIBUTE_REGISTRY, validate_attribute
from app.shared.database.base import utcnow

STYLE_KEYS = {
    "preferred_style", "favourite_colours", "disliked_colours", "brand_preferences",
    "formality_preference", "style_experimentation", "indian_categories",
    "western_categories", "fusion_categories",
}
FIT_KEYS = {
    "height_cm", "usual_top_size", "usual_bottom_size", "shoulder_fit", "waist_fit",
    "hip_fit", "preferred_coverage", "sleeve_preferences", "neckline_preferences",
    "footwear_comfort", "silhouettes_liked", "silhouettes_avoided",
}
LIFESTYLE_KEYS = {
    "city", "climate", "work_context", "routine", "activity_level",
    "travel_frequency", "budget", "calendar_patterns",
}


async def get_or_create_profile(session: AsyncSession, account_id: uuid.UUID) -> AppearanceProfile:
    row = (await session.execute(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))).scalar_one_or_none()
    if row is None:
        row = AppearanceProfile(account_id=account_id)
        session.add(row)
        await session.flush()
    return row


async def attributes_for(session: AsyncSession, profile_id: uuid.UUID) -> List[ProfileAttribute]:
    stmt = select(ProfileAttribute).where(ProfileAttribute.profile_id == profile_id).order_by(ProfileAttribute.key)
    return list((await session.execute(stmt)).scalars().all())


async def attribute_map(session: AsyncSession, profile_id: uuid.UUID) -> Dict[str, ProfileAttribute]:
    return {row.key: row for row in await attributes_for(session, profile_id)}


async def _projection(session: AsyncSession, model, profile_id: uuid.UUID):
    row = (await session.execute(select(model).where(model.profile_id == profile_id))).scalar_one_or_none()
    if row is None:
        row = model(profile_id=profile_id)
        session.add(row)
        await session.flush()
    return row


async def sync_projections(session: AsyncSession, profile_id: uuid.UUID, values: Dict[str, Any]) -> None:
    if STYLE_KEYS & values.keys():
        row = await _projection(session, StylePreference, profile_id)
        for key in STYLE_KEYS & values.keys():
            setattr(row, key, values[key])
    if FIT_KEYS & values.keys():
        row = await _projection(session, FitPreference, profile_id)
        for key in FIT_KEYS & values.keys():
            setattr(row, key, values[key])
    if LIFESTYLE_KEYS & values.keys():
        row = await _projection(session, LifestyleContext, profile_id)
        for key in LIFESTYLE_KEYS & values.keys():
            setattr(row, key, values[key])
    if {"current_goal", "appearance_goals"} & values.keys():
        await session.execute(delete(AppearanceGoal).where(AppearanceGoal.profile_id == profile_id))
        goals = []
        if values.get("current_goal"):
            goals.append(values["current_goal"])
        goals.extend(values.get("appearance_goals") or [])
        for priority, goal in enumerate(dict.fromkeys(goals), start=1):
            session.add(AppearanceGoal(profile_id=profile_id, goal=goal, priority=priority))
    if "allergies" in values:
        await session.execute(delete(UserConstraint).where(UserConstraint.profile_id == profile_id, UserConstraint.kind == "allergy"))
        for allergy in values["allergies"]:
            session.add(UserConstraint(profile_id=profile_id, kind="allergy", value=allergy))


async def apply_attributes(
    session: AsyncSession,
    profile: AppearanceProfile,
    updates: Iterable[Dict[str, Any]],
    *,
    source: str = "user_declared",
    confidence: float = 1.0,
    verification_state: str = "confirmed",
    source_ai_run_id: uuid.UUID | None = None,
    reason: str = "user_update",
) -> List[ProfileAttribute]:
    """Apply explicit changes and record one immutable profile version.

    AI callers may add observations elsewhere, but cannot call this silently.
    This function is used by user PATCH, onboarding answers and explicit
    observation confirmation only.
    """
    existing = await attribute_map(session, profile.id)
    cleaned: Dict[str, Any] = {}
    changed: List[tuple[ProfileAttribute, Any]] = []
    for item in updates:
        key = item["key"]
        value = validate_attribute(key, item["value"])
        cleaned[key] = value
        row = existing.get(key)
        old_value = row.value if row else None
        old_source = row.source if row else None
        if row is None:
            row = ProfileAttribute(profile_id=profile.id, key=key, value=value, source=source, confidence=confidence, verification_state=verification_state, source_ai_run_id=source_ai_run_id, last_reviewed_at=utcnow())
            session.add(row)
            existing[key] = row
        else:
            # This is never a silent AI write: callers are user actions.
            row.value = value
            row.source = source
            row.confidence = confidence
            row.verification_state = verification_state
            row.source_ai_run_id = source_ai_run_id
            row.last_reviewed_at = utcnow()
        if old_value != value or old_source != source:
            changed.append((row, old_value))

    if not changed:
        return []
    profile.version += 1
    profile.last_reviewed_at = utcnow()
    # Assign explicitly so SQLAlchemy does not need an implicit async refresh
    # for the server-side on-update value while serialising the response.
    profile.updated_at = profile.last_reviewed_at
    projection_values = dict(cleaned)
    if {"current_goal", "appearance_goals"} & cleaned.keys():
        # Rebuilding the goal projection must retain the other goal field when
        # only one of the two authoritative attributes changed.
        for key in ("current_goal", "appearance_goals"):
            if key in existing:
                projection_values[key] = existing[key].value
    await sync_projections(session, profile.id, projection_values)
    for row, old_value in changed:
        session.add(ProfileChangeEvent(
            profile_id=profile.id, profile_version=profile.version,
            attribute_key=row.key, old_value=old_value, new_value=row.value,
            source=source, reason=reason,
        ))
    await session.flush()
    return [row for row, _ in changed]


def readiness(rows: Iterable[ProfileAttribute]) -> List[Dict[str, Any]]:
    values = {row.key: row.value for row in rows if row.verification_state == "confirmed"}
    colour_ready = bool(values.get("skin_tone") and values.get("undertone")) or bool(values.get("favourite_colours"))
    outfit_ready = bool(values.get("current_goal") and values.get("preferred_style"))
    fit_ready = bool(values.get("usual_top_size") or values.get("usual_bottom_size"))
    return [
        {"area": "colours", "ready": colour_ready, "message": "Ready for colour recommendations" if colour_ready else "Confirm your colours or add favourites"},
        {"area": "everyday_outfits", "ready": outfit_ready, "message": "Ready for everyday outfits" if outfit_ready else "Add your goal and preferred style"},
        {"area": "fit", "ready": fit_ready, "message": "Ready for fit guidance" if fit_ready else "Add sizes for better fit guidance"},
        {"area": "planning", "ready": False, "message": "Connect calendar later for automatic planning"},
    ]


def serialize_attribute(row: ProfileAttribute) -> Dict[str, Any]:
    spec = ATTRIBUTE_REGISTRY[row.key]
    return {
        "id": str(row.id), "key": row.key, "label": spec.label, "section": spec.section,
        "value": row.value, "source": row.source, "confidence": row.confidence,
        "verification_state": row.verification_state,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        "review_due_at": row.review_due_at.isoformat() if row.review_due_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "source_ai_run_id": str(row.source_ai_run_id) if row.source_ai_run_id else None,
    }


async def serialize_profile(session: AsyncSession, profile: AppearanceProfile) -> Dict[str, Any]:
    rows = await attributes_for(session, profile.id)
    return {
        "id": str(profile.id), "version": profile.version,
        "baseline_status": profile.baseline_status,
        "attributes": [serialize_attribute(row) for row in rows],
        "readiness": readiness(rows),
        "weight_required": False,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


async def change_history(session: AsyncSession, profile_id: uuid.UUID) -> List[Dict[str, Any]]:
    rows = (await session.execute(select(ProfileChangeEvent).where(ProfileChangeEvent.profile_id == profile_id).order_by(ProfileChangeEvent.created_at.desc()))).scalars().all()
    return [{"id": str(r.id), "profile_version": r.profile_version, "attribute_key": r.attribute_key, "old_value": r.old_value, "new_value": r.new_value, "source": r.source, "reason": r.reason, "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
