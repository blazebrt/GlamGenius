"""A small, typed attention view over existing Planning authorities.

The agenda never computes a recommendation.  It only orders incomplete rows
already owned by Today/Event Ready and adds a neutral preparation entry for a
confirmed event that has not yet been generated.  This keeps the Today hot
path unchanged while giving notifications one deterministic input.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock
from app.domains.planning.models import CalendarEvent, DailyPlan, DailyPlanAction, EventReadyAction, EventReadyPlan

AGENDA_VERSION = "vc-09-v1"
EVENT_HORIZON_DAYS = 7
MAX_ITEMS = 3

SOURCE_TODAY = "today_action"
SOURCE_EVENT_READY = "event_ready_action"
SOURCE_EVENT_PREPARATION = "event_preparation_entry"
_SOURCE_PRIORITY = {SOURCE_EVENT_READY: 0, SOURCE_EVENT_PREPARATION: 1, SOURCE_TODAY: 2}

# Only these server-owned routes can be returned as a destination.
DESTINATIONS = frozenset({
    "/(tabs)/today", "/(tabs)/style", "/(tabs)/care", "/(tabs)/plan",
    "/event-ready", "/improve", "/(tabs)/services", "/(tabs)/inventory",
})


@dataclass(frozen=True)
class AttentionItem:
    key: str
    source_kind: str
    source_id: str
    source_action_id: str | None
    domain: str
    entity_type: str
    entity_id: str | None
    title: str
    body: str
    relevance: str
    urgency: str
    priority_tier: int
    due_at: datetime | None
    event_id: str | None
    destination: str
    destination_params: dict[str, str]
    completed: bool
    notification_eligible: bool
    provenance: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "source_kind": self.source_kind, "source_id": self.source_id,
            "source_action_id": self.source_action_id, "domain": self.domain,
            "entity_type": self.entity_type, "entity_id": self.entity_id,
            "title": self.title, "body": self.body, "relevance": self.relevance,
            "urgency": self.urgency, "priority_tier": self.priority_tier,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "event_id": self.event_id, "destination": self.destination,
            "destination_params": dict(self.destination_params), "completed": self.completed,
            "notification_eligible": self.notification_eligible, "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class AttentionAgenda:
    generated_for: date
    timezone: str
    items: tuple[AttentionItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "agenda_version": AGENDA_VERSION,
            "generated_for": self.generated_for.isoformat(),
            "timezone": self.timezone,
            "items": [item.as_dict() for item in self.items],
        }


def _safe_destination(module: str, action_type: str, *, event_id: str | None = None) -> tuple[str, dict[str, str]]:
    if event_id:
        return "/event-ready", {"eventId": event_id}
    if module in {"skincare", "hair", "maintenance"}:
        return "/(tabs)/care", {}
    if module in {"shopping", "outfit"}:
        return "/(tabs)/style", {}
    if module == "perfume":
        return "/(tabs)/care", {}
    if action_type in {"event", "event_preparation"}:
        return "/(tabs)/plan", {}
    return "/(tabs)/today", {}


def _event_tier(action_key: str, domain: str, priority: int) -> tuple[int, str]:
    if action_key in {"care:attention", "style:unavailable", "event:unavailable", "preparation:item_unavailable"} or "unavailable" in action_key:
        return 0, "blocking"
    if action_key in {"context:confirm_event", "style:choose_event_look"}:
        return 1, "time_bound"
    if domain in {"preparation", "style", "care"} and priority <= 40:
        return 1, "time_bound"
    return 3, "upkeep"


def _today_tier(action: DailyPlanAction) -> tuple[int, str, bool]:
    if action.module in {"shopping"} or action.action_type in {"low_use", "value_recovery", "purchase"}:
        return 4, "optional", False
    if action.module in {"maintenance", "hair", "skincare"}:
        return 3, "upkeep", True
    return 2, "today", True


def _sort_key(item: AttentionItem) -> tuple[Any, ...]:
    # ``key`` is the final deterministic tie-break; no customer-facing text is
    # used for ordering.
    due = item.due_at or datetime.max.replace(tzinfo=clock.zone("UTC"))
    return (item.priority_tier, due, _SOURCE_PRIORITY.get(item.source_kind, 9), item.key)


async def build_agenda(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    generated_for: date | None = None,
    timezone_name: str | None = None,
    horizon_days: int = EVENT_HORIZON_DAYS,
) -> AttentionAgenda:
    """Read and order owned Today/Event Ready concerns for one account."""
    tz_name = clock.resolve_timezone(timezone_name)
    plan_date = generated_for or clock.local_today(tz_name)
    today_start, _ = clock.day_bounds(plan_date, tz_name)
    horizon_start = today_start
    horizon_end, _ = clock.day_bounds(plan_date + timedelta(days=max(1, horizon_days)), tz_name)
    rows: list[AttentionItem] = []

    plan = (await session.execute(select(DailyPlan).where(
        DailyPlan.account_id == account_id, DailyPlan.plan_date == plan_date,
    ))).scalar_one_or_none()
    if plan is not None:
        actions = (await session.execute(select(DailyPlanAction).where(
            DailyPlanAction.plan_id == plan.id,
            DailyPlanAction.completed_at.is_(None),
            DailyPlanAction.dismissed_at.is_(None),
        ).order_by(DailyPlanAction.priority, DailyPlanAction.id))).scalars().all()
        for action in actions:
            tier, urgency, push_ok = _today_tier(action)
            destination, params = _safe_destination(action.module, action.action_type)
            rows.append(AttentionItem(
                key=f"today:{action.id}", source_kind=SOURCE_TODAY, source_id=str(plan.id),
                source_action_id=str(action.id), domain=action.module, entity_type="daily_plan_action",
                entity_id=str(action.id), title=action.title, body=action.body, relevance=action.relevance,
                urgency=urgency, priority_tier=tier,
                due_at=datetime.combine(plan_date, time.min, tzinfo=clock.zone(tz_name)), event_id=None,
                destination=destination, destination_params=params, completed=False,
                notification_eligible=push_ok and tier < 4,
                provenance={"authority": "daily_plan_action", "plan_id": str(plan.id)},
            ))

    events = (await session.execute(select(CalendarEvent).where(
        CalendarEvent.account_id == account_id,
        CalendarEvent.status == "active",
        CalendarEvent.starts_at >= horizon_start,
        CalendarEvent.starts_at < horizon_end,
    ).order_by(CalendarEvent.starts_at, CalendarEvent.id))).scalars().all()
    event_ids = [event.id for event in events]
    plans = {}
    if event_ids:
        plans = {row.calendar_event_id: row for row in (await session.execute(select(EventReadyPlan).where(
            EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id.in_(event_ids),
        ))).scalars().all()}
    for event in events:
        event_id = str(event.id)
        plan_row = plans.get(event.id)
        if plan_row is None and event.user_confirmed and event.occasion_key:
            rows.append(AttentionItem(
                key=f"event:prepare:{event.id}", source_kind=SOURCE_EVENT_PREPARATION,
                source_id=event_id, source_action_id=None, domain="event", entity_type="calendar_event",
                entity_id=event_id, title=f"Prepare for {event.title}", body="Open Event Ready when you are ready to prepare.",
                relevance="A confirmed upcoming event is on your calendar.", urgency="time_bound", priority_tier=1,
                due_at=event.starts_at, event_id=event_id, destination="/event-ready",
                destination_params={"eventId": event_id}, completed=False, notification_eligible=True,
                provenance={"authority": "calendar_event", "event_id": event_id},
            ))
            continue
        if plan_row is None and not event.user_confirmed:
            rows.append(AttentionItem(
                key=f"event:confirm:{event.id}", source_kind=SOURCE_EVENT_PREPARATION,
                source_id=event_id, source_action_id=None, domain="event", entity_type="calendar_event",
                entity_id=event_id, title="Confirm your event details",
                body="Confirm the event details before preparing around it.",
                relevance="The event context is incomplete.", urgency="time_bound", priority_tier=1,
                due_at=event.starts_at, event_id=event_id, destination="/event-ready",
                destination_params={"eventId": event_id}, completed=False, notification_eligible=True,
                provenance={"authority": "calendar_event", "event_id": event_id, "action_key": "context:confirm_event"},
            ))
            continue
        if plan_row is None:
            continue
        actions = (await session.execute(select(EventReadyAction).where(
            EventReadyAction.event_ready_plan_id == plan_row.id,
            EventReadyAction.completed_at.is_(None),
        ).order_by(EventReadyAction.priority, EventReadyAction.id))).scalars().all()
        for action in actions:
            tier, urgency = _event_tier(action.action_key, action.domain, action.priority)
            rows.append(AttentionItem(
                key=f"event:{event.id}:{action.action_key}", source_kind=SOURCE_EVENT_READY,
                source_id=str(plan_row.id), source_action_id=str(action.id), domain=action.domain,
                entity_type="event_ready_action", entity_id=str(action.id), title=action.title, body=action.body,
                relevance=action.relevance, urgency=urgency, priority_tier=tier, due_at=event.starts_at,
                event_id=event_id, destination="/event-ready", destination_params={"eventId": event_id},
                completed=False, notification_eligible=tier < 4,
                provenance={"authority": "event_ready_action", "event_ready_plan_id": str(plan_row.id), "action_key": action.action_key},
            ))

    # Semantic dedupe keeps the same canonical action from appearing twice,
    # while preserving unrelated rows with merely similar copy.
    deduped: dict[str, AttentionItem] = {}
    for item in sorted(rows, key=_sort_key):
        semantic = f"{item.domain}|{item.urgency}|{item.event_id or plan_date.isoformat()}|{item.entity_type}|{item.entity_id}"
        if semantic not in deduped:
            deduped[semantic] = item
    return AttentionAgenda(plan_date, tz_name, tuple(sorted(deduped.values(), key=_sort_key)[:MAX_ITEMS]))


async def agenda_payload(session: AsyncSession, account_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
    return (await build_agenda(session, account_id, **kwargs)).as_dict()
