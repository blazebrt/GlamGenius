"""The Monday-to-Sunday planner.

Built day by day on top of the same compiler Today uses, in date order, so each
day sees what the days before it are already using. That ordering is the whole
trick: plan Monday through Sunday independently and you get the same good shirt
on four days, because each day is individually right.

Locked days are never touched. A user who has decided about Friday has decided,
and regenerating the week must not quietly overrule them.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock, compiler
from app.domains.planning import context as context_stage
from app.domains.planning.models import (
    PLANNER_VERSION, DailyPlan, OutfitSchedule, WeeklyPlan, WeeklyPlanDay,
)
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError


async def owned_week(session: AsyncSession, account_id: uuid.UUID, week_start: date) -> Optional[WeeklyPlan]:
    return (await session.execute(
        select(WeeklyPlan).where(
            WeeklyPlan.account_id == account_id, WeeklyPlan.week_start == week_start
        )
    )).scalar_one_or_none()


async def get_or_create_week(
    session: AsyncSession, account_id: uuid.UUID, week_start: date, timezone_name: str, repetition_window_days: int = 7
) -> WeeklyPlan:
    plan = await owned_week(session, account_id, week_start)
    if plan is None:
        plan = WeeklyPlan(
            account_id=account_id, week_start=week_start, timezone_name=timezone_name,
            repetition_window_days=repetition_window_days, engine_version=PLANNER_VERSION,
        )
        session.add(plan)
        await session.flush()
    return plan


async def _day_rows(session: AsyncSession, plan: WeeklyPlan) -> Dict[date, WeeklyPlanDay]:
    rows = (await session.execute(
        select(WeeklyPlanDay).where(WeeklyPlanDay.weekly_plan_id == plan.id)
    )).scalars().all()
    return {row.plan_date: row for row in rows}


async def generate(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    week_start: date,
    timezone_name: str,
    repetition_window_days: int = 7,
    regenerate_locked: bool = False,
) -> WeeklyPlan:
    """Build or rebuild a week, in date order, respecting locks."""
    plan = await get_or_create_week(session, account_id, week_start, timezone_name, repetition_window_days)
    plan.repetition_window_days = repetition_window_days
    existing = await _day_rows(session, plan)

    for position, plan_date in enumerate(clock.week_dates(week_start)):
        row = existing.get(plan_date)
        if row is not None and row.locked and not regenerate_locked:
            continue

        context = await context_stage.gather(
            session, account_id=account_id, plan_date=plan_date,
            timezone_name=timezone_name, repetition_window_days=repetition_window_days,
        )
        daily, _ = await compiler.compile_day(session, context=context, force=True, trigger="weekly_generate")

        if row is None:
            row = WeeklyPlanDay(
                weekly_plan_id=plan.id, plan_date=plan_date,
                daily_plan_id=daily.id, position=position,
            )
            session.add(row)
        else:
            row.daily_plan_id = daily.id
            row.position = position
        # Flush each day before building the next: the following day's
        # repetition check reads outfit_schedule, which this write populates.
        await session.flush()

    plan.version += 1
    plan.generated_at = utcnow()
    plan.status = "ready"
    await session.flush()
    return plan


async def set_lock(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date, locked: bool, timezone_name: str
) -> WeeklyPlanDay:
    start = clock.week_start(plan_date)
    plan = await owned_week(session, account_id, start)
    if plan is None:
        raise NotFoundError("There is no plan for that week yet. Generate one first.")
    row = (await session.execute(
        select(WeeklyPlanDay).where(
            WeeklyPlanDay.weekly_plan_id == plan.id, WeeklyPlanDay.plan_date == plan_date
        )
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("That day is not part of the generated week.")
    row.locked = locked
    if row.daily_plan_id:
        daily = await session.get(DailyPlan, row.daily_plan_id)
        if daily is not None and daily.account_id == account_id:
            daily.locked = locked
    await session.flush()
    return row


async def swap_days(
    session: AsyncSession, *, account_id: uuid.UUID, first: date, second: date
) -> WeeklyPlan:
    """Move one day's **outfit** to another day, and vice versa.

    Only the outfit moves. Swapping the whole ``daily_plan_id`` would drag each
    day's date-specific facts along with it — the weather, the calendar event,
    the headline — so a Monday/Friday swap would show Friday's rain and Friday's
    meeting on Monday, and ``/today?plan_date=`` would disagree with the
    planner. Each ``DailyPlan`` stays attached to its own date; the ``look_id``
    is what changes hands.

    A locked day cannot be swapped onto, because that is the same as
    overwriting it.
    """
    if first == second:
        raise ValidationFailedError("Those are the same day.", field="swap_with_date")
    start = clock.week_start(first)
    if clock.week_start(second) != start:
        raise ValidationFailedError("You can only swap days inside the same week.", field="swap_with_date")

    plan = await owned_week(session, account_id, start)
    if plan is None:
        raise NotFoundError("There is no plan for that week yet. Generate one first.")

    rows = await _day_rows(session, plan)
    left, right = rows.get(first), rows.get(second)
    if left is None or right is None:
        raise NotFoundError("Both days must be part of the generated week.")
    if left.locked or right.locked:
        raise ValidationFailedError(
            "One of those days is locked. Unlock it first if you want to move it.", field="swap_with_date"
        )

    left_plan = await session.get(DailyPlan, left.daily_plan_id) if left.daily_plan_id else None
    right_plan = await session.get(DailyPlan, right.daily_plan_id) if right.daily_plan_id else None
    if left_plan is None or right_plan is None:
        raise ValidationFailedError(
            "Both days need a plan before they can be swapped. Generate the week first.",
            field="swap_with_date",
        )
    if left_plan.account_id != account_id or right_plan.account_id != account_id:
        raise NotFoundError("We could not find those days.")

    # Only the outfit changes hands. Each plan keeps its own date, weather and
    # event.
    left_plan.look_id, right_plan.look_id = right_plan.look_id, left_plan.look_id
    for row in (left_plan, right_plan):
        row.version += 1
        # Pin to the current context so the next Today open is a cache hit and
        # the user's arrangement survives, rather than being rebuilt away.
        row.cache_key = await _pinned_cache_key(session, account_id, row)

    await _swap_schedule(session, account_id, first, second)
    plan.version += 1
    await session.flush()
    return plan


async def _pinned_cache_key(session: AsyncSession, account_id: uuid.UUID, plan: DailyPlan) -> str:
    """The cache key for this plan's day as context stands right now."""
    context = await context_stage.gather(
        session, account_id=account_id, plan_date=plan.plan_date,
        timezone_name=plan.timezone_name,
    )
    return context_stage.cache_key(context)


async def _swap_schedule(session: AsyncSession, account_id: uuid.UUID, first: date, second: date) -> None:
    rows = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == account_id,
            OutfitSchedule.plan_date.in_([first, second]),
        )
    )).scalars().all()
    by_date = {row.plan_date: row for row in rows}
    left, right = by_date.get(first), by_date.get(second)
    if left is None or right is None:
        return
    # Worn history is a fact about a day that already happened; never move it.
    if left.status == "worn" or right.status == "worn":
        return
    left.look_id, right.look_id = right.look_id, left.look_id
    left.item_ids, right.item_ids = right.item_ids, left.item_ids


def repetition_report(days: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which items appear more than once across the week.

    Surfaced rather than silently prevented: wearing the same trousers twice in
    a week is completely normal, and the planner's job is to make it visible,
    not to forbid it.
    """
    seen: Dict[str, List[str]] = {}
    for day in days:
        for item in day.get("owned_items", []):
            seen.setdefault(item["display_name"], []).append(day["plan_date"])
    repeated = {name: dates for name, dates in seen.items() if len(dates) > 1}
    return {
        "repeated_items": [
            {"display_name": name, "dates": dates, "times": len(dates)}
            for name, dates in sorted(repeated.items(), key=lambda pair: -len(pair[1]))
        ],
        "note": "Repeating something is normal. This is here so you can see it, not so you avoid it.",
    }
