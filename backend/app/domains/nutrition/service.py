"""Account-scoped Nutrition preference and suggestion orchestration."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.nutrition.food_options import NUTRITION_FOOD_OPTIONS_VERSION
from app.domains.nutrition.guidance import build_nutrition_guidance, public_nutrition_guidance
from app.domains.nutrition.preferences import diet_label
from app.domains.nutrition.safety import (
    NUTRITION_BOUNDARIES,
    NUTRITION_DISCLAIMER,
    NUTRITION_HYDRATION_NO_TARGET,
)
from app.domains.nutrition.schemas import HydrationPreferencePatch, NutritionPreferencePatch
from app.domains.planning import context as planning_context
from app.domains.routines.models import HydrationPreference, NutritionPreference


async def nutrition_preference(session: AsyncSession, account_id: uuid.UUID) -> NutritionPreference:
    row = (await session.execute(
        select(NutritionPreference).where(NutritionPreference.account_id == account_id)
    )).scalar_one_or_none()
    return row or NutritionPreference(
        account_id=account_id,
        diet="non_vegetarian",
        avoid_foods=[],
        focus_nutrients=[],
        enabled=False,
    )


async def hydration_preference(session: AsyncSession, account_id: uuid.UUID) -> HydrationPreference:
    row = (await session.execute(
        select(HydrationPreference).where(HydrationPreference.account_id == account_id)
    )).scalar_one_or_none()
    return row or HydrationPreference(
        account_id=account_id,
        enabled=False,
        remind_in_hot_weather_only=True,
        note=None,
    )


async def _ensure_nutrition_preference(
    session: AsyncSession, account_id: uuid.UUID
) -> NutritionPreference:
    row = (await session.execute(
        select(NutritionPreference).where(NutritionPreference.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        row = NutritionPreference(account_id=account_id)
        session.add(row)
        await session.flush()
    return row


async def _ensure_hydration_preference(
    session: AsyncSession, account_id: uuid.UUID
) -> HydrationPreference:
    row = (await session.execute(
        select(HydrationPreference).where(HydrationPreference.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        row = HydrationPreference(account_id=account_id)
        session.add(row)
        await session.flush()
    return row


async def patch_nutrition_preference(
    session: AsyncSession, account_id: uuid.UUID, body: NutritionPreferencePatch
) -> dict[str, Any]:
    row = await _ensure_nutrition_preference(session, account_id)
    for field in ("diet", "avoid_foods", "focus_nutrients", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    return serialize_nutrition_preference(row)


async def patch_hydration_preference(
    session: AsyncSession, account_id: uuid.UUID, body: HydrationPreferencePatch
) -> dict[str, Any]:
    row = await _ensure_hydration_preference(session, account_id)
    for field in ("enabled", "remind_in_hot_weather_only", "note"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    return serialize_hydration_preference(row)


def serialize_nutrition_preference(row: NutritionPreference) -> dict[str, Any]:
    return {
        "diet": row.diet, "avoid_foods": list(row.avoid_foods or []),
        "focus_nutrients": list(row.focus_nutrients or []), "enabled": row.enabled,
    }


def serialize_hydration_preference(row: HydrationPreference) -> dict[str, Any]:
    return {
        "enabled": row.enabled,
        "remind_in_hot_weather_only": row.remind_in_hot_weather_only,
        "note": row.note,
        "no_target": NUTRITION_HYDRATION_NO_TARGET,
    }


async def nutrition_suggestions(
    session: AsyncSession, *, account_id: uuid.UUID
) -> dict[str, Any]:
    """Return the frozen, opt-in V3-04.2 food-context contract."""
    preference = await nutrition_preference(session, account_id)
    if not preference.enabled:
        return {
            "enabled": False,
            "suggestions": [],
            "message": "Food suggestions are off. You can turn them on if you want them.",
            "disclaimer": NUTRITION_DISCLAIMER,
        }

    hydration = await hydration_preference(session, account_id)
    day_context = await planning_context.gather(session, account_id=account_id)
    climate = day_context.climate
    guidance = await build_nutrition_guidance(
        session,
        nutrition_enabled=bool(preference.enabled),
        protein_focus="protein" in (preference.focus_nutrients or []),
        diet=preference.diet,
        avoid_foods=tuple(preference.avoid_foods or ()),
        hydration_enabled=bool(hydration.enabled),
        hot_weather=(
            getattr(climate, "temperature_band", None) == "hot"
            or getattr(climate, "moisture_regime", None) == "humid"
            or getattr(climate, "condition", None) in {"hot", "humid"}
        ),
        hot_weather_only=bool(hydration.remind_in_hot_weather_only),
    )
    payload = public_nutrition_guidance(guidance)
    payload.update({
        "enabled": True, "diet": preference.diet, "diet_label": diet_label(preference.diet),
        "food_options_version": NUTRITION_FOOD_OPTIONS_VERSION,
        "food_first": True, "hydration_enabled": hydration.enabled,
        "disclaimer": NUTRITION_DISCLAIMER,
        "boundaries": list(NUTRITION_BOUNDARIES),
    })
    return payload


__all__ = [
    "hydration_preference",
    "nutrition_preference",
    "nutrition_suggestions",
    "patch_hydration_preference",
    "patch_nutrition_preference",
    "serialize_hydration_preference",
    "serialize_nutrition_preference",
]
