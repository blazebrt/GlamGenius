"""Routine, ingredient, perfume, supplement and nutrition routes.

The five appearance modules the brief asks for, behind one flag. Each returns
an explicit "not set up yet" answer rather than an empty body when the user has
not populated it, so the app can hide a module instead of showing an empty one.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.nutrition import service as nutrition_service
from app.domains.nutrition.schemas import HydrationPreferencePatch, NutritionPreferencePatch
from app.domains.planning.models import NotificationDelivery
from app.domains.recommendation.occasions import OCCASION_KEYS
from app.domains.routines import service
from app.domains.routines.schemas import (
    CareExperienceFeedbackInput,
    IngredientCheckRequest,
    IngredientConfirmRequest,
    ObservationInput,
    PerfumeQuery,
    RoutineGenerateRequest,
    RoutineNotificationAction,
    RoutineStepComplete,
)
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_routines"))])


# --- Routines ------------------------------------------------------------------


@router.post("/routines/generate")
async def generate_routines(
    body: RoutineGenerateRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Build routines from the products you already own."""
    result = await service.generate_routines(
        session, account_id=current.account_id, account_id_str=current.account_id_str, body=body,
    )
    await session.commit()
    return result


@router.post("/routines/simplify")
async def simplify_routines(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Apply one explicit, user-authorized Care effort reduction."""
    result = await service.simplify_care_routine(
        session, account_id=current.account_id, account_id_str=current.account_id_str,
    )
    await session.commit()
    return result


@router.post("/routines/products/{item_id}/pause")
async def pause_care_product(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Keep an owned Skin/Hair product while excluding it from Care routines."""
    result = await service.pause_care_product(
        session, account_id=current.account_id, account_id_str=current.account_id_str, item_id=item_id,
    )
    await session.commit()
    return result


@router.post("/routines/products/{item_id}/resume")
async def resume_care_product(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Make an explicitly paused Skin/Hair product eligible for Care again."""
    result = await service.resume_care_product(
        session, account_id=current.account_id, account_id_str=current.account_id_str, item_id=item_id,
    )
    await session.commit()
    return result


@router.post("/routines/products/{item_id}/prefer")
async def prefer_care_product(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Prefer an owned eligible product for its canonical Care step."""
    result = await service.prefer_care_product(
        session, account_id=current.account_id, account_id_str=current.account_id_str, item_id=item_id,
    )
    await session.commit()
    return result


@router.post("/routines/products/{item_id}/unprefer")
async def unprefer_care_product(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Clear an explicit product selection preference."""
    result = await service.unprefer_care_product(
        session, account_id=current.account_id, account_id_str=current.account_id_str, item_id=item_id,
    )
    await session.commit()
    return result


@router.get("/routines/today")
async def routines_today(
    on: date | None = Query(None, description="Defaults to today in your timezone"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Only the routines that are actually relevant right now."""
    return await service.routines_today(session, account_id=current.account_id, on=on)


@router.post("/routines/steps/{step_id}/complete")
async def complete_step(
    step_id: uuid.UUID,
    body: RoutineStepComplete,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Mark a step done for a day. Consistency, not streaks."""
    result = await service.complete_step(
        session, account_id=current.account_id, step_id=step_id, body=body,
    )
    await session.commit()
    return result


@router.post("/routines/notifications/{delivery_id}/action")
async def complete_step_from_notification(
    delivery_id: uuid.UUID,
    body: RoutineNotificationAction,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record a Done / Skip tap without trusting client-owned step details."""
    delivery = await session.get(NotificationDelivery, delivery_id)
    if delivery is None or delivery.account_id != current.account_id:
        raise ValidationFailedError("We could not find that notification.")
    params = delivery.destination_params or {}
    if delivery.source_kind != "routine_due" or params.get("routine_action") != "complete_step":
        raise ValidationFailedError("This notification does not have a routine action.")
    try:
        step_id = uuid.UUID(str(params["step_id"]))
        done_on = date.fromisoformat(str(params["done_on"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailedError("This routine reminder is no longer valid.") from exc
    result = await service.complete_step(
        session, account_id=current.account_id, step_id=step_id,
        body=RoutineStepComplete(done_on=done_on, completed=body.action == "done"),
    )
    await session.commit()
    return result


@router.get("/routines/consistency")
async def routine_consistency(
    days: int = Query(14, ge=1, le=90),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await service.consistency(session, account_id=current.account_id, days=days)


@router.get("/routines/improve")
async def improve_overview(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Everything the You → Improve screen shows, in one call."""
    return await service.improve_overview(session, account_id=current.account_id)


# --- Ingredients ---------------------------------------------------------------


@router.post("/ingredients/check")
async def check_ingredients(
    body: IngredientCheckRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Check a label, a typed list, or products you own against the reviewed rules."""
    return await service.check_ingredients(
        session, account_id=current.account_id, account_id_str=current.account_id_str, body=body,
    )


@router.post("/ingredients/confirm")
async def confirm_ingredients(
    body: IngredientConfirmRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Confirm a low-confidence read so it starts driving the rules."""
    result = await service.confirm_ingredients(session, account_id=current.account_id, body=body)
    await session.commit()
    return result


@router.get("/ingredients/{ingredient_key}")
async def ingredient_detail(
    ingredient_key: str,
    current: CurrentAccount = Depends(get_current_account),
):
    """The reviewed note for one ingredient. About the ingredient, not about you."""
    return service.ingredient_detail(ingredient_key)


# --- Perfume --------------------------------------------------------------------


@router.get("/perfume/recommendation")
async def perfume_recommendation(
    occasion_key: str | None = Query(None, max_length=32),
    weather: str | None = Query(None, max_length=24),
    time_of_day: str | None = Query(None, max_length=24),
    season: str | None = Query(None, max_length=24),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Which of the perfumes you own suits today."""
    if occasion_key and occasion_key not in OCCASION_KEYS:
        raise ValidationFailedError(
            f"'{occasion_key}' is not an occasion we know.", field="occasion_key",
        )
    query = PerfumeQuery(
        occasion_key=occasion_key, weather=weather, time_of_day=time_of_day, season=season,
    )
    return await service.perfume_recommendation(
        session, account_id=current.account_id,
        occasion_key=query.occasion_key, weather=query.weather,
        time_of_day=query.time_of_day, season=query.season,
    )


# --- Nutrition and hydration ---------------------------------------------------------


@router.get("/nutrition/appearance-suggestions")
async def nutrition_suggestions(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Appearance-related food context, filtered to what you eat."""
    return await nutrition_service.nutrition_suggestions(session, account_id=current.account_id)


@router.get("/nutrition/preferences")
async def get_nutrition_preferences(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await nutrition_service.nutrition_preference(session, current.account_id)
    await session.commit()
    return nutrition_service.serialize_nutrition_preference(row)


@router.patch("/nutrition/preferences")
async def patch_nutrition_preferences(
    body: NutritionPreferencePatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await nutrition_service.patch_nutrition_preference(session, current.account_id, body)
    await session.commit()
    return result


@router.get("/nutrition/hydration")
async def get_hydration_preferences(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await nutrition_service.hydration_preference(session, current.account_id)
    await session.commit()
    return nutrition_service.serialize_hydration_preference(row)


@router.patch("/nutrition/hydration")
async def patch_hydration_preferences(
    body: HydrationPreferencePatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await nutrition_service.patch_hydration_preference(session, current.account_id, body)
    await session.commit()
    return result


# --- Observations ---------------------------------------------------------------------


@router.post("/routines/observations")
async def record_observation(
    body: ObservationInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Save something you noticed, in your own words. We never interpret it."""
    result = await service.record_observation(session, account_id=current.account_id, body=body)
    await session.commit()
    return result


@router.get("/routines/observations")
async def list_observations(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_observations(session, account_id=current.account_id)


# --- Explicit Care experience feedback -----------------------------------------


@router.post("/routines/experience-feedback")
async def record_care_experience_feedback(
    body: CareExperienceFeedbackInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await service.record_care_experience_feedback(
        session, account_id=current.account_id, body=body,
    )
    await session.commit()
    return result


@router.get("/routines/experience-feedback")
async def list_care_experience_feedback(
    subject_type: str | None = Query(None, max_length=16),
    subject_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await service.list_care_experience_feedback(
        session, account_id=current.account_id, subject_type=subject_type,
        subject_id=subject_id, limit=limit,
    )


@router.delete("/routines/experience-feedback/{feedback_id}")
async def delete_care_experience_feedback(
    feedback_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await service.delete_care_experience_feedback(
        session, account_id=current.account_id, feedback_id=feedback_id,
    )
    await session.commit()
    return result
