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

from app.domains.identity.models import ACCOUNT_STATUS_ACTIVE, Account
from app.domains.planning import clock, compiler, notifications, push
from app.domains.planning import context as context_stage
from app.domains.planning.models import NotificationDelivery, NotificationPreference
from app.domains.system.models import WorkerStatus
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.workers.schedule import NOTIFICATION_WORKER_NAME, service_version

logger = logging.getLogger(__name__)

#: One name, from the schedule authority, so readiness and the admin
#: endpoint cannot end up watching a worker under a different spelling.
WORKER_NAME = NOTIFICATION_WORKER_NAME

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


def _active_accounts():
    """The one definition of who may be given new proactive work.

    An account that is not ``active`` — one whose deletion has been requested
    — gets nothing new: no compiled day, no queued decision, no claimed push.
    The production cycle selects through it and :func:`process_account`, which
    the manual path also runs, checks through it, so the two cannot drift.
    """
    return select(Account.id).where(Account.status == ACCOUNT_STATUS_ACTIVE)


async def _account_is_active(session: AsyncSession, account_id: uuid_module.UUID) -> bool:
    return bool(await session.scalar(
        select(_active_accounts().where(Account.id == account_id).exists())
    ))


async def _settle_if_account_inactive(
    session: AsyncSession, account_id: uuid_module.UUID,
    delivery_id: uuid_module.UUID, claim: str,
) -> bool:
    """The final lifecycle gate and the provider-attempt marker, just before the provider.

    The first gate in :func:`process_account` runs before the day is compiled.
    Deletion can be requested after that, while the triggers run or while the
    claim commits. So the account is checked again here, in a short
    transaction of its own that ends before ``push.send``. No transaction or
    lock is held across the provider request.

    If the account is still active, the same transaction records the attempt
    (:func:`notifications.mark_attempt_started`) and commits it, and only then
    returns ``False`` so the caller sends. From that commit on, ``attempted_at``
    says the provider may have been reached, and the row is never claimed
    again. A crash before it leaves ``attempted_at`` empty: provably unsent,
    and recoverable once its lease expires. A deletion request that commits
    after this check finds that send already under way, and it is treated as
    in-flight work, not new work. If the claim is no longer this worker's, or
    an attempt was already recorded, nothing is sent (``True``).

    Otherwise returns ``True`` and nothing is sent. The delivery this worker
    claimed is settled as suppressed, reason ``account_inactive``, with the
    claim cleared, under the row lock and only if the claim is still ours.
    It does not stay ``sending``, where a stale-claim sweep could pick it up
    later. It does not return to ``queued``, where a reactivated account would
    send it late. ``suppressed`` is never claimed again, and queueing the
    same notification again returns this row.
    """
    async with session.begin():
        if await _account_is_active(session, account_id):
            if await notifications.mark_attempt_started(session, delivery_id, claim):
                return False
            logger.warning("notification_attempt_not_marked delivery=%s", delivery_id)
            return True
        row = await session.get(
            NotificationDelivery, delivery_id,
            with_for_update=True, populate_existing=True,
        )
        if row is not None and row.status == notifications.STATUS_SENDING and row.claim_token == claim:
            row.status = notifications.STATUS_SUPPRESSED
            row.suppressed_reason = notifications.SUPPRESSED_ACCOUNT_INACTIVE
            row.claim_token = None
            row.claimed_at = None
    logger.info("notification_withheld_account_inactive delivery=%s", delivery_id)
    return True


def _preference_due(row: NotificationPreference, now: datetime) -> bool:
    local = clock.local_now(row.timezone_name, moment=now)
    # Preferred time is hour precision. Running outside that hour is a miss,
    # intentionally avoiding late catch-up delivery.
    return local.hour == row.preferred_hour and not notifications.in_quiet_hours(
        local.hour, row.quiet_hours_start, row.quiet_hours_end,
    )


async def process_account(session: AsyncSession, preference: NotificationPreference, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    # First, before any work is compiled or queued for this account.
    if not await _account_is_active(session, preference.account_id):
        return 0
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
    # Ordered, real conditions.  The shared outbox still enforces one daily
    # delivery, quiet hours and repeat safety no matter which condition wins.
    # A material notice about a product the customer explicitly chose to watch
    # is considered first; it returns nothing unless one is actually waiting,
    # so an ordinary reminder keeps the slot on every other day. It is also
    # where a Product Watch delivery abandoned before the provider was reached
    # is re-proved and recovered (``product_watch.queue_material_notice``).
    #
    # A candidate the customer switched off for its own topic is recorded as
    # suppressed and the walk continues: turning off Care does not turn off
    # Maintenance. Every other decision ends it. An actual delivery owns the
    # day's one slot, and the master switch, quiet hours, the daily cap and an
    # inactive account apply to every candidate after it too.
    decision = None
    for trigger in (
        notifications.queue_for_product_watch,
        notifications.queue_for_environment_crossing,
        notifications.queue_for_protocol_day,
        notifications.queue_for_running_out,
        notifications.queue_for_deferred_purchase_relevance,
        notifications.queue_for_agenda,
    ):
        candidate = await trigger(
            session, account_id=preference.account_id, plan_date=plan_date,
            timezone_name=preference.timezone_name, moment=now,
        )
        if candidate is None or notifications.is_candidate_opt_out(candidate):
            continue
        decision = candidate
        break
    if decision is None or decision.status not in {
        notifications.STATUS_QUEUED, notifications.STATUS_SENDING,
    }:
        # Nothing to send. Everything this account decided — the compiled day,
        # suppression decisions recorded so the account can explain why nothing
        # was sent, any watch cursor that moved — is committed here, inside this
        # account's own boundary, so no lock or unsaved row outlives it.
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
        data=({"delivery_id": str(decision.id), "destination": decision.deep_link, **(decision.destination_params or {})} if decision.deep_link else None),
        category_id=(decision.destination_params or {}).get("category_id"),
    ) for device in devices]
    # The last thing before the provider: the account must still be active.
    if await _settle_if_account_inactive(session, preference.account_id, decision.id, claim):
        return 0
    result = await push.send(messages)
    async with session.begin():
        row = await session.get(NotificationDelivery, decision.id, with_for_update=True)
        if row is None or row.status != notifications.STATUS_SENDING or row.claim_token != claim:
            return 0
        # ``attempted_at`` was committed before the provider was called. It is
        # the marker, and settling leaves it as it was.
        outcomes = result.outcomes or []
        if result.sent:
            row.status = notifications.STATUS_PROVIDER_ACCEPTED
            row.sent_at = utcnow()
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
    # Discovery is one short read, and its session is closed before any account
    # is processed. Only plain identifiers leave it, never ORM rows, so no
    # object, lock or transaction from here reaches an account's work. The
    # order is fixed, so a batch always visits accounts the same way.
    async with factory() as session:
        account_ids = list((await session.execute(
            select(NotificationPreference.account_id).where(
                NotificationPreference.enabled.is_(True),
                NotificationPreference.native_push_enabled.is_(True),
                NotificationPreference.account_id.in_(_active_accounts()),
            ).order_by(NotificationPreference.account_id)
        )).scalars().all())
    if summary is not None:
        summary.accounts_considered = len(account_ids)
    for account_id in account_ids:
        try:
            total += await _process_in_own_session(factory, account_id, now=now)
        except Exception:  # noqa: BLE001 - one account must not stop the batch
            if summary is not None:
                summary.accounts_failed += 1
                summary.failed_account_ids.append(str(account_id))
            logger.exception("notification_account_failed account=%s", account_id)
    if summary is not None:
        summary.notifications_sent = total
    return total


async def _process_in_own_session(factory, account_id: uuid_module.UUID, *, now: datetime | None) -> int:
    """One account, in a session of its own that ends before the next account starts.

    Every account gets a fresh session: its own transaction, its own identity
    map, its own row locks. :func:`process_account` commits what the account
    decided before it returns, whether or not it sends. A failure rolls back
    this account's work, and only this account's. Leaving the block closes the
    session, which ends any transaction still open, so nothing this account
    locked or left unsaved can be committed, rolled back or waited on by the
    account after it.
    """
    async with factory() as session:
        try:
            preference = (await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.account_id == account_id,
                )
            )).scalar_one_or_none()
            if preference is None:
                # Withdrawn between discovery and now; nothing to send.
                return 0
            return await process_account(session, preference, now=now)
        except Exception:
            await session.rollback()
            raise


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
        "service_version": service_version(),
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
