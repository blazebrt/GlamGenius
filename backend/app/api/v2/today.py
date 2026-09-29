"""The Today engine.

``GET /today`` is the hot path. It gathers context, hashes it, and returns the
stored plan untouched when the hash still matches — so the common case is a
handful of indexed reads and no recomputation, let alone an AI call.

This router is retired and is not mounted in production. Notification
settings and device registration, which kept their ``/today/`` paths, are
not here: they are production routes in ``notification_settings``.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import agenda, clock, compiler, service
from app.domains.planning import context as context_stage
from app.domains.planning.models import DailyPlan
from app.domains.planning.schemas import (
    ActionComplete,
    AirQualityInput,
    CalendarEventInput,
    ClarificationAnswer,
    ItemUnavailable,
    TodayFeedback,
    TodayOutfitSwap,
    TodayRegenerate,
    WeatherInput,
)
from app.domains.recommendation import orchestrator as recommendation_orchestrator
from app.domains.recommendation import service as recommendation_service
from app.domains.recommendation.schemas import LookSwapItem
from app.shared.database.base import utcnow
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_today"))])


async def _plan_for(
    session: AsyncSession, current: CurrentAccount, plan_date: date | None, *, force: bool = False, trigger: str = "requested"
) -> DailyPlan:
    context = await context_stage.gather(session, account_id=current.account_id, plan_date=plan_date)
    plan, _ = await compiler.compile_day(session, context=context, force=force, trigger=trigger)
    return plan


@router.get("/today")
async def get_today(
    plan_date: date | None = Query(None, description="Defaults to today in your timezone"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What to wear and do today. Served from cache unless something changed."""
    plan = await _plan_for(session, current, plan_date)
    await session.commit()
    body = await service.serialize_plan(session, plan)
    body["recalculations"] = await service.recalculation_history(session, current.account_id, plan.plan_date)
    return body


@router.post("/today/regenerate")
async def regenerate_today(
    body: TodayRegenerate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Rebuild the day on purpose, even if nothing about the context moved."""
    plan = await _plan_for(
        session, current, body.plan_date, force=True, trigger=body.reason or "manual"
    )
    await session.commit()
    return await service.serialize_plan(session, plan)


@router.post("/today/actions/{action_id}/complete")
async def complete_action(
    action_id: uuid.UUID,
    body: ActionComplete,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await service.owned_action(session, current.account_id, action_id)
    row.completed_at = utcnow() if body.completed else None
    await session.flush()
    plan = await session.get(DailyPlan, row.plan_id)
    await session.commit()
    return await service.serialize_plan(session, plan)


@router.post("/today/outfit/swap")
async def swap_today_item(
    body: TodayOutfitSwap,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Change one piece without rebuilding the day.

    Reuses the Phase 4 swap, which already verifies that the replacement is
    owned, active, confirmed and in the right category.
    """
    plan_date = body.plan_date or clock.local_today(await context_stage.resolve_timezone_for(session, current.account_id))
    plan = await service.owned_plan(session, current.account_id, plan_date)
    if plan.look_id is None:
        raise ValidationFailedError("There is no outfit on that day to change yet.", field="plan_date")

    look = await recommendation_service.owned_look(session, current.account_id, plan.look_id)
    await recommendation_orchestrator.swap_item(
        session, look=look,
        body=LookSwapItem(slot=body.slot, from_item_id=body.from_item_id, to_item_id=body.to_item_id, note=body.note),
    )
    # Re-key the plan against the *current* context rather than invalidating it.
    # Re-opening Today is then a cache hit and the user's own choice survives;
    # a genuine change (rain, a new meeting) still moves the hash and rebuilds.
    context = await context_stage.gather(session, account_id=current.account_id, plan_date=plan_date)
    material = await compiler.build_day_care_material(session, context)
    plan.cache_key = compiler.material_cache_key(context, material)
    plan.version += 1
    await service.mark_worn(session, current.account_id, plan_date, worn=False)
    # The schedule feeds repetition history, so it has to follow the swap.
    await service.sync_schedule_items(session, current.account_id, plan_date, plan.look_id)
    await session.flush()
    await session.commit()
    return await service.serialize_plan(session, plan)


@router.post("/today/feedback")
async def send_today_feedback(
    body: TodayFeedback,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Tell us how it went. ``wore_it`` is what teaches the repetition rules."""
    plan_date = body.plan_date or clock.local_today(await context_stage.resolve_timezone_for(session, current.account_id))
    plan = await service.owned_plan(session, current.account_id, plan_date)

    if body.rating in ("wore_it", "loved"):
        await service.mark_worn(session, current.account_id, plan_date, worn=True)
    if plan.look_id:
        look = await recommendation_service.owned_look(session, current.account_id, plan.look_id)
        mapped = {"wore_it": "worn", "loved": "loved", "not_for_me": "not_for_me", "changed_it": "saved"}
        await recommendation_service.save_feedback(
            session, look, rating=mapped[body.rating], reason=body.reason, note=body.note, worn_on=plan_date,
        )
    await session.commit()
    return await service.serialize_plan(session, plan)


@router.post("/today/clarify")
async def answer_clarification(
    body: ClarificationAnswer,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Answer the one question the plan asked, then rebuild with the answer."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    plan_date = body.plan_date or clock.local_today(timezone_name)
    plan = await service.owned_plan(session, current.account_id, plan_date)
    if not plan.needs_clarification or not plan.clarification:
        raise ValidationFailedError("There is no open question for that day.", field="question_key")
    if plan.clarification.get("key") != body.question_key:
        raise ValidationFailedError("That is not the question we asked.", field="question_key")

    if body.question_key == "weather":
        await service.record_weather(session, current.account_id, WeatherInput(for_date=plan_date, condition=body.answer))
    elif body.question_key in ("occasion_key", "dress_code"):
        events = await context_stage.day_events(session, current.account_id, plan_date, timezone_name)
        if not events:
            raise ValidationFailedError("There is no event on that day to correct.", field="question_key")
        target = events[0]
        row = await service.owned_event(session, current.account_id, target.id)
        if body.question_key == "occasion_key":
            row.occasion_key = body.answer
            row.user_confirmed = True
            row.inference_confidence = 1.0
        else:
            row.dress_code_hint = body.answer
        await session.flush()

    plan = await _plan_for(session, current, plan_date, force=True, trigger="clarification_answered")
    await session.commit()
    return await service.serialize_plan(session, plan)


@router.post("/today/items/unavailable")
async def report_item_unavailable(
    body: ItemUnavailable,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """"I can't wear that today." Rebuilds the day without it."""
    await service.set_item_state(
        session, current.account_id, body.item_id, body.state, body.available_from, body.note
    )
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    plan = await _plan_for(session, current, clock.local_today(timezone_name), force=True, trigger="item_unavailable")
    await session.commit()
    return await service.serialize_plan(session, plan)


# --- Supporting context the user can supply themselves ----------------------


@router.post("/today/weather")
async def set_weather(
    body: WeatherInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record the weather yourself. No outside service required."""
    row = await service.record_weather(session, current.account_id, body)
    plan = await _plan_for(session, current, body.for_date, trigger="weather_changed")
    await session.commit()
    return {"weather": service.serialize_weather(row), "plan": await service.serialize_plan(session, plan)}


@router.post("/today/air-quality")
async def set_air_quality(
    body: AirQualityInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record the air quality yourself. No outside service required."""
    row = await service.record_air_quality(session, current.account_id, body)
    plan = await _plan_for(session, current, body.for_date, trigger="air_quality_changed")
    await session.commit()
    return {"air_quality": service.serialize_air_quality(row), "plan": await service.serialize_plan(session, plan)}


@router.post("/today/events")
async def add_event(
    body: CalendarEventInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Add a commitment by hand. Duplicates are absorbed, not stacked."""
    row, created = await service.upsert_event(session, current.account_id, body)
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    local_date = clock.local_now(timezone_name, moment=row.starts_at).date()
    plan = await _plan_for(session, current, local_date, trigger="calendar_changed")
    await session.commit()
    return {
        "event": service.serialize_event(row, timezone_name),
        "created": created,
        "plan": await service.serialize_plan(session, plan),
    }


@router.get("/today/agenda")
async def get_today_agenda(
    plan_date: date | None = Query(None, description="Defaults to today in your timezone"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read the thin cross-domain attention view without compiling Today."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    return await agenda.agenda_payload(
        session, current.account_id, generated_for=plan_date, timezone_name=timezone_name,
    )
