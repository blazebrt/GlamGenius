"""VC-02 Event Ready orchestration over CalendarEvent, Style and Care."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care import maintenance as care_maintenance
from app.domains.care.decisions import CareDecisionReasonCode
from app.domains.planning import clock, compiler
from app.domains.planning import context as context_stage
from app.domains.planning.models import CalendarEvent, EventReadyAction, EventReadyPlan
from app.domains.recommendation.models import Look, LookItem, OccasionRecord, RecommendationRun, StyleRequest
from app.domains.recommendation.occasions import get_occasion
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

EVENT_READY_VERSION = "vc-02-v1"


def _sha(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _event_payload(event: CalendarEvent, timezone_name: str) -> dict[str, Any]:
    local = clock.local_now(timezone_name, moment=event.starts_at)
    return {
        "id": str(event.id), "title": event.title, "starts_at": event.starts_at.isoformat(),
        "ends_at": event.ends_at.isoformat() if event.ends_at else None, "all_day": event.all_day,
        "local_date": local.date().isoformat(), "local_time": local.strftime("%H:%M"),
        "location": event.location, "occasion_key": event.occasion_key,
        "dress_code_hint": event.dress_code_hint, "user_confirmed": event.user_confirmed,
        "provider": event.provider, "source": event.source,
        "inference_confidence": event.inference_confidence, "status": event.status,
    }


def _context_payload(day: Any) -> dict[str, Any]:
    weather = day.weather
    air = day.air_quality
    return {
        "weather": None if weather is None else {
            "condition": weather.condition, "temp_min_c": weather.temp_min_c,
            "temp_max_c": weather.temp_max_c, "precipitation_chance": weather.precipitation_chance,
            "humidity": weather.humidity, "location": weather.location,
            "provider": weather.provider, "source": weather.source,
            **({"attribution": weather.attribution} if weather.attribution else {}),
        },
        "air_quality": None if air is None else {
            "aqi": air.aqi, "index_system": air.index_system, "category": air.category,
            "location": air.location,
            "provider": air.provider, "source": air.source,
            **({"attribution": air.attribution} if air.attribution else {}),
        },
        "unavailable_item_ids": sorted(str(value) for value in day.unavailable_item_ids),
    }


def _status(event: CalendarEvent, day: Any) -> str:
    local_date = clock.local_now(day.timezone_name, moment=event.starts_at).date()
    today = day.now_local.date()
    if local_date < today:
        return "past"
    if not event.user_confirmed or not event.occasion_key:
        return "needs_confirmation"
    return "event_day" if local_date == today else "preparing"


async def _event_day_context(session: AsyncSession, event: CalendarEvent, timezone_name: str) -> Any:
    target = clock.local_now(timezone_name, moment=event.starts_at).date()
    day = await context_stage.gather(
        session, account_id=event.account_id, plan_date=target, timezone_name=timezone_name,
        environment_location=event.location, explicit_environment_location=bool(event.location and event.location.strip()),
    )
    # Never use DayContext.primary_event: the requested CalendarEvent is the authority.
    from app.domains.planning.context import DayEvent
    target_event = DayEvent(
        id=event.id, title=event.title, starts_at=event.starts_at, ends_at=event.ends_at,
        all_day=event.all_day, location=event.location, occasion_key=event.occasion_key,
        dress_code_hint=event.dress_code_hint, confidence=event.inference_confidence,
        user_confirmed=event.user_confirmed,
    )
    day.events = [target_event]
    day.occasion_key = event.occasion_key or "everyday"
    day.occasion_confidence = 1.0 if event.user_confirmed and event.occasion_key else event.inference_confidence
    day.dress_code = event.dress_code_hint
    return day


def _care_payload(material: Any) -> dict[str, Any]:
    decisions, plan, cadence = material.decisions, material.care_plan, material.hair_wash_cadence
    # Maintenance can change the preparation timeline, so its canonical
    # fingerprint belongs in the material the plan fingerprint hashes. Without
    # it, a maintenance-only change would alter the actions while the stored
    # provenance still described the previous state.
    return {
        "maintenance_fingerprint": care_maintenance.maintenance_fingerprint(material.maintenance),
        "maintenance_version": material.maintenance.maintenance_version,
        "authority": "care", "decision_version": decisions.decision_version,
        "decision_fingerprint": material.decision_fingerprint,
        "routine_plan_version": plan.plan_version, "routine_plan_fingerprint": material.routine_plan_fingerprint,
        "resolved_effort": plan.resolved_effort.value,
        "active_skin_slot_count": plan.active_skin_slot_count, "active_hair_slot_count": plan.active_hair_slot_count,
        "skin_gap_count": decisions.skin_core_gap_count, "hair_gap_count": decisions.hair_core_gap_count,
        "hair_wash": {**cadence.as_payload(), "fingerprint": material.hair_wash_cadence_fingerprint},
    }


async def _selected_look(
    session: AsyncSession,
    account_id: uuid.UUID,
    look_id: uuid.UUID | None,
    event: CalendarEvent,
    timezone_name: str,
) -> tuple[Look | None, list[LookItem]]:
    if look_id is None:
        return None, []
    filters = [
        Look.id == look_id, Look.account_id == account_id, Look.status == "active",
        RecommendationRun.kind == "style_occasion", OccasionRecord.account_id == account_id,
        OccasionRecord.occasion_key == event.occasion_key,
        OccasionRecord.event_date == clock.local_now(timezone_name, moment=event.starts_at).date(),
    ]
    row = (await session.execute(
        select(Look, OccasionRecord).join(RecommendationRun, RecommendationRun.id == Look.run_id)
        .join(StyleRequest, StyleRequest.id == RecommendationRun.style_request_id)
        .join(OccasionRecord, OccasionRecord.id == StyleRequest.occasion_id)
        .where(*filters)
    )).one_or_none()
    if row is None:
        return None, []
    look, occasion_record = row
    if event.dress_code_hint:
        resolved_style_code = occasion_record.dress_code or get_occasion(occasion_record.occasion_key).dress_codes[0]
        if resolved_style_code != event.dress_code_hint:
            return None, []
    if look is None:
        return None, []
    items = (await session.execute(select(LookItem).where(LookItem.look_id == look.id).order_by(LookItem.position))).scalars().all()
    return look, list(items)


def _action_material(key: str, payload: dict[str, Any]) -> str:
    return _sha({"event_ready_version": EVENT_READY_VERSION, "action_key": key, "material": payload})


def _maintenance_actions(material: Any, event_local_date: date) -> list[dict[str, Any]]:
    """Upkeep that falls due on or before the event, at most one card.

    Timing comes from the one maintenance authority; Event Ready never
    computes a second schedule of its own.
    """
    # The comparison is against the event's own local date, which is what the
    # customer is preparing for. It is passed in rather than read off the
    # material so the two can never drift into being accidentally equal.
    due = care_maintenance.due_by_event_date(material.maintenance, event_local_date)
    if not due:
        return []
    named = ", ".join(row.label for row in due[:2])
    extra = len(due) - 2
    body = (
        f"{named} and {extra} more fall due before this event, by your own rhythm."
        if extra > 0
        else f"{named} {'falls' if len(due) == 1 else 'fall'} due before this event, by your own rhythm."
    )
    return [{
        "action_key": "preparation:maintenance_timing", "domain": "preparation",
        "timing": "before_event", "title": "Upkeep timing for this event",
        "body": body,
        "relevance": "You track this upkeep and set the interval yourself.",
        "priority": 45, "inventory_item_id": None,
        "material": {
            "kinds": [row.kind_key for row in due],
            "next_due_on": [row.next_due_on.isoformat() for row in due if row.next_due_on],
        },
    }]


def _actions(event: CalendarEvent, day: Any, material: Any, look: Look | None, look_items: list[LookItem], status: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if status == "needs_confirmation":
        key = "context:confirm_event"
        actions.append({"action_key": key, "domain": "context", "timing": "now", "title": "Confirm your event details", "body": "Your event type needs confirmation before we prepare around it.", "relevance": "The event context is inferred or incomplete.", "priority": 10, "inventory_item_id": None, "material": {"occasion_key": event.occasion_key, "title": event.title}})
        return actions
    if status != "past" and look is None:
        key = "style:choose_event_look"
        actions.append({"action_key": key, "domain": "style", "timing": "before_event", "title": "Choose your event look", "body": "Choose an existing Style look for this event when you are ready.", "relevance": "No event look has been selected.", "priority": 20, "inventory_item_id": None, "material": {"occasion_key": event.occasion_key}})
    if look is not None and status != "past":
        blocked = set(day.unavailable_item_ids)
        unavailable = [item for item in look_items if item.inventory_item_id in blocked]
        if unavailable:
            key = "preparation:item_unavailable"
            item_ids = sorted(str(item.inventory_item_id) for item in unavailable if item.inventory_item_id)
            names = ", ".join(item.display_name for item in unavailable)
            actions.append({"action_key": key, "domain": "preparation", "timing": "before_event", "title": "One part of your planned look is unavailable", "body": f"{names} is marked unavailable for that day.", "relevance": "Use Style's swap option if you want to change it.", "priority": 30, "inventory_item_id": None, "material": {"item_ids": item_ids}})
    if status != "past":
        cadence = material.hair_wash_cadence
        if cadence.status.value == "due":
            key = "care:hair_wash"
            actions.append({"action_key": key, "domain": "care", "timing": "before_event", "title": "Hair care is due for this event", "body": "Your existing Hair Care cadence marks a wash as due.", "relevance": cadence.reason.value, "priority": 40, "inventory_item_id": None, "material": cadence.as_payload()})
        elif cadence.status.value in ("needs_anchor", "unscheduled"):
            # Uncertainty is surfaced, never converted into a guessed schedule.
            day.missing_information = list(dict.fromkeys([*day.missing_information, "hair_wash_cadence"]))
        hard_safety = [
            decision for decision in material.decisions.product_decisions
            if any(reason.code in (CareDecisionReasonCode.PRODUCT_EXPIRED, CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH) for reason in decision.blocking_reasons)
        ]
        if hard_safety:
            key = "care:attention"
            reasons = sorted({reason.code.value for decision in hard_safety for reason in decision.blocking_reasons if reason.code in (CareDecisionReasonCode.PRODUCT_EXPIRED, CareDecisionReasonCode.CONFIRMED_ALLERGY_MATCH)})
            actions.append({"action_key": key, "domain": "care", "timing": "before_event", "title": "Review one Care item", "body": "One existing Care safety rule needs your attention before the event.", "relevance": "A canonical Care safety decision is blocking an item.", "priority": 35, "inventory_item_id": None, "material": {"item_ids": sorted(str(decision.item_id) for decision in hard_safety), "reason_codes": reasons}})
    if status != "past":
        actions.extend(_maintenance_actions(material, day.plan_date))
    return actions


async def _plan_row(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID, *, lock: bool = False) -> EventReadyPlan | None:
    statement = select(EventReadyPlan).where(EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id == event_id)
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def _serialize(session: AsyncSession, event: CalendarEvent, plan: EventReadyPlan | None, timezone_name: str, *, day: Any | None = None, material: Any | None = None) -> dict[str, Any]:
    event_payload = _event_payload(event, timezone_name)
    if day is None:
        day = await _event_day_context(session, event, timezone_name)
    status = "not_generated" if plan is None else (_status(event, day) if event.status == "active" else "past")
    local_date = date.fromisoformat(event_payload["local_date"])
    days_until = (local_date - day.now_local.date()).days
    actions = []
    if plan is not None and event.status == "active" and status != "past":
        rows = (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == plan.id).order_by(EventReadyAction.priority, EventReadyAction.created_at))).scalars().all()
        actions = [{"id": str(row.id), "action_key": row.action_key, "domain": row.domain, "timing": row.timing, "title": row.title, "body": row.body, "relevance": row.relevance, "inventory_item_id": str(row.inventory_item_id) if row.inventory_item_id else None, "completed": row.completed_at is not None, "completed_at": row.completed_at.isoformat() if row.completed_at else None} for row in rows]
    care = _care_payload(material) if material is not None else None
    selected = None
    if plan and plan.selected_look_id:
        selected_row = (await session.execute(select(Look).where(Look.id == plan.selected_look_id, Look.account_id == event.account_id))).scalar_one_or_none()
        if selected_row:
            selected = {"id": str(selected_row.id), "title": selected_row.title, "status": selected_row.status}
    missing = list(day.missing_information)
    if day.weather is None and "event_day_weather" not in missing:
        missing.append("event_day_weather")
    if not event.user_confirmed or not event.occasion_key:
        missing.append("event_confirmation")
    style_status = "blocked_by_event_confirmation" if (status == "needs_confirmation" or (status == "not_generated" and (not event.user_confirmed or not event.occasion_key))) else ("look_selected" if selected else "needs_look")
    payload = {"event_ready_version": EVENT_READY_VERSION, "event": event_payload, "status": status, "countdown": {"days_until": days_until, "event_local_date": event_payload["local_date"]}, "context": {"weather": _context_payload(day)["weather"], "air_quality": _context_payload(day)["air_quality"]}, "style": {"authority": "style", "status": style_status, "selected_look": selected}, "care": care, "timeline": actions, "readiness": {"completed_actions": sum(a["completed"] for a in actions), "total_actions": len(actions), "all_done": bool(actions) and all(a["completed"] for a in actions)}, "missing_information": sorted(set(missing))}
    payload["event_ready_fingerprint"] = plan.input_fingerprint if plan else _sha({"event_ready_version": EVENT_READY_VERSION, "event": event_payload, "context": _context_payload(day)})
    return payload


async def generate(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID) -> dict[str, Any]:
    event = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.account_id == account_id))).scalar_one_or_none()
    if event is None:
        raise NotFoundError("We could not find that event.")
    if event.status != "active":
        raise ValidationFailedError("That event is no longer active.", field="event_id")
    timezone_name = await context_stage.resolve_timezone_for(session, account_id)
    day = await _event_day_context(session, event, timezone_name)
    material = await compiler.build_day_care_material(session, day)
    plan = await _plan_row(session, account_id, event_id)
    status = _status(event, day)
    selected_material = {"look_id": None, "look_version": None, "items": []}
    fingerprint = _sha({"event_ready_version": EVENT_READY_VERSION, "event": _event_payload(event, timezone_name), "context": _context_payload(day), "care": _care_payload(material), "style": selected_material})
    if plan is None:
        await session.execute(pg_insert(EventReadyPlan).values(
            account_id=account_id, calendar_event_id=event_id, selected_look_id=None,
            status=status, engine_version=EVENT_READY_VERSION,
            input_fingerprint=fingerprint, generated_at=utcnow(),
        ).on_conflict_do_nothing(index_elements=["account_id", "calendar_event_id"]))
        await session.flush()
        plan = await _plan_row(session, account_id, event_id, lock=True)
    else:
        plan = await _plan_row(session, account_id, event_id, lock=True)
    if plan is None:
        raise ValidationFailedError("Event Ready could not be generated right now.", field="event_id")
    look = look_items = None
    if plan.selected_look_id is not None:
        look, look_items = await _selected_look(session, account_id, plan.selected_look_id, event, timezone_name)
        if look is None:
            # A date/occasion change can make an old Style look invalid; never
            # expose it as if it were still compatible with this event.
            plan.selected_look_id = None
    selected_material = {"look_id": str(look.id) if look else None, "look_version": look.version if look else None, "items": sorted(str(item.inventory_item_id) for item in (look_items or []) if item.inventory_item_id)}
    fingerprint = _sha({"event_ready_version": EVENT_READY_VERSION, "event": _event_payload(event, timezone_name), "context": _context_payload(day), "care": _care_payload(material), "style": selected_material})
    actions = _actions(event, day, material, look, look_items or [], status)
    old = {row.action_key: row for row in (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == plan.id))).scalars().all()}
    desired = {row["action_key"]: _action_material(row["action_key"], row["material"]) for row in actions}
    actions_current = set(old) == set(desired) and all(old[key].material_fingerprint == value for key, value in desired.items())
    if plan.input_fingerprint != fingerprint:
        plan.status, plan.input_fingerprint, plan.generated_at = status, fingerprint, utcnow()
    if plan.input_fingerprint == fingerprint and actions_current:
        return await _serialize(session, event, plan, timezone_name, day=day, material=material)
    for key, row in old.items():
        if key not in desired:
            await session.delete(row)
    for row in actions:
        fp = desired[row["action_key"]]
        existing = old.get(row["action_key"])
        if existing is None:
            await session.execute(pg_insert(EventReadyAction).values(
                event_ready_plan_id=plan.id, action_key=row["action_key"], domain=row["domain"],
                timing=row["timing"], title=row["title"], body=row["body"],
                relevance=row["relevance"], priority=row["priority"],
                inventory_item_id=row["inventory_item_id"], material_fingerprint=fp,
            ).on_conflict_do_nothing(index_elements=["event_ready_plan_id", "action_key"]))
        elif existing.material_fingerprint != fp:
            existing.title, existing.body, existing.relevance, existing.priority = row["title"], row["body"], row["relevance"], row["priority"]
            existing.inventory_item_id, existing.material_fingerprint, existing.completed_at = row["inventory_item_id"], fp, None
    await session.flush()
    return await _serialize(session, event, plan, timezone_name, day=day, material=material)


async def read(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID) -> dict[str, Any]:
    event = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.account_id == account_id))).scalar_one_or_none()
    if event is None:
        raise NotFoundError("We could not find that event.")
    timezone_name = await context_stage.resolve_timezone_for(session, account_id)
    plan = await _plan_row(session, account_id, event_id)
    day = await _event_day_context(session, event, timezone_name)
    material = await compiler.build_day_care_material(session, day) if plan is not None else None
    return await _serialize(session, event, plan, timezone_name, day=day, material=material)


async def set_look(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID, look_id: uuid.UUID | None) -> dict[str, Any]:
    event = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.account_id == account_id, CalendarEvent.status == "active"))).scalar_one_or_none()
    if event is None:
        raise NotFoundError("We could not find that event.")
    plan = await _plan_row(session, account_id, event_id)
    if plan is None:
        await generate(session, account_id, event_id)
        plan = await _plan_row(session, account_id, event_id)
    if look_id is not None:
        if not event.user_confirmed or not event.occasion_key:
            raise ValidationFailedError("Confirm the event type before choosing a look for it.", field="look_id")
        look, _ = await _selected_look(session, account_id, look_id, event, await context_stage.resolve_timezone_for(session, account_id))
        if look is None:
            raise ValidationFailedError("Choose an active Style look for this event.", field="look_id")
    plan.selected_look_id = look_id
    await session.flush()
    return await generate(session, account_id, event_id)


async def complete_action(session: AsyncSession, account_id: uuid.UUID, event_id: uuid.UUID, action_id: uuid.UUID, completed: bool) -> dict[str, Any]:
    event = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id, CalendarEvent.account_id == account_id))).scalar_one_or_none()
    if event is None:
        raise NotFoundError("We could not find that event.")
    plan = await _plan_row(session, account_id, event_id)
    if plan is None:
        raise NotFoundError("Event Ready has not been generated yet.")
    action = (await session.execute(select(EventReadyAction).where(EventReadyAction.id == action_id, EventReadyAction.event_ready_plan_id == plan.id))).scalar_one_or_none()
    if action is None:
        raise NotFoundError("We could not find that preparation action.")
    if completed:
        if action.completed_at is None:
            action.completed_at = utcnow()
    else:
        action.completed_at = None
    await session.flush()
    timezone_name = await context_stage.resolve_timezone_for(session, account_id)
    day = await _event_day_context(session, event, timezone_name)
    material = await compiler.build_day_care_material(session, day)
    return await _serialize(session, event, plan, timezone_name, day=day, material=material)
