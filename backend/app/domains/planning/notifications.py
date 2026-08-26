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
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock
from app.domains.planning.models import (
    MODULE_MAINTENANCE,
    MODULES,
    DailyPlan,
    DailyPlanAction,
    NotificationDevice,
    NotificationDelivery,
    NotificationPreference,
)
from app.shared.database.base import utcnow

SUPPRESSED_DUPLICATE = "duplicate"
SUPPRESSED_CAP = "daily_cap_reached"
SUPPRESSED_QUIET = "quiet_hours"
SUPPRESSED_DISABLED = "disabled"
SUPPRESSED_MODULE_OFF = "module_disabled"
STATUS_SUPPRESSED = "suppressed"
STATUS_QUEUED = "queued"
STATUS_PROVIDER_ACCEPTED = "provider_accepted"
STATUS_PROVIDER_FAILED = "provider_failed"
STATUS_RECEIPT_OK = "receipt_ok"
STATUS_RECEIPT_FAILED = "receipt_failed"

#: Modules a new account is notified about by default. Maintenance sits here
#: like any other module: its real gate is the per-kind ``reminders_enabled``
#: choice, which defaults to off. Excluding it from this map instead would make
#: that per-kind opt-in unreachable, because nothing in the product turns the
#: generic module flag back on.
DEFAULT_MODULE_NOTIFICATIONS: dict[str, bool] = {module: True for module in MODULES}


async def maintenance_reminders_allowed(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date,
) -> Sequence[Any]:
    """The due kinds this account has explicitly asked to be reminded about.

    Consent is read from canonical maintenance state rather than inferred from
    the module appearing in a plan, and it is returned per kind rather than as
    an account-wide boolean so the notification can name only what was opted
    into. Empty means no maintenance notification is permitted at all.
    """
    from app.domains.care import maintenance as care_maintenance
    from app.domains.care import maintenance_service as care_maintenance_service

    decided = await care_maintenance_service.build_maintenance(
        session, account_id, plan_date=plan_date,
    )
    return care_maintenance.reminder_eligible(decided)


async def preferences_for(session: AsyncSession, account_id: uuid.UUID, timezone_name: str, *, lock: bool = False) -> NotificationPreference:
    statement = select(NotificationPreference).where(NotificationPreference.account_id == account_id)
    if lock:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        row = NotificationPreference(
            account_id=account_id, timezone_name=timezone_name,
            modules=dict(DEFAULT_MODULE_NOTIFICATIONS), native_push_enabled=False,
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
            NotificationDelivery.status.in_((STATUS_QUEUED, STATUS_PROVIDER_ACCEPTED, STATUS_PROVIDER_FAILED)),
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
    deep_link: str | None = None,
    source_kind: str | None = None,
    source_id: str | None = None,
    scheduled_for: datetime | None = None,
) -> NotificationDelivery:
    """Decide about one notification and record the decision either way."""
    preference = await preferences_for(session, account_id, timezone_name, lock=True)
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
        dedup_hash=digest, title=title, body=body, status=STATUS_QUEUED,
        deep_link=deep_link, source_kind=source_kind, source_id=source_id,
        scheduled_for=scheduled_for,
    )

    local_hour = clock.local_now(preference.timezone_name or timezone_name, moment=moment).hour
    if not preference.enabled:
        row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_DISABLED
    elif preference.modules is not None and not preference.modules.get(
        module, DEFAULT_MODULE_NOTIFICATIONS.get(module, True)
    ):
        row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_MODULE_OFF
    elif in_quiet_hours(local_hour, preference.quiet_hours_start, preference.quiet_hours_end):
        row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_QUIET
    elif await _sent_today(session, account_id, plan_date) >= preference.daily_cap:
        row.status, row.suppressed_reason = STATUS_SUPPRESSED, SUPPRESSED_CAP

    session.add(row)
    await session.flush()
    return row


async def queue_for_plan(
    session: AsyncSession, *, plan: DailyPlan, timezone_name: str, moment=None
) -> NotificationDelivery | None:
    """The single daily notification, built from the plan's top action.

    One notification carrying the most important thing, rather than one per
    module. If the plan has nothing worth saying, nothing is queued at all.
    """
    if plan.status != "ready":
        return None
    candidates = (await session.execute(
        select(DailyPlanAction)
        .where(DailyPlanAction.plan_id == plan.id)
        .order_by(DailyPlanAction.priority)
    )).scalars().all()

    action = None
    eligible_kinds: Sequence[Any] | None = None
    body: str | None = None
    for candidate in candidates:
        if candidate.module == MODULE_MAINTENANCE:
            # Maintenance reminders are a per-kind opt-in. Without one we move
            # on to the next action rather than silently sending maintenance
            # text or dropping the day's notification altogether.
            if eligible_kinds is None:
                eligible_kinds = await maintenance_reminders_allowed(
                    session, plan.account_id, plan.plan_date,
                )
            if not eligible_kinds:
                continue
            # The plan's card names every due kind. The notification must name
            # only the ones the customer asked to hear about, so the copy is
            # rebuilt from the opted-in subset rather than reused.
            from app.domains.care.maintenance import maintenance_headline

            title, card_body = maintenance_headline(eligible_kinds)
            body = f"{title}. {card_body}".strip()
        action = candidate
        break
    if action is None:
        return None
    return await queue(
        session, account_id=plan.account_id, plan_date=plan.plan_date,
        notification_key="daily_plan", title=plan.headline,
        body=body if body is not None else f"{action.title}. {action.body}".strip(),
        module=action.module, timezone_name=timezone_name, moment=moment,
    )


async def queue_for_agenda(
    session: AsyncSession, *, account_id: uuid.UUID, plan_date: date,
    timezone_name: str, moment: datetime | None = None,
) -> NotificationDelivery | None:
    """Queue the first genuinely useful item from the typed Attention Agenda."""
    from app.domains.planning.agenda import build_agenda

    agenda = await build_agenda(session, account_id, generated_for=plan_date, timezone_name=timezone_name)
    candidate = next((item for item in agenda.items if item.notification_eligible), None)
    if candidate is None:
        return None
    if candidate.provenance.get("action_key") == "preparation:maintenance_timing":
        if not await maintenance_reminders_allowed(session, account_id, plan_date):
            return None
    return await queue(
        session, account_id=account_id, plan_date=plan_date,
        notification_key=candidate.key, title=candidate.title, body=candidate.body,
        module=candidate.domain, timezone_name=timezone_name, moment=moment,
        deep_link=candidate.destination, source_kind=candidate.source_kind,
        source_id=candidate.source_id, scheduled_for=moment,
    )


async def register_device(
    session: AsyncSession, account_id: uuid.UUID, *, device_key: str,
    platform: str, expo_push_token: str,
) -> NotificationDevice:
    """Idempotently register/rotate one account-owned device token."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = utcnow()
    await session.execute(pg_insert(NotificationDevice).values(
        account_id=account_id, device_key=device_key, platform=platform,
        expo_push_token=expo_push_token, status="active", last_seen_at=now,
        disabled_at=None,
    ).on_conflict_do_update(
        index_elements=["account_id", "device_key"],
        set_={"platform": platform, "expo_push_token": expo_push_token,
              "status": "active", "last_seen_at": now, "disabled_at": None},
    ))
    return (await session.execute(select(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.device_key == device_key,
    ))).scalar_one()


async def unregister_device(session: AsyncSession, account_id: uuid.UUID, device_key: str) -> bool:
    from sqlalchemy import delete

    result = await session.execute(delete(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.device_key == device_key,
    ))
    return bool(result.rowcount)


async def active_devices(session: AsyncSession, account_id: uuid.UUID) -> list[NotificationDevice]:
    return list((await session.execute(select(NotificationDevice).where(
        NotificationDevice.account_id == account_id, NotificationDevice.status == "active",
        NotificationDevice.disabled_at.is_(None),
    ).order_by(NotificationDevice.id))).scalars().all())


def serialize_preferences(row: NotificationPreference) -> dict[str, Any]:
    return {
        "enabled": row.enabled, "native_push_enabled": row.native_push_enabled,
        "daily_cap": row.daily_cap,
        "quiet_hours": {"start": row.quiet_hours_start, "end": row.quiet_hours_end},
        "preferred_hour": row.preferred_hour,
        "modules": {
            module: bool(row.modules.get(module, DEFAULT_MODULE_NOTIFICATIONS[module]))
            for module in MODULES
        },
        "timezone": row.timezone_name,
        "note": "At most one proactive appearance notification a day by default. Repeats are never sent twice.",
    }


def serialize_delivery(row: NotificationDelivery) -> dict[str, Any]:
    return {
        "id": str(row.id), "plan_date": row.plan_date.isoformat(),
        "notification_key": row.notification_key, "title": row.title, "body": row.body,
        "status": row.status, "suppressed_reason": row.suppressed_reason,
        "scheduled_for": row.scheduled_for.isoformat() if row.scheduled_for else None,
        "deep_link": row.deep_link, "source_kind": row.source_kind, "source_id": row.source_id,
        "provider_ticket_id": row.provider_ticket_id, "provider_error_code": row.provider_error_code,
        "attempted_at": row.attempted_at.isoformat() if row.attempted_at else None,
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
