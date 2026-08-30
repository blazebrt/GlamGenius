"""Operating the notification worker: duplicate safety, visibility, safe testing.

The worker's decision logic is covered elsewhere. This module covers the
questions an operator asks: if a run overlaps or is retried, can a customer be
notified twice? If a run fails or never happens, can I see it? Can I test by
hand without notifying real people?
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid
from datetime import timedelta

import pytest
from app.domains.planning import clock, notifications, push
from app.domains.planning.models import NotificationDelivery, NotificationDevice, NotificationPreference
from app.domains.system.models import WorkerStatus
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.workers import notifications as worker
from sqlalchemy import select


async def _opt_in(account_id, *, hour: int, timezone_name: str = clock.DEFAULT_TIMEZONE):
    """An account that wants a notification, with one registered device."""
    factory = get_sessionmaker()
    async with factory() as session:
        preference = await notifications.preferences_for(session, account_id, timezone_name)
        preference.enabled = True
        preference.native_push_enabled = True
        preference.preferred_hour = hour
        preference.quiet_hours_start = 0
        preference.quiet_hours_end = 0
        await notifications.register_device(
            session, account_id, device_key=f"install-{uuid.uuid4().hex[:8]}",
            platform="android", expo_push_token=f"ExponentPushToken[{uuid.uuid4().hex}]",
        )
        await session.commit()


def _at_local_hour(hour: int, timezone_name: str = clock.DEFAULT_TIMEZONE):
    """A UTC moment whose local hour in ``timezone_name`` is ``hour``."""
    probe = utcnow().replace(minute=0, second=0, microsecond=0)
    for _ in range(48):
        if clock.local_now(timezone_name, moment=probe).hour == hour:
            return probe
        probe += timedelta(hours=1)
    raise AssertionError(f"no UTC hour maps to local hour {hour}")


class _CountingPush:
    """Stands in for Expo. Records every batch it is asked to deliver."""

    def __init__(self) -> None:
        self.batches: list[list] = []

    async def send(self, messages):
        self.batches.append(list(messages))
        return push.PushResult(
            sent=len(messages), failed=0,
            receipts=[uuid.uuid4().hex for _ in messages],
            errors=[],
            outcomes=[push.PushOutcome(m.to, True, ticket_id=uuid.uuid4().hex) for m in messages],
        )

    @property
    def messages_sent(self) -> int:
        return sum(len(batch) for batch in self.batches)


# ---------------------------------------------------------------------------
# A duplicate run sends nothing twice
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_repeated_run_in_the_same_hour_sends_nothing_twice(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The acceptance criterion: run the worker twice, one notification only.

    This is the scenario a scheduler actually produces — a retry after a slow
    run, or two invocations overlapping the same hour.
    """
    _, account_id = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_id, hour=9)

    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)

    first = await worker.process_once(now=moment)
    second = await worker.process_once(now=moment)

    assert second == 0, "the second run must not deliver anything"
    assert sender.messages_sent == first, (
        f"push was called {sender.messages_sent} times for {first} notification(s); "
        "a repeated run reached the provider again"
    )

    factory = get_sessionmaker()
    async with factory() as session:
        delivered = list((await session.execute(select(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.status.in_((
                notifications.STATUS_PROVIDER_ACCEPTED, notifications.STATUS_SENDING,
            )),
        ))).scalars().all())
    assert len(delivered) <= 1, "one hour produced more than one live delivery row"


@pytest.mark.asyncio
async def test_an_overlapping_run_cannot_claim_the_same_delivery(db_clean, registered_supabase_user):
    """Two runs racing for one queued row: exactly one wins the claim.

    The claim is committed before the provider call, so the loser has nothing
    to send rather than sending a copy.
    """
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        row = NotificationDelivery(
            account_id=account_id, plan_date=utcnow().date(), notification_key="agenda",
            dedup_hash=uuid.uuid4().hex, title="Today", status=notifications.STATUS_QUEUED,
        )
        session.add(row)
        await session.commit()
        delivery_id = row.id

    async with factory() as session:
        first = await notifications.claim_delivery(session, delivery_id)
        await session.commit()
    async with factory() as session:
        second = await notifications.claim_delivery(session, delivery_id)
        await session.commit()

    assert first is not None, "the first run must be able to claim the delivery"
    assert second is None, "a second run claimed a delivery that was already claimed"


@pytest.mark.asyncio
async def test_quiet_hours_stop_a_run_that_fires_at_the_wrong_time(
    db_clean, registered_supabase_user, monkeypatch,
):
    """A scheduler firing inside quiet hours must deliver nothing."""
    _, account_id = await registered_supabase_user()
    await _opt_in(account_id, hour=9)
    factory = get_sessionmaker()
    async with factory() as session:
        preference = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_id,
        ))).scalar_one()
        preference.quiet_hours_start = 8
        preference.quiet_hours_end = 10
        await session.commit()

    sender = _CountingPush()
    monkeypatch.setattr(worker.push, "send", sender.send)

    assert await worker.process_once(now=_at_local_hour(9)) == 0
    assert sender.messages_sent == 0, "a notification was delivered inside quiet hours"


# ---------------------------------------------------------------------------
# A failed or missed run is visible
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_successful_run_records_a_heartbeat(db_clean):
    summary = await worker.run_cycle(now=utcnow())
    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(select(WorkerStatus).where(
            WorkerStatus.worker_name == worker.WORKER_NAME,
        ))).scalar_one_or_none()
    assert row is not None, "a completed run left no heartbeat, so a missed run cannot be detected"
    assert row.last_successful_job_at is not None
    assert row.last_error_code is None
    assert summary.ok


@pytest.mark.asyncio
async def test_a_failed_run_is_recorded_and_exits_non_zero(db_clean, monkeypatch):
    """A run that blows up must leave evidence and tell the scheduler."""
    async def _explode(**_kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(worker, "process_once", _explode)

    with pytest.raises(RuntimeError):
        await worker.run_cycle()

    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(select(WorkerStatus).where(
            WorkerStatus.worker_name == worker.WORKER_NAME,
        ))).scalar_one_or_none()
    assert row is not None, "a failed run left no trace"
    assert row.last_error_code is not None
    assert row.last_error_at is not None


# The command line is exercised in a subprocess, not in-process. main() owns its
# event loop through asyncio.run(), and this suite deliberately shares one loop
# for the whole session (see pytest.ini) — calling asyncio.run() inside a test
# would tear that loop down and break every async test that follows. A
# subprocess is also the more faithful check: it is exactly how a scheduler
# invokes the worker, exit code and all.
def _run_cli(args: list[str], **env_overrides) -> subprocess.CompletedProcess:
    backend_root = pathlib.Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(backend_root), **env_overrides}
    return subprocess.run(
        [sys.executable, "-m", "app.workers.notifications", *args],
        cwd=backend_root, env=env, capture_output=True, text=True, timeout=120,
    )


def test_a_healthy_run_exits_zero_and_logs_one_summary_line():
    result = _run_cli([])
    assert result.returncode == worker.EXIT_OK, result.stderr
    runs = [line for line in result.stderr.splitlines() if "notification_worker_run" in line]
    assert len(runs) == 1, f"expected exactly one run summary line, got {len(runs)}"
    assert "accounts_considered=" in runs[0]
    assert "notifications_sent=" in runs[0]


def test_a_failed_run_exits_non_zero_so_the_scheduler_notices():
    """An unreachable database is a failed run, and the scheduler must be told."""
    result = _run_cli(
        [], POSTGRES_URL="postgresql+asyncpg://glamgenius@127.0.0.1:1/nonexistent",
    )
    assert result.returncode == worker.EXIT_FAILED, (
        f"a run against an unreachable database exited {result.returncode}; "
        "a scheduler would treat that as success"
    )
    assert "notification_worker_run" in result.stderr, "a failed run logged no summary line"


def test_a_manual_run_for_an_unnominated_account_is_refused_at_the_command_line():
    result = _run_cli(
        ["--account", str(uuid.uuid4())], NOTIFICATION_TEST_ACCOUNT_IDS="",
    )
    assert result.returncode == worker.EXIT_REFUSED, result.stderr
    assert "NOTIFICATION_TEST_ACCOUNT_IDS" in result.stderr


def test_the_dry_run_flag_switches_the_transport_off_before_anything_runs(monkeypatch):
    """--dry-run must take effect before any delivery path is reachable.

    asyncio.run is replaced rather than executed, so this asserts the ordering
    inside main() without starting a second event loop.
    """
    seen: dict[str, str] = {}

    def _capture(coro):
        coro.close()
        seen["mode"] = push._delivery_mode()
        return None

    monkeypatch.delenv("PUSH_DELIVERY_MODE", raising=False)
    monkeypatch.setattr(worker.asyncio, "run", _capture)

    assert worker.main(["--dry-run"]) == worker.EXIT_OK
    assert seen["mode"] == "dry_run", "the run started with the live transport still enabled"


def test_the_run_summary_line_names_accounts_and_notifications():
    """One log line per run, carrying the two numbers an operator needs."""
    line = worker.RunSummary(
        accounts_considered=12, accounts_failed=0, notifications_sent=5, duration_ms=830,
    ).as_log_line()
    assert "accounts_considered=12" in line
    assert "notifications_sent=5" in line
    assert "outcome=ok" in line
    degraded = worker.RunSummary(accounts_considered=3, accounts_failed=1).as_log_line()
    assert "outcome=degraded" in degraded


# ---------------------------------------------------------------------------
# Testing by hand cannot reach real users
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_manual_run_is_refused_when_no_test_account_is_nominated(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_TEST_ACCOUNT_IDS", "")
    with pytest.raises(worker.NotNominated):
        await worker.run_for_account(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_a_manual_run_is_refused_for_an_account_that_was_not_nominated(monkeypatch):
    nominated, other = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv("NOTIFICATION_TEST_ACCOUNT_IDS", nominated)
    with pytest.raises(worker.NotNominated):
        await worker.run_for_account(other)


@pytest.mark.asyncio
async def test_a_manual_run_touches_only_the_nominated_account(
    db_clean, registered_supabase_user, monkeypatch,
):
    """A bystander who is also due must not be notified by a manual run."""
    _, target = await registered_supabase_user()
    _, bystander = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(target, hour=9)
    await _opt_in(bystander, hour=9)

    sender = _CountingPush()
    monkeypatch.setattr(worker.push, "send", sender.send)
    monkeypatch.setenv("NOTIFICATION_TEST_ACCOUNT_IDS", str(target))

    summary = await worker.run_for_account(str(target), now=moment)

    assert summary.accounts_considered == 1
    factory = get_sessionmaker()
    async with factory() as session:
        bystander_rows = list((await session.execute(select(NotificationDelivery).where(
            NotificationDelivery.account_id == bystander,
            NotificationDelivery.status.in_((
                notifications.STATUS_SENDING, notifications.STATUS_PROVIDER_ACCEPTED,
            )),
        ))).scalars().all())
    assert bystander_rows == [], "a manual run delivered to an account nobody nominated"


@pytest.mark.asyncio
async def test_dry_run_opens_no_socket_and_delivers_nothing(monkeypatch):
    """The dry-run switch is read inside push.send, so no caller can bypass it."""
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("dry run attempted a network call to the push provider")

    monkeypatch.setenv("PUSH_DELIVERY_MODE", "dry_run")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _forbidden)

    result = await push.send([
        push.PushMessage(to="ExponentPushToken[real-user]", title="T", body="B"),
    ])

    assert result.sent == 0, "a dry run reported a delivery"
    assert result.errors == [push.DRY_RUN_ERROR]
    assert [o.accepted for o in (result.outcomes or [])] == [False]


@pytest.mark.asyncio
async def test_dry_run_never_marks_a_delivery_as_accepted(
    db_clean, registered_supabase_user, monkeypatch,
):
    """A dry run must not consume the daily cap by looking like a success."""
    _, account_id = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_id, hour=9)
    monkeypatch.setenv("PUSH_DELIVERY_MODE", "dry_run")

    await worker.process_once(now=moment)

    factory = get_sessionmaker()
    async with factory() as session:
        accepted = list((await session.execute(select(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.status == notifications.STATUS_PROVIDER_ACCEPTED,
        ))).scalars().all())
    assert accepted == [], "a dry run recorded a delivery as accepted by the provider"


@pytest.mark.asyncio
async def test_a_device_registered_for_the_account_is_the_only_target(
    db_clean, registered_supabase_user, monkeypatch,
):
    """A manual test run reaches the nominated account's own devices, nobody else's."""
    _, target = await registered_supabase_user()
    _, bystander = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(target, hour=9)
    await _opt_in(bystander, hour=9)

    sender = _CountingPush()
    monkeypatch.setattr(worker.push, "send", sender.send)
    monkeypatch.setenv("NOTIFICATION_TEST_ACCOUNT_IDS", str(target))
    await worker.run_for_account(str(target), now=moment)

    factory = get_sessionmaker()
    async with factory() as session:
        bystander_tokens = {
            row.expo_push_token for row in (await session.execute(select(NotificationDevice).where(
                NotificationDevice.account_id == bystander,
            ))).scalars().all()
        }
    targeted = {message.to for batch in sender.batches for message in batch}
    assert not (targeted & bystander_tokens), "a manual run addressed another account's device"
