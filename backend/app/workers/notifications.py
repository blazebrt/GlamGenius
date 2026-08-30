"""Bounded, repeat-safe notification worker.

Invoke with ``python -m app.workers.notifications`` from a scheduler.  The
repository has no scheduler service of its own; a host cron should invoke this
command hourly.  A narrow one-hour local-time window means a missed run does
not send a stale notification late at night.

Operating it
------------
``python -m app.workers.notifications``
    One production cycle.  Exits 0 when the batch completed, 2 when it did not.
    Writes one ``notification_worker_run`` log line and one heartbeat row in
    ``system_worker_status`` so a missed or failed run is visible afterwards.

``python -m app.workers.notifications --dry-run``
    The same cycle with the push transport switched off.  No socket is opened,
    so nothing can reach a device.

``python -m app.workers.notifications --account <uuid>``
    One account only, and only an account named in
    ``NOTIFICATION_TEST_ACCOUNT_IDS``.  Refuses to run when that list is empty.

Deployment procedure: ``docs/OPERATIONS.md`` section 6.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock, compiler, notifications, push
from app.domains.planning import context as context_stage
from app.domains.planning.models import NotificationDelivery, NotificationPreference
from app.domains.system.models import WorkerStatus
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)

WORKER_NAME = "notification_worker"

EXIT_OK = 0
EXIT_FAILED = 2
EXIT_REFUSED = 3


@dataclass
class RunSummary:
    """What one cycle did. Instrumentation only — it decides nothing."""

    accounts_considered: int = 0
    accounts_failed: int = 0
    notifications_sent: int = 0
    duration_ms: int = 0
    failed_account_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.accounts_failed

    def as_log_line(self) -> str:
        return (
            "notification_worker_run "
            f"outcome={'ok' if self.ok else 'degraded'} "
            f"accounts_considered={self.accounts_considered} "
            f"accounts_failed={self.accounts_failed} "
            f"notifications_sent={self.notifications_sent} "
            f"duration_ms={self.duration_ms}"
        )


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
    if decision is None or decision.status not in {
        notifications.STATUS_QUEUED, notifications.STATUS_SENDING,
    }:
        # queue_for_agenda records suppression decisions durably.  Commit them
        # before returning so the account can explain why nothing was sent.
        if decision is not None and decision.status == notifications.STATUS_SUPPRESSED:
            await session.commit()
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


async def process_once(*, now: datetime | None = None, summary: RunSummary | None = None) -> int:
    """Run one cycle. ``summary``, when given, is filled in as a side effect.

    The return value and every decision below are unchanged; ``summary`` only
    counts what already happened, so instrumentation cannot alter delivery.
    """
    factory = get_sessionmaker()
    total = 0
    async with factory() as session:
        # Hold plain identifiers, not ORM rows. A rollback expires every object
        # in the session's identity map, so reading an attribute off one
        # afterwards needs fresh IO from a synchronous attribute access and
        # raises MissingGreenlet — inside the very handler meant to contain the
        # failure. The rows still queued behind it are expired too, so a single
        # bad account would cost every later account its notification for that
        # hour, and the batch would look like "nothing was due" rather than an
        # error. Re-reading each account after the rollback keeps the failure
        # confined to the account that caused it.
        account_ids = list((await session.execute(
            select(NotificationPreference.account_id).where(
                NotificationPreference.enabled.is_(True),
                NotificationPreference.native_push_enabled.is_(True),
            )
        )).scalars().all())
        if summary is not None:
            summary.accounts_considered = len(account_ids)
        for account_id in account_ids:
            try:
                preference = (await session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.account_id == account_id,
                    )
                )).scalar_one_or_none()
                if preference is None:
                    # Withdrawn between the two reads; nothing to send.
                    continue
                total += await process_account(session, preference, now=now)
            except Exception:  # noqa: BLE001 - one account must not stop the batch
                await session.rollback()
                if summary is not None:
                    summary.accounts_failed += 1
                    summary.failed_account_ids.append(str(account_id))
                logger.exception("notification_account_failed account=%s", account_id)
    if summary is not None:
        summary.notifications_sent = total
    return total


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
async def record_heartbeat(summary: RunSummary, *, error: str | None = None) -> None:
    """Write what this run did to ``system_worker_status``.

    A batch process cannot notice its own absence, so the record of the last
    run is what makes a *missed* run visible: GET /api/v2/admin/workers reports
    the age of this row, and an hourly worker whose last run is hours old is
    the alert. Never raises — a heartbeat that fails must not fail the run that
    already delivered.
    """
    values = {
        "worker_name": WORKER_NAME,
        "last_heartbeat_at": func.now(),
        "last_attempted_job_at": func.now(),
        "service_version": os.environ.get("COMMIT_SHA", os.environ.get("APP_VERSION", "unknown")),
    }
    if error is None and summary.ok:
        values["last_successful_job_at"] = func.now()
        values["last_error_code"] = None
        values["last_error_summary"] = None
    else:
        values["last_error_code"] = (error or "accounts_failed")[:64]
        values["last_error_summary"] = (
            f"{summary.accounts_failed} of {summary.accounts_considered} accounts failed"
            if error is None
            else f"Run failed: {error}"
        )[:255]
        values["last_error_at"] = func.now()

    try:
        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(
                pg_insert(WorkerStatus)
                .values(started_at=func.now(), **values)
                .on_conflict_do_update(index_elements=["worker_name"], set_=values)
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - observability must never break delivery
        logger.exception("notification_worker_heartbeat_failed")


def _report(summary: RunSummary, *, error: str | None = None) -> None:
    """One line per run, whatever happened."""
    if error is not None:
        logger.error("%s error=%s", summary.as_log_line(), error)
    elif summary.ok:
        logger.info(summary.as_log_line())
    else:
        logger.error("%s failed_accounts=%s", summary.as_log_line(), ",".join(summary.failed_account_ids))

    if error is not None or not summary.ok:
        # Sentry is optional; when no DSN is configured this is a no-op.
        try:
            import sentry_sdk  # noqa: PLC0415

            sentry_sdk.capture_message(
                f"notification worker run degraded: {summary.as_log_line()}",
                level="error",
            )
        except Exception:  # noqa: BLE001 - never let reporting break the run
            pass


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
async def run_cycle(*, now: datetime | None = None) -> RunSummary:
    """One production cycle, instrumented. Used by the hourly scheduler."""
    summary = RunSummary()
    started = time.monotonic()
    error: str | None = None
    try:
        await process_once(now=now, summary=summary)
    except Exception as exc:  # noqa: BLE001 - the batch itself failed
        error = f"{type(exc).__name__}: {exc}"[:200]
        logger.exception("notification_worker_failed")
    summary.duration_ms = int((time.monotonic() - started) * 1000)
    _report(summary, error=error)
    await record_heartbeat(summary, error=error)
    if error is not None:
        raise RuntimeError(error)
    return summary


def _nominated_test_accounts() -> set[str]:
    from app import config  # noqa: PLC0415 - read at call time so tests can set it

    raw = os.environ.get("NOTIFICATION_TEST_ACCOUNT_IDS")
    if raw is not None:
        return {value.strip() for value in raw.split(",") if value.strip()}
    return set(config.NOTIFICATION_TEST_ACCOUNT_IDS)


class NotNominated(RuntimeError):
    """Raised when a manual run targets an account nobody nominated for testing."""


async def run_for_account(account_id: str, *, now: datetime | None = None) -> RunSummary:
    """Manual trigger: one account, and only a nominated test account.

    This is the safety boundary for testing by hand. It refuses when no test
    account is nominated and when the requested account is not among them, so
    a manual run can never reach the customer base. The decision logic it then
    calls is exactly the logic the hourly run uses — unchanged and unbypassed.
    """
    nominated = _nominated_test_accounts()
    if not nominated:
        raise NotNominated(
            "NOTIFICATION_TEST_ACCOUNT_IDS is empty. Nominate the account ids that may "
            "receive a manual test notification before running this."
        )
    if str(account_id) not in nominated:
        raise NotNominated(
            f"Account {account_id} is not in NOTIFICATION_TEST_ACCOUNT_IDS. "
            "A manual run may only target a nominated test account."
        )

    summary = RunSummary()
    started = time.monotonic()
    factory = get_sessionmaker()
    async with factory() as session:
        preference = (await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.account_id == uuid_module.UUID(str(account_id)),
            )
        )).scalar_one_or_none()
        if preference is None:
            logger.warning("notification_manual_no_preference account=%s", account_id)
        else:
            summary.accounts_considered = 1
            try:
                summary.notifications_sent = await process_account(session, preference, now=now)
            except Exception:  # noqa: BLE001
                await session.rollback()
                summary.accounts_failed = 1
                summary.failed_account_ids.append(str(account_id))
                logger.exception("notification_account_failed account=%s", account_id)
    summary.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info("%s mode=manual account=%s", summary.as_log_line(), account_id)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.notifications",
        description=(
            "Run one notification cycle. With no arguments this is the hourly "
            "production command; see docs/OPERATIONS.md section 6."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do everything except deliver: the push transport is switched off, "
             "so no socket is opened and nothing can reach a device.",
    )
    parser.add_argument(
        "--account", metavar="UUID", default=None,
        help="Process only this account. Allowed only for an account listed in "
             "NOTIFICATION_TEST_ACCOUNT_IDS.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        # Set before any delivery path runs. push.send() reads this at call
        # time, so from here on no push can leave the process.
        os.environ["PUSH_DELIVERY_MODE"] = "dry_run"
        logger.info("notification_worker_dry_run host=%s", socket.gethostname())

    try:
        if args.account:
            asyncio.run(run_for_account(args.account))
        else:
            asyncio.run(run_cycle())
    except NotNominated as exc:
        logger.error("notification_worker_refused reason=%s", exc)
        return EXIT_REFUSED
    except Exception:  # noqa: BLE001 - already logged and recorded by run_cycle
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
