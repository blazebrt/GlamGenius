"""Proactive notifications, kept rare on purpose.

The default is **one** appearance notification per day. That is a product
decision enforced in code, not a suggestion in a settings screen: an app that
tells you about your face every few hours is one people delete.

Three gates, in order, and every decision is written down — including the
suppressed ones, so "why didn't I hear about X" is answerable:

1. **Deduplication.** A stable hash of account, date and content. The same
   notification can be queued a hundred times and sends once.
2. **The daily cap.** Default 1.
3. **Quiet hours.** Default 21:00–07:00 local, which is the user's local time,
   not the server's.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock
from app.domains.planning.models import (
    MODULES,
    DailyPlan,
    DailyPlanAction,
    NotificationDelivery,
    NotificationPreference,
)
from app.shared.database.base import utcnow

SUPPRESSED_DUPLICATE = "duplicate"
SUPPRESSED_CAP = "daily_cap_reached"
SUPPRESSED_QUIET = "quiet_hours"
SUPPRESSED_DISABLED = "disabled"
SUPPRESSED_MODULE_OFF = "module_disabled"


async def preferences_for(session: AsyncSession, account_id: uuid.UUID, timezone_name: str) -> NotificationPreference:
    row = (await session.execute(
        select(NotificationPreference).where(NotificationPreference.account_id == account_id)
    )).scalar_one_or_none()
    if row is None:
        row = NotificationPreference(
            account_id=account_id, timezone_name=timezone_name,
            modules={module: True for module in MODULES},
        )
        session.add(row)
        await session.flush()
    return row


def dedup_hash(account_id: uuid.UUID, plan_date: date, notification_key: str, title: str) -> str:
    """What makes two notifications the same notification.

    Content is included as well as the key, so a plan that genuinely changed can
    notify again, while a plan recomputed to the same answer cannot.
    """
    raw = f"{account_id}|{plan_date.isoformat()}|{notification_key}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Quiet hours, handling the normal case of a window crossing midnight."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def _sent_today(session: AsyncSession, account_id: uuid.UUID, plan_date: date) -> int:
    return int((await session.execute(
        select(func.count()).select_from(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.plan_date == plan_date,
            NotificationDelivery.status == "queued",
        )
    )).scalar_one())


async def queue(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date,
    notification_key: str,
    title: str,
    body: str = "",
    module: str = "outfit",
    timezone_name: str = clock.DEFAULT_TIMEZONE,
    moment=None,
) -> NotificationDelivery:
    """Decide about one notification and record the decision either way."""
    preference = await preferences_for(session, account_id, timezone_name)
    digest = dedup_hash(account_id, plan_date, notification_key, title)

    existing = (await session.execute(
        select(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.dedup_hash == digest,
        )
    )).scalar_one_or_none()
    if existing is not None:
        # Already decided. Return the original decision rather than making a
        # second one — this is what makes queueing idempotent.
        return existing

    row = NotificationDelivery(
        account_id=account_id, plan_date=plan_date, notification_key=notification_key,
        dedup_hash=digest, title=title, body=body, status="queued",
    )

    local_hour = clock.local_now(preference.timezone_name or timezone_name, moment=moment).hour
    if not preference.enabled:
        row.status, row.suppressed_reason = "suppressed", SUPPRESSED_DISABLED
    elif preference.modules and preference.modules.get(module) is False:
        row.status, row.suppressed_reason = "suppressed", SUPPRESSED_MODULE_OFF
    elif in_quiet_hours(local_hour, preference.quiet_hours_start, preference.quiet_hours_end):
        row.status, row.suppressed_reason = "suppressed", SUPPRESSED_QUIET
    elif await _sent_today(session, account_id, plan_date) >= preference.daily_cap:
        row.status, row.suppressed_reason = "suppressed", SUPPRESSED_CAP
    else:
        row.sent_at = utcnow()

    session.add(row)
    await session.flush()
    return row


async def queue_for_plan(
    session: AsyncSession, *, plan: DailyPlan, timezone_name: str, moment=None
) -> Optional[NotificationDelivery]:
    """The single daily notification, built from the plan's top action.

    One notification carrying the most important thing, rather than one per
    module. If the plan has nothing worth saying, nothing is queued at all.
    """
    action = (await session.execute(
        select(DailyPlanAction)
        .where(DailyPlanAction.plan_id == plan.id)
        .order_by(DailyPlanAction.priority)
        .limit(1)
    )).scalar_one_or_none()
    if action is None or plan.status != "ready":
        return None
    return await queue(
        session, account_id=plan.account_id, plan_date=plan.plan_date,
        notification_key="daily_plan", title=plan.headline,
        body=f"{action.title}. {action.body}".strip(), module=action.module,
        timezone_name=timezone_name, moment=moment,
    )


def serialize_preferences(row: NotificationPreference) -> dict[str, Any]:
    return {
        "enabled": row.enabled,
        "daily_cap": row.daily_cap,
        "quiet_hours": {"start": row.quiet_hours_start, "end": row.quiet_hours_end},
        "preferred_hour": row.preferred_hour,
        "modules": {module: bool(row.modules.get(module, True)) for module in MODULES},
        "timezone": row.timezone_name,
        "note": "At most one proactive appearance notification a day by default. Repeats are never sent twice.",
    }


def serialize_delivery(row: NotificationDelivery) -> dict[str, Any]:
    return {
        "id": str(row.id), "plan_date": row.plan_date.isoformat(),
        "notification_key": row.notification_key, "title": row.title, "body": row.body,
        "status": row.status, "suppressed_reason": row.suppressed_reason,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
    }


async def recent_deliveries(
    session: AsyncSession, account_id: uuid.UUID, limit: int = 30
) -> list[dict[str, Any]]:
    rows = (await session.execute(
        select(NotificationDelivery)
        .where(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return [serialize_delivery(row) for row in rows]
