"""Bounded, repeat-safe notification worker.

Invoke with ``python -m app.workers.notifications`` from a scheduler.  The
repository has no scheduler service of its own; a host cron should invoke this
command hourly.  A narrow one-hour local-time window means a missed run does
not send a stale notification late at night.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock, compiler, context as context_stage, notifications, push
from app.domains.planning.models import NotificationDelivery, NotificationPreference
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)


def _preference_due(row: NotificationPreference, now: datetime) -> bool:
    local = clock.local_now(row.timezone_name, moment=now)
    # Preferred time is hour precision. Running outside that hour is a miss,
    # intentionally avoiding late catch-up delivery.
    return local.hour == row.preferred_hour and not notifications.in_quiet_hours(
        local.hour, row.quiet_hours_start, row.quiet_hours_end,
    )


async def process_account(session: AsyncSession, preference: NotificationPreference, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    if not preference.enabled or not preference.native_push_enabled or not _preference_due(preference, now):
        return 0
    devices = await notifications.active_devices(session, preference.account_id)
    if not devices:
        return 0
    plan_date = clock.local_today(preference.timezone_name, moment=now)
    # The worker is proactive: it uses the same canonical Today compiler as
    # GET /today, without coupling notification delivery to a screen open.
    context = await context_stage.gather(session, account_id=preference.account_id, plan_date=plan_date)
    await compiler.compile_day(session, context=context, force=False, trigger="notification_worker")
    decision = await notifications.queue_for_agenda(
        session, account_id=preference.account_id, plan_date=plan_date,
        timezone_name=preference.timezone_name, moment=now,
    )
    if decision is None or decision.status != notifications.STATUS_QUEUED:
        return 0
    claim = await notifications.claim_delivery(session, decision.id)
    if claim is None:
        await session.rollback()
        return 0
    # Commit the claim before calling Expo. This is the duplicate-prevention
    # boundary; no transaction remains open across the network request.
    await session.commit()
    messages = [push.PushMessage(
        to=device.expo_push_token, title=decision.title, body=decision.body,
        data=({"destination": decision.deep_link, **(decision.destination_params or {})} if decision.deep_link else None),
    ) for device in devices]
    result = await push.send(messages)
    async with session.begin():
        row = await session.get(NotificationDelivery, decision.id, with_for_update=True)
        if row is None or row.status != notifications.STATUS_SENDING or row.claim_token != claim:
            return 0
        row.attempted_at = utcnow()
        outcomes = result.outcomes or []
        if result.sent:
            row.status = notifications.STATUS_PROVIDER_ACCEPTED
            row.sent_at = row.attempted_at
            accepted = next((item for item in outcomes if item.accepted), None)
            row.provider_ticket_id = accepted.ticket_id if accepted else (result.receipts[0] if result.receipts else None)
        else:
            row.status = notifications.STATUS_PROVIDER_FAILED
            errors = result.errors or []
            row.provider_error_code = errors[0][:80] if errors else "transport_failed"
        # Provider errors belong to the exact token that produced them.
        by_token = {item.token: item for item in outcomes}
        for device in devices:
            outcome = by_token.get(device.expo_push_token)
            if outcome and outcome.error == "DeviceNotRegistered":
                device.status = "disabled"
                device.disabled_at = utcnow()
        row.claim_token = None
        row.claimed_at = None
    return result.sent


async def process_once(*, now: datetime | None = None) -> int:
    factory = get_sessionmaker()
    total = 0
    async with factory() as session:
        rows = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.enabled.is_(True), NotificationPreference.native_push_enabled.is_(True),
        ))).scalars().all()
        for preference in rows:
            try:
                total += await process_account(session, preference, now=now)
            except Exception:  # noqa: BLE001 - one account must not stop the batch
                await session.rollback()
                logger.exception("notification_account_failed account=%s", preference.account_id)
    return total


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    sent = asyncio.run(process_once())
    logger.info("notification_worker_complete sent=%s", sent)


if __name__ == "__main__":
    main()
