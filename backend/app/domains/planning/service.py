"""Ownership, persistence and serialisation for Today and the planner.

Same two rules as every other V2 domain: ownership is checked rather than
assumed, and an owned item in a response is always a real row. ``serialize_plan``
resolves the day's look through the Phase 4 serialiser, which already re-resolves
every item against the account's active inventory — so an item archived after
the plan was built cannot keep appearing on Today.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.planning.models import (
    LAUNDRY_STATES,
    CalendarEvent,
    DailyPlan,
    DailyPlanAction,
    ExternalIntegration,
    LaundryStateEvent,
    OutfitSchedule,
    PlanRecalculationEvent,
    WeatherSnapshot,
    AirQualitySnapshot,
    WeeklyPlan,
    WeeklyPlanDay,
)
from app.domains.planning.providers import KNOWN_CALENDAR_PROVIDERS, PROVIDER_MANUAL, catalogue
from app.domains.planning.schemas import CalendarEventInput, WeatherInput, AirQualityInput
from app.domains.recommendation import service as recommendation_service
from app.domains.recommendation.models import Look
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

INTEGRATION_CALENDAR = "calendar"
INTEGRATION_WEATHER = "weather"


# --- Ownership --------------------------------------------------------------


async def owned_plan(session: AsyncSession, account_id: uuid.UUID, plan_date: date) -> DailyPlan:
    plan = (await session.execute(
        select(DailyPlan).where(DailyPlan.account_id == account_id, DailyPlan.plan_date == plan_date)
    )).scalar_one_or_none()
    if plan is None:
        raise NotFoundError("We could not find a plan for that day.")
    return plan


async def owned_action(session: AsyncSession, account_id: uuid.UUID, action_id: uuid.UUID) -> DailyPlanAction:
    row = (await session.execute(
        select(DailyPlanAction)
        .join(DailyPlan, DailyPlan.id == DailyPlanAction.plan_id)
        .where(DailyPlanAction.id == action_id, DailyPlan.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that item on your plan.")
    return row


async def owned_item(session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID) -> InventoryItem:
    row = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.id == item_id,
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
        )
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that item in your inventory.")
    return row


# --- Weather ----------------------------------------------------------------


async def record_weather(session: AsyncSession, account_id: uuid.UUID, body: WeatherInput) -> WeatherSnapshot:
    row = WeatherSnapshot(
        account_id=account_id, for_date=body.for_date, condition=body.condition,
        temp_min_c=body.temp_min_c, temp_max_c=body.temp_max_c,
        precipitation_chance=body.precipitation_chance, humidity=body.humidity,
        location=body.location, provider=PROVIDER_MANUAL, source="user_declared",
    )
    session.add(row)
    await session.flush()
    return row


def serialize_weather(row: WeatherSnapshot | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id), "for_date": row.for_date.isoformat(), "condition": row.condition,
        "temp_min_c": row.temp_min_c, "temp_max_c": row.temp_max_c,
        "precipitation_chance": row.precipitation_chance, "humidity": row.humidity,
        "location": row.location, "provider": row.provider, "source": row.source,
    }


# --- Air Quality ------------------------------------------------------------


async def record_air_quality(session: AsyncSession, account_id: uuid.UUID, body: AirQualityInput) -> AirQualitySnapshot:
    from app.domains.planning.environment import determine_naqi_category
    row = AirQualitySnapshot(
        account_id=account_id, for_date=body.for_date, aqi=body.aqi,
        category=determine_naqi_category(body.aqi), pollutant=body.pollutant,
        provider=PROVIDER_MANUAL, source="user_declared",
    )
    session.add(row)
    await session.flush()
    return row


def serialize_air_quality(row: AirQualitySnapshot | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row.id), "for_date": row.for_date.isoformat(), "aqi": row.aqi,
        "category": row.category, "pollutant": row.pollutant,
        "provider": row.provider, "source": row.source,
    }


# --- Calendar ---------------------------------------------------------------


async def upsert_event(
    session: AsyncSession,
    account_id: uuid.UUID,
    body: CalendarEventInput,
    *,
    provider: str = PROVIDER_MANUAL,
    integration_id: uuid.UUID | None = None,
) -> tuple[CalendarEvent, bool]:
    """Add an event, or return the existing one. Returns ``(row, created)``.

    The dedup key is provider + external id + start instant, so re-syncing a
    calendar or re-posting the same event does not create a second row.
    """
    from app.domains.planning.context import infer_occasion

    # Python's built-in hash() is salted per interpreter process, so the same
    # event reposted after a restart (or handled by another worker) would get
    # a different key and slip past deduplication. A digest is stable forever.
    if body.external_id:
        external_id = body.external_id
    else:
        seed = f"{body.title}|{body.starts_at.isoformat()}".encode()
        external_id = f"user-{hashlib.sha256(seed).hexdigest()[:24]}"
    dedup_key = f"{provider}:{external_id}:{body.starts_at.isoformat()}"

    existing = (await session.execute(
        select(CalendarEvent).where(
            CalendarEvent.account_id == account_id, CalendarEvent.dedup_key == dedup_key
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing, False

    occasion_key, confidence = (body.occasion_key, 1.0) if body.occasion_key else infer_occasion(body.title)
    row = CalendarEvent(
        account_id=account_id, integration_id=integration_id, external_id=external_id,
        dedup_key=dedup_key, title=body.title, starts_at=body.starts_at, ends_at=body.ends_at,
        all_day=body.all_day, location=body.location, occasion_key=occasion_key,
        dress_code_hint=body.dress_code_hint, inference_confidence=confidence,
        user_confirmed=bool(body.occasion_key), provider=provider,
        # What matters for revocation is whether the event arrived *through an
        # integration*, not which provider name it carried. An event seeded by
        # connecting a calendar belongs to that connection even when the
        # provider is the manual one.
        source="integration" if integration_id is not None else "user_declared",
    )
    session.add(row)
    await session.flush()
    return row, True


async def owned_event(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID) -> CalendarEvent:
    row = (await session.execute(
        select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that event.")
    return row


def serialize_event(row: CalendarEvent, timezone_name: str) -> dict[str, Any]:
    return {
        "id": str(row.id), "title": row.title,
        "starts_at": row.starts_at.isoformat(),
        "local_time": clock.local_now(timezone_name, moment=row.starts_at).strftime("%H:%M"),
        "local_date": clock.local_now(timezone_name, moment=row.starts_at).date().isoformat(),
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "all_day": row.all_day, "location": row.location,
        "occasion_key": row.occasion_key, "dress_code_hint": row.dress_code_hint,
        "inference_confidence": row.inference_confidence,
        "user_confirmed": row.user_confirmed, "provider": row.provider,
        "source": row.source, "status": row.status,
    }


# --- Integrations -----------------------------------------------------------


async def connect_calendar(
    session: AsyncSession, account_id: uuid.UUID, provider: str, credential_ref: str | None, label: str | None
) -> ExternalIntegration:
    if provider not in KNOWN_CALENDAR_PROVIDERS:
        raise ValidationFailedError(
            f"'{provider}' is not a calendar we support. Choose one of: {', '.join(KNOWN_CALENDAR_PROVIDERS)}.",
            field="provider",
        )
    row = (await session.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
            ExternalIntegration.kind == INTEGRATION_CALENDAR,
            ExternalIntegration.provider == provider,
        )
    )).scalar_one_or_none()
    if row is None:
        row = ExternalIntegration(
            account_id=account_id, kind=INTEGRATION_CALENDAR, provider=provider,
            credential_ref=credential_ref, external_account_label=label,
        )
        session.add(row)
    else:
        row.status = "connected"
        row.credential_ref = credential_ref
        row.external_account_label = label
        row.revoked_at = None
        row.last_error = None
    row.last_synced_at = utcnow()
    await session.flush()
    return row


async def disconnect_calendar(session: AsyncSession, account_id: uuid.UUID) -> list[ExternalIntegration]:
    """Revoke calendar access and stop using anything it gave us.

    Disconnecting has to actually mean something. Events sourced from the
    integration are marked revoked so they stop feeding plans; events the user
    typed themselves are theirs and are left alone.
    """
    rows = (await session.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
            ExternalIntegration.kind == INTEGRATION_CALENDAR,
        )
    )).scalars().all()
    integration_ids = [row.id for row in rows]
    for row in rows:
        row.status = "revoked"
        row.revoked_at = utcnow()
        row.credential_ref = None

    # Scoped by integration id rather than by a source string: exactly the
    # events that came from the connection being revoked, and nothing else.
    events = (
        (await session.execute(
            select(CalendarEvent).where(
                CalendarEvent.account_id == account_id,
                CalendarEvent.integration_id.in_(integration_ids),
                CalendarEvent.status == "active",
            )
        )).scalars().all()
        if integration_ids else []
    )
    for event in events:
        event.status = "revoked"

    await session.flush()
    return list(rows)


async def calendar_status(session: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    rows = (await session.execute(
        select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
            ExternalIntegration.kind == INTEGRATION_CALENDAR,
        )
    )).scalars().all()
    connected = [row for row in rows if row.status == "connected"]
    return {
        "connected": bool(connected),
        "integrations": [{
            "id": str(row.id), "provider": row.provider, "status": row.status,
            "label": row.external_account_label,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
            "last_error": row.last_error,
            # Proof, in the response, that no token is held here.
            "stores_credentials": False,
        } for row in rows],
        "providers": catalogue()["calendar"],
        "note": (
            "You can use the planner without connecting anything — add your events yourself. "
            "No access token is ever stored in the app database."
        ),
    }


# --- Laundry ----------------------------------------------------------------


async def set_item_state(
    session: AsyncSession, account_id: uuid.UUID, item_id: uuid.UUID, state: str,
    available_from: date | None, note: str | None,
) -> LaundryStateEvent:
    if state not in LAUNDRY_STATES:
        raise ValidationFailedError(f"'{state}' is not a state we track.", field="state")
    await owned_item(session, account_id, item_id)
    row = LaundryStateEvent(
        account_id=account_id, item_id=item_id, state=state,
        available_from=available_from, note=note,
    )
    session.add(row)
    await session.flush()
    return row


# --- Serialisation ----------------------------------------------------------


def serialize_action(row: DailyPlanAction) -> dict[str, Any]:
    return {
        "id": str(row.id), "module": row.module, "action_type": row.action_type,
        "title": row.title, "body": row.body, "priority": row.priority,
        "relevance": row.relevance,
        "inventory_item_id": str(row.inventory_item_id) if row.inventory_item_id else None,
        "completed": row.completed_at is not None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


async def serialize_plan(
    session: AsyncSession, plan: DailyPlan, *, include_look: bool = True
) -> dict[str, Any]:
    actions = (await session.execute(
        select(DailyPlanAction).where(DailyPlanAction.plan_id == plan.id).order_by(DailyPlanAction.priority)
    )).scalars().all()

    look_payload: dict[str, Any] | None = None
    if include_look and plan.look_id:
        look = await session.get(Look, plan.look_id)
        if look is not None and look.account_id == plan.account_id:
            look_payload = await recommendation_service.serialize_look(session, look)

    schedule = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == plan.account_id, OutfitSchedule.plan_date == plan.plan_date
        )
    )).scalar_one_or_none()

    weather = await session.get(WeatherSnapshot, plan.weather_snapshot_id) if plan.weather_snapshot_id else None
    air_quality = await session.get(AirQualitySnapshot, plan.air_quality_snapshot_id) if plan.air_quality_snapshot_id else None
    primary = [row for row in actions if row.priority <= 40]
    optional = [row for row in actions if row.priority > 40]

    return {
        "plan_date": plan.plan_date.isoformat(),
        "timezone": plan.timezone_name,
        "weekday": plan.plan_date.strftime("%A"),
        "status": plan.status,
        "headline": plan.headline,
        "confidence": plan.confidence,
        "generated_from": plan.generated_from,
        "engine_version": plan.engine_version,
        "used_llm": plan.used_llm,
        "locked": plan.locked,
        "version": plan.version,
        "outfit": look_payload,
        "weather": serialize_weather(weather),
        "air_quality": serialize_air_quality(air_quality),
        "weather_note": plan.weather_note,
        "event_note": plan.event_note,
        # The short list the screen opens with, and the rest underneath it.
        "primary": [serialize_action(row) for row in primary],
        "optional_modules": [serialize_action(row) for row in optional],
        "needs_clarification": plan.needs_clarification,
        "clarification": plan.clarification,
        "missing_information": plan.missing_information,
        "worn": schedule.status == "worn" if schedule else False,
        "computed_at": plan.computed_at.isoformat() if plan.computed_at else None,
        "disclaimer": "Built from what you own and told us. Not medical or diagnostic advice.",
    }


async def serialize_week(session: AsyncSession, plan: WeeklyPlan) -> dict[str, Any]:
    rows = (await session.execute(
        select(WeeklyPlanDay).where(WeeklyPlanDay.weekly_plan_id == plan.id).order_by(WeeklyPlanDay.plan_date)
    )).scalars().all()

    days: list[dict[str, Any]] = []
    for row in rows:
        daily = await session.get(DailyPlan, row.daily_plan_id) if row.daily_plan_id else None
        payload: dict[str, Any] = {
            "plan_date": row.plan_date.isoformat(),
            "weekday": row.plan_date.strftime("%A"),
            "locked": row.locked,
            "note": row.note,
            "owned_items": [],
            "headline": None, "occasion": None, "weather": None,
            "confidence": None, "status": "empty",
        }
        if daily is not None and daily.account_id == plan.account_id:
            summary = await serialize_plan(session, daily)
            payload.update({
                "headline": summary["headline"], "status": summary["status"],
                "confidence": summary["confidence"], "weather": summary["weather"],
                "air_quality": summary.get("air_quality"),
                "needs_clarification": summary["needs_clarification"],
                "owned_items": (summary["outfit"] or {}).get("owned_items", []),
                "optional_addition_count": (summary["outfit"] or {}).get("optional_addition_count", 0),
                "worn": summary["worn"],
            })
        days.append(payload)

    from app.domains.planning.weekly import repetition_report

    unavailable = (await session.execute(
        select(LaundryStateEvent).where(LaundryStateEvent.account_id == plan.account_id)
        .order_by(LaundryStateEvent.created_at.desc()).limit(50)
    )).scalars().all()

    return {
        "week_start": plan.week_start.isoformat(),
        "week_end": (plan.week_start + (clock.week_dates(plan.week_start)[-1] - plan.week_start)).isoformat(),
        "timezone": plan.timezone_name,
        "status": plan.status,
        "version": plan.version,
        "engine_version": plan.engine_version,
        "repetition_window_days": plan.repetition_window_days,
        "days": days,
        "repetition": repetition_report(days),
        "laundry": [{
            "item_id": str(row.item_id), "state": row.state,
            "available_from": row.available_from.isoformat() if row.available_from else None,
        } for row in unavailable[:10]],
        "generated_at": plan.generated_at.isoformat() if plan.generated_at else None,
    }


async def recalculation_history(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(PlanRecalculationEvent).where(
            PlanRecalculationEvent.account_id == account_id,
            PlanRecalculationEvent.plan_date == plan_date,
        ).order_by(PlanRecalculationEvent.created_at.desc()).limit(20)
    )).scalars().all()
    return [{
        "trigger": row.trigger, "detail": row.detail, "recomputed": row.recomputed,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


async def sync_schedule_items(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date, look_id: uuid.UUID | None
) -> OutfitSchedule | None:
    """Re-read the look's items into the schedule row.

    The schedule is what ``recent_wear`` builds repetition history from. If it
    is not refreshed after a swap it keeps the piece the user took *off*, so
    later days penalise the wrong garment and can recommend the very item the
    user just chose to wear.
    """
    row = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == account_id, OutfitSchedule.plan_date == plan_date
        )
    )).scalar_one_or_none()
    if row is None:
        return None

    from app.domains.recommendation.models import OWNERSHIP_OWNED, LookItem

    item_ids: list[str] = []
    if look_id is not None:
        rows = (await session.execute(
            select(LookItem.inventory_item_id)
            .where(LookItem.look_id == look_id, LookItem.ownership == OWNERSHIP_OWNED)
            .order_by(LookItem.position)
        )).scalars().all()
        item_ids = [str(value) for value in rows if value is not None]

    row.look_id = look_id
    row.item_ids = item_ids
    await session.flush()
    return row


async def mark_worn(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date, *, worn: bool = True
) -> OutfitSchedule | None:
    row = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == account_id, OutfitSchedule.plan_date == plan_date
        )
    )).scalar_one_or_none()
    if row is None:
        return None
    row.status = "worn" if worn else "planned"
    row.worn_at = utcnow() if worn else None
    await session.flush()
    return row
