"""The Monday-to-Sunday planner."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock, compiler, event_ready, service, weekly
from app.domains.planning import context as context_stage
from app.domains.planning.schemas import DayLock, DayPatch, EventReadyActionComplete, EventReadyLookPatch, WeekGenerate
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_planner"))])


@router.get("/planner/events/upcoming")
async def get_upcoming_events(
    days: int = Query(default=90, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=50),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """List active CalendarEvents without generating any planning state."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    events = await service.upcoming_events(
        session, current.account_id, timezone_name, days=days, limit=limit,
    )
    return {
        "timezone": timezone_name,
        "events": [service.serialize_event(event, timezone_name) for event in events],
    }


@router.post("/planner/events/{event_id}/ready/generate")
async def generate_event_ready(
    event_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await event_ready.generate(session, current.account_id, event_id)
    await session.commit()
    return result


@router.get("/planner/events/{event_id}/ready")
async def get_event_ready(
    event_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await event_ready.read(session, current.account_id, event_id)


@router.patch("/planner/events/{event_id}/ready/look")
async def set_event_ready_look(
    event_id: uuid.UUID,
    body: EventReadyLookPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await event_ready.set_look(session, current.account_id, event_id, body.look_id)
    await session.commit()
    return result


@router.post("/planner/events/{event_id}/ready/actions/{action_id}/complete")
async def complete_event_ready_action(
    event_id: uuid.UUID,
    action_id: uuid.UUID,
    body: EventReadyActionComplete,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await event_ready.complete_action(session, current.account_id, event_id, action_id, body.completed)
    await session.commit()
    return result


@router.get("/planner/week")
async def get_week(
    week_start: date | None = Query(None, description="Any date in the week; the Monday is used"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The current week, or the week containing the date you asked for."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    start = clock.week_start(week_start or clock.local_today(timezone_name))
    plan = await weekly.owned_week(session, current.account_id, start)
    if plan is None:
        return {
            "week_start": start.isoformat(),
            "status": "not_generated",
            "days": [
                {"plan_date": value.isoformat(), "weekday": value.strftime("%A"), "status": "empty", "locked": False, "owned_items": []}
                for value in clock.week_dates(start)
            ],
            "message": "No plan for this week yet. Generate one to see all seven days.",
        }
    return await service.serialize_week(session, plan)


@router.post("/planner/week/generate")
async def generate_week(
    body: WeekGenerate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    start = clock.week_start(body.week_start or clock.local_today(timezone_name))
    plan = await weekly.generate(
        session,
        account_id=current.account_id,
        week_start=start,
        timezone_name=timezone_name,
        repetition_window_days=body.repetition_window_days,
        regenerate_locked=body.regenerate_locked,
    )
    await session.commit()
    return await service.serialize_week(session, plan)


@router.patch("/planner/day/{plan_date}")
async def patch_day(
    plan_date: date,
    body: DayPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Move a day, regenerate a day, or leave a note on one."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    start = clock.week_start(plan_date)
    week = await weekly.owned_week(session, current.account_id, start)
    if week is None:
        raise NotFoundError("There is no plan for that week yet. Generate one first.")

    if body.swap_with_date is not None:
        await weekly.swap_days(session, account_id=current.account_id, first=plan_date, second=body.swap_with_date)

    if body.regenerate:
        day = await service.owned_plan(session, current.account_id, plan_date) if body.look_id is None else None
        if day is not None and day.locked:
            raise ValidationFailedError("That day is locked. Unlock it before regenerating.", field="regenerate")
        context = await context_stage.gather(
            session, account_id=current.account_id, plan_date=plan_date,
            timezone_name=timezone_name, repetition_window_days=week.repetition_window_days,
        )
        await compiler.compile_day(session, context=context, force=True, trigger="day_regenerate")

    if body.note is not None:
        from sqlalchemy import select

        from app.domains.planning.models import WeeklyPlanDay

        row = (await session.execute(
            select(WeeklyPlanDay).where(
                WeeklyPlanDay.weekly_plan_id == week.id, WeeklyPlanDay.plan_date == plan_date
            )
        )).scalar_one_or_none()
        if row is None:
            raise NotFoundError("That day is not part of the generated week.")
        row.note = body.note

    await session.commit()
    return await service.serialize_week(session, week)


@router.post("/planner/day/{plan_date}/lock")
async def lock_day(
    plan_date: date,
    body: DayLock,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Lock a day so regenerating the week leaves it alone."""
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    await weekly.set_lock(
        session, account_id=current.account_id, plan_date=plan_date,
        locked=body.locked, timezone_name=timezone_name,
    )
    week = await weekly.owned_week(session, current.account_id, clock.week_start(plan_date))
    await session.commit()
    return await service.serialize_week(session, week)
