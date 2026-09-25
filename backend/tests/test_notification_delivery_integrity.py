"""Lane D — notification delivery integrity, against PostgreSQL 16.

Three defects in how the hourly worker delivers, each proved here with the real
worker, the real domain triggers and real row locks. No real push is ever sent:
the provider is a counting stand-in for every test.

* S — **one session per account.** The worker used one session for the whole
  batch. An account that locked its preference and watch rows and sent nothing
  left those locks, and any unsaved work, inside the transaction the next
  account then used.
* T — **a topic opt-out is one candidate's, not the day's.** The worker stopped
  at the first decision of any kind, so a Care routine suppressed because Care
  is off ended the walk before an enabled Maintenance or Event Preparation
  reminder was ever considered.
* R — **a claim is not an attempt, and an abandoned claim is recovered.** A
  Product Watch delivery claimed and committed, then abandoned before the
  provider was called, was stranded forever: its cursor had already consumed
  the event, and nothing looked for the row again.

No timing sleeps. Every interleaving is built from explicit pauses, and the
only waiting is a bounded poll of PostgreSQL's own lock tables.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta

import pytest
from app.domains.care import maintenance_service
from app.domains.planning import clock, notification_strings, notifications, push
from app.domains.planning import service as planning_service
from app.domains.planning.models import NotificationDelivery, NotificationDevice, NotificationPreference
from app.domains.planning.schemas import CalendarEventInput
from app.domains.product import watch as product_watch
from app.domains.product.models import ProductWatch
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.workers import notifications as worker
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from tests.test_domain_routines_api import _generate, _seeded_shelf
from tests.test_notification_worker_operations import _at_local_hour, _CountingPush, _opt_in
from tests.test_official_records_api import off_clean  # noqa: F401 - re-exported fixture
from tests.test_step12c_product_watch import (
    _claim,
    _confirm,
    _delete_watch,
    _device,
    _ingest,
    _put_watch,
    _read_watch,
    _set_preferences,
    _watch_deliveries,
    _watch_row,
    _watching,
)

TZ = clock.DEFAULT_TIMEZONE


class ProcessDied(BaseException):
    """The worker process ending mid-cycle. Nothing in the worker handles it."""


class _Pause:
    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> None:
        self.reached.set()
        await self.release.wait()

    async def wait_reached(self) -> None:
        # A ceiling for a broken build, not a pace.
        await asyncio.wait_for(self.reached.wait(), timeout=30)


@pytest.fixture
async def race():
    """Pauses and tasks for one race, all released and finished at teardown."""
    pauses: list[_Pause] = []
    tasks: list[asyncio.Task] = []

    class Race:
        def pause(self) -> _Pause:
            pauses.append(_Pause())
            return pauses[-1]

        def spawn(self, coroutine) -> asyncio.Task:
            tasks.append(asyncio.create_task(coroutine))
            return tasks[-1]

    yield Race()
    for pause in pauses:
        pause.release.set()
    for task in tasks:
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30)
            except BaseException:  # noqa: BLE001 - teardown: finish, whatever it says
                task.cancel()
        elif not task.cancelled():
            task.exception()


def _factory():
    return get_sessionmaker()


def _sender(monkeypatch) -> _CountingPush:
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    return sender


async def _rows(account_id) -> list[NotificationDelivery]:
    async with _factory()() as session:
        return list((await session.execute(
            select(NotificationDelivery).where(NotificationDelivery.account_id == account_id)
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
        )).scalars().all())


async def _devices(account_id) -> list[NotificationDevice]:
    async with _factory()() as session:
        return list((await session.execute(select(NotificationDevice).where(
            NotificationDevice.account_id == account_id,
        ))).scalars().all())


async def _preference(account_id) -> NotificationPreference:
    async with _factory()() as session:
        return (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_id,
        ))).scalar_one()


async def _two_accounts_in_batch_order(registered_supabase_user):
    """Two accounts, returned in the order the worker visits them."""
    first = await registered_supabase_user()
    second = await registered_supabase_user()
    return tuple(sorted((first, second), key=lambda pair: str(pair[1])))


def _ordinary_reminder():
    """An enabled, due agenda reminder: the real outbox decides about it."""
    async def queue_for_agenda(session, *, account_id, plan_date, timezone_name, moment=None):
        return await notifications.queue(
            session, account_id=account_id, plan_date=plan_date, notification_key="agenda:ordinary",
            title="Your look for today", module="outfit", topic="today_style",
            timezone_name=timezone_name, moment=moment, scheduled_for=moment,
        )
    return queue_for_agenda


async def _nowait(sql: str, **params) -> str:
    """Try to take a row lock without waiting. Returns ``"acquired"`` or the error."""
    async with _factory()() as probe:
        try:
            await probe.execute(text(sql), params)
            return "acquired"
        except DBAPIError as refused:
            return type(refused.orig).__name__ if refused.orig is not None else "DBAPIError"
        finally:
            await probe.rollback()


async def _until_a_backend_waits_on_a_lock() -> None:
    """Poll PostgreSQL until some backend of this database is waiting for a lock."""
    for _ in range(3000):
        async with _factory()() as watcher:
            waiting = await watcher.scalar(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            ))
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no backend ever waited on a lock")


# ===========================================================================
# S — one session per account
# ===========================================================================
def _record_processing_order(monkeypatch) -> list:
    """The accounts in the order the worker actually visits them.

    Recorded rather than assumed, so a test holds whatever order a batch uses.
    """
    order: list = []
    gather = worker.context_stage.gather

    async def recording_gather(session, *, account_id, plan_date):
        # Other code compiles through the same module too; each account once.
        if account_id not in order:
            order.append(account_id)
        return await gather(session, account_id=account_id, plan_date=plan_date)

    monkeypatch.setattr(worker.context_stage, "gather", recording_gather)
    return order


ONLY_PRODUCT_WATCH = {"today_style": False, "care": False, "event_preparation": False, "maintenance": False}


@pytest.mark.asyncio
async def test_s_a_a_no_send_account_releases_its_preference_and_watch_locks_before_the_next_account(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch, race,  # noqa: F811
):
    """The first account takes the real Product Watch locks and sends nothing.

    ``queue_material_notice`` locks that account's notification preference and
    then its watch rows, finds no notice, and returns nothing; nothing else is
    due for it either. While the worker is inside the second account, a second
    connection asks for both of the first account's rows with NOWAIT. Under one
    shared session they were still locked.
    """
    accounts = []
    for _ in range(2):
        token, account_id = await registered_supabase_user()
        device = await _device(app_client)
        await _claim(app_client, device, token)
        await _watching(app_client, device, token, account_id)
        await _opt_in(account_id, hour=9)
        await _set_preferences(account_id, topics=ONLY_PRODUCT_WATCH)
        accounts.append(account_id)
    moment = _at_local_hour(9)
    sender = _sender(monkeypatch)
    order = _record_processing_order(monkeypatch)
    inside_second = race.pause()
    recorded_gather = worker.context_stage.gather

    async def pause_inside_the_second(session, *, account_id, plan_date):
        result = await recorded_gather(session, account_id=account_id, plan_date=plan_date)
        if len(order) == 2 and account_id == order[1] and not inside_second.reached.is_set():
            await inside_second()
        return result

    monkeypatch.setattr(worker.context_stage, "gather", pause_inside_the_second)
    run = race.spawn(worker.process_once(now=moment))
    await inside_second.wait_reached()
    first = order[0]

    preference_lock = await _nowait(
        "SELECT 1 FROM notification_preferences WHERE account_id = :a FOR UPDATE NOWAIT", a=first,
    )
    watch_lock = await _nowait(
        "SELECT 1 FROM product_watches WHERE account_id = :a FOR UPDATE NOWAIT", a=first,
    )
    inside_second.release.set()
    await asyncio.wait_for(run, timeout=60)

    assert sorted(order, key=str) == sorted(accounts, key=str)
    assert preference_lock == "acquired", f"the first account's preference was still locked: {preference_lock}"
    assert watch_lock == "acquired", f"the first account's watch was still locked: {watch_lock}"
    assert sender.batches == [], "neither account had anything to send"


@pytest.mark.asyncio
async def test_s_b_work_an_account_never_committed_cannot_be_committed_by_the_next_account(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The first account leaves work unsaved and returns; the second's commits must not keep it."""
    _, account_x = await registered_supabase_user()
    _, account_y = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_x, hour=9)
    await _opt_in(account_y, hour=9)
    sender = _sender(monkeypatch)
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder())
    order = _record_processing_order(monkeypatch)
    seen: list = []
    active_devices = notifications.active_devices

    async def devices(session, account_id):
        seen.append(account_id)
        if len(seen) == 1:
            # Account-local work the first account never commits: a change to
            # its own row and a new row, both left unsaved in its session.
            # Then it returns with nothing to do.
            row = (await session.execute(select(NotificationPreference).where(
                NotificationPreference.account_id == account_id,
            ))).scalar_one()
            row.daily_cap = 4
            session.add(NotificationDelivery(
                account_id=account_id, plan_date=_plan_date(moment), notification_key="never-committed",
                dedup_hash=uuid.uuid4().hex, title="Never committed", status=notifications.STATUS_SUPPRESSED,
                suppressed_reason="test_only",
            ))
            return []
        return await active_devices(session, account_id)

    monkeypatch.setattr(notifications, "active_devices", devices)
    assert await worker.process_once(now=moment) == 1, "the second account still sends"
    assert sender.messages_sent == 1
    first, second = seen
    assert order == [second], "only the second account got as far as compiling its day"
    assert await _rows(first) == [], "the second account's commit made the first's unsaved row durable"
    assert (await _preference(first)).daily_cap == 1, "the second account's commit kept the first's change"
    [sent] = await _rows(second)
    assert sent.status == notifications.STATUS_PROVIDER_ACCEPTED


@pytest.mark.asyncio
async def test_s_c_a_failure_after_touching_the_database_cannot_roll_back_the_account_before_it(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The first account decides, commits nothing to send; the second writes and then fails.

    The first account's decided day must survive the second account's rollback.
    Under one shared session it did not: nothing had committed it yet.
    """
    from app.domains.planning.models import DailyPlan

    _, account_x = await registered_supabase_user()
    _, account_y = await registered_supabase_user()
    moment = _at_local_hour(9)
    for account_id in (account_x, account_y):
        await _opt_in(account_id, hour=9)
        await _set_preferences(account_id, topics=ONLY_PRODUCT_WATCH)
    sender = _sender(monkeypatch)
    order = _record_processing_order(monkeypatch)
    ordinary = _ordinary_reminder()

    async def queue_for_agenda(session, *, account_id, plan_date, timezone_name, moment=None):
        if account_id == order[0]:
            return None
        await ordinary(
            session, account_id=account_id, plan_date=plan_date, timezone_name=timezone_name, moment=moment,
        )
        raise RuntimeError("the second account fails after it has written")

    monkeypatch.setattr(notifications, "queue_for_agenda", queue_for_agenda)
    summary = worker.RunSummary()
    assert await worker.process_once(now=moment, summary=summary) == 0
    first, second = order
    assert summary.accounts_failed == 1 and summary.failed_account_ids == [str(second)]

    async with _factory()() as session:
        plans = {
            row.account_id for row in (await session.execute(select(DailyPlan).where(
                DailyPlan.plan_date == _plan_date(moment),
            ))).scalars().all()
        }
    assert first in plans, "the first account's decided day was rolled back by the second account's failure"
    assert second not in plans, "the failing account's own work was rolled back"
    assert await _rows(second) == []
    assert sender.batches == []


@pytest.mark.asyncio
async def test_s_c_the_account_after_a_failure_is_still_processed_normally(
    db_clean, registered_supabase_user, monkeypatch,
):
    _, account_x = await registered_supabase_user()
    _, account_y = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_x, hour=9)
    await _opt_in(account_y, hour=9)
    sender = _sender(monkeypatch)
    order = _record_processing_order(monkeypatch)
    ordinary = _ordinary_reminder()

    async def queue_for_agenda(session, *, account_id, plan_date, timezone_name, moment=None):
        row = await ordinary(
            session, account_id=account_id, plan_date=plan_date, timezone_name=timezone_name, moment=moment,
        )
        if account_id == order[0]:
            raise RuntimeError("the first account fails after writing")
        return row

    monkeypatch.setattr(notifications, "queue_for_agenda", queue_for_agenda)
    assert await worker.process_once(now=moment) == 1
    assert sender.messages_sent == 1
    failing, healthy = order
    [sent] = await _rows(healthy)
    assert sent.status == notifications.STATUS_PROVIDER_ACCEPTED
    assert await _rows(failing) == []


@pytest.mark.asyncio
async def test_s_d_each_account_is_processed_in_its_own_session_and_identity_map(
    db_clean, registered_supabase_user, monkeypatch,
):
    (_, account_a), (_, account_b) = await _two_accounts_in_batch_order(registered_supabase_user)
    moment = _at_local_hour(9)
    await _opt_in(account_a, hour=9)
    await _opt_in(account_b, hour=9)
    _sender(monkeypatch)
    seen: list[tuple] = []
    process_account = worker.process_account

    async def observed(session, preference, **kwargs):
        seen.append((session, preference))
        for earlier_session, earlier_preference in seen[:-1]:
            assert session is not earlier_session
            assert not earlier_session.in_transaction(), "the earlier account's transaction was still open"
            assert earlier_preference not in session
            assert all(getattr(obj, "account_id", None) != earlier_preference.account_id for obj in session)
        return await process_account(session, preference, **kwargs)

    monkeypatch.setattr(worker, "process_account", observed)
    await worker.process_once(now=moment)
    assert {preference.account_id for _, preference in seen} == {account_a, account_b}
    assert seen[0][0] is not seen[1][0]


# ===========================================================================
# T — a topic opt-out is one candidate's, not the day's
# ===========================================================================
def test_t_candidate_level_and_account_wide_suppressions_are_classified_exactly():
    """Only the customer's per-topic choice passes the day's chance on.

    Everything else stops the walk: the master switch, quiet hours, the daily
    cap, an inactive account, and any reason nobody listed. An actual delivery
    (queued or sending) is not a suppression at all.
    """
    def decision(status, reason=None):
        return type("Decision", (), {"status": status, "suppressed_reason": reason})()

    suppressed = notifications.STATUS_SUPPRESSED
    assert frozenset({notifications.SUPPRESSED_MODULE_OFF}) == notifications.CANDIDATE_SUPPRESSIONS
    assert notifications.is_candidate_opt_out(decision(suppressed, notifications.SUPPRESSED_MODULE_OFF))
    for account_wide in (
        notifications.SUPPRESSED_DISABLED, notifications.SUPPRESSED_QUIET, notifications.SUPPRESSED_CAP,
        notifications.SUPPRESSED_ACCOUNT_INACTIVE, notifications.SUPPRESSED_DUPLICATE,
        notifications.SUPPRESSED_WATCH_ENDED, "a_reason_nobody_listed", None,
    ):
        assert not notifications.is_candidate_opt_out(decision(suppressed, account_wide)), account_wide
    for delivery in (notifications.STATUS_QUEUED, notifications.STATUS_SENDING, notifications.STATUS_PROVIDER_ACCEPTED):
        assert not notifications.is_candidate_opt_out(decision(delivery, notifications.SUPPRESSED_MODULE_OFF))


async def _customer_with_a_due_routine(app_client, registered_supabase_user, *, hour=9):
    """A real account, a real shelf, real generated routines with a step due today."""
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    generated = await _generate(app_client, token, explain=False)
    assert generated.status_code == 200, generated.text
    await _opt_in(account_id, hour=hour)
    return token, account_id


async def _due_maintenance(account_id, plan_date: date) -> None:
    """A per-kind maintenance reminder the customer opted into, due by their own rhythm."""
    async with _factory()() as session:
        await maintenance_service.set_preference(
            session, account_id, "haircut", tracked=True, interval_days=42, reminders_enabled=True,
        )
        await maintenance_service.record_done(
            session, account_id, "haircut", done_on=plan_date - timedelta(days=60), today=plan_date,
        )
        await session.commit()


async def _confirmed_event_tomorrow(account_id, moment: datetime) -> None:
    """A confirmed upcoming event: the Event Preparation candidate."""
    async with _factory()() as session:
        await planning_service.upsert_event(session, account_id, CalendarEventInput(
            title="Cousin's wedding", starts_at=moment + timedelta(days=1), occasion_key="wedding",
        ))
        await session.commit()


def _plan_date(moment: datetime) -> date:
    return clock.local_today(TZ, moment=moment)


def _by_source(rows, source_kind):
    return [row for row in rows if row.source_kind == source_kind]


@pytest.mark.asyncio
async def test_t_a_care_off_does_not_starve_an_enabled_due_maintenance_reminder(
    db_clean, app_client, registered_supabase_user, fake_provider, monkeypatch,
):
    moment = _at_local_hour(9)
    _, account_id = await _customer_with_a_due_routine(app_client, registered_supabase_user)
    await _due_maintenance(account_id, _plan_date(moment))
    await _set_preferences(account_id, topics={"care": False, "today_style": False, "maintenance": True})
    sender = _sender(monkeypatch)

    assert await worker.process_once(now=moment) == 1
    rows = await _rows(account_id)
    [care] = _by_source(rows, "routine_due")
    assert (care.status, care.suppressed_reason) == (notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_MODULE_OFF)
    [sent] = [row for row in rows if row.status == notifications.STATUS_PROVIDER_ACCEPTED]
    assert notifications.topic_for_candidate(type("C", (), {
        "source_kind": sent.source_kind, "domain": "maintenance", "provenance": {},
    })()) == "maintenance"
    assert "Haircut" in f"{sent.title} {sent.body}"
    assert sender.messages_sent == 1


@pytest.mark.asyncio
async def test_t_b_care_off_does_not_starve_an_enabled_event_preparation_reminder(
    db_clean, app_client, registered_supabase_user, fake_provider, monkeypatch,
):
    moment = _at_local_hour(9)
    _, account_id = await _customer_with_a_due_routine(app_client, registered_supabase_user)
    await _confirmed_event_tomorrow(account_id, moment)
    await _set_preferences(account_id, topics={"care": False, "today_style": False, "event_preparation": True})
    sender = _sender(monkeypatch)

    assert await worker.process_once(now=moment) == 1
    rows = await _rows(account_id)
    [care] = _by_source(rows, "routine_due")
    assert care.suppressed_reason == notifications.SUPPRESSED_MODULE_OFF
    [sent] = [row for row in rows if row.status == notifications.STATUS_PROVIDER_ACCEPTED]
    assert sent.source_kind in {"event_preparation_entry", "event_ready_action"}
    assert sent.deep_link == "/event-ready"
    assert sender.messages_sent == 1


@pytest.mark.asyncio
async def test_t_c_a_daily_cap_reached_stops_every_later_trigger(
    db_clean, app_client, registered_supabase_user, fake_provider, monkeypatch,
):
    moment = _at_local_hour(9)
    _, account_id = await _customer_with_a_due_routine(app_client, registered_supabase_user)
    await _due_maintenance(account_id, _plan_date(moment))
    await _set_preferences(account_id, topics={"today_style": False})
    async with _factory()() as session:
        earlier = await notifications.queue(
            session, account_id=account_id, plan_date=_plan_date(moment), notification_key="earlier-today",
            title="Earlier today", module="outfit", topic="today_style", timezone_name=TZ, moment=moment,
        )
        # A different notification already went out today.
        earlier.status = notifications.STATUS_PROVIDER_ACCEPTED
        await session.commit()
    sender = _sender(monkeypatch)

    assert await worker.process_once(now=moment) == 0
    rows = await _rows(account_id)
    [care] = _by_source(rows, "routine_due")
    assert care.suppressed_reason == notifications.SUPPRESSED_CAP
    assert [row.notification_key for row in rows] == ["earlier-today", care.notification_key], (
        "a trigger after the cap was still evaluated"
    )
    assert sender.batches == []


@pytest.mark.asyncio
async def test_t_d_quiet_hours_that_begin_mid_cycle_stop_every_later_trigger(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """Quiet hours committed after the worker's first gate, before its first trigger.

    Product Watch re-reads the preference under its lock and records the notice
    as held for quiet hours, without consuming it. Nothing after it may send.
    """
    moment = _at_local_hour(9)
    token, account_id = await registered_supabase_user()
    device = await _device(app_client)
    await _claim(app_client, device, token)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    await _ingest(tmp_path)
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder())
    sender = _sender(monkeypatch)
    before = (await _watch_row(account_id)).notice_cursor

    compiled = race.pause()
    compile_day = worker.compiler.compile_day

    async def compile_then_pause(session, **kwargs):
        result = await compile_day(session, **kwargs)
        await compiled()
        return result

    monkeypatch.setattr(worker.compiler, "compile_day", compile_then_pause)
    run = race.spawn(worker.process_once(now=moment))
    await compiled.wait_reached()
    await _set_preferences(account_id, quiet_hours_start=0, quiet_hours_end=23)
    compiled.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0

    rows = await _rows(account_id)
    [held] = _by_source(rows, product_watch.SOURCE_KIND)
    assert held.suppressed_reason == notifications.SUPPRESSED_QUIET
    assert [row for row in rows if row.notification_key == "agenda:ordinary"] == []
    assert (await _watch_row(account_id)).notice_cursor == before, "quiet hours consumed the watch event"
    assert sender.batches == []


@pytest.mark.asyncio
async def test_t_e_a_product_watch_opt_out_sends_nothing_leaves_no_backlog_and_frees_the_slot(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id = await registered_supabase_user()
    device = await _device(app_client)
    await _claim(app_client, device, token)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    await _ingest(tmp_path)
    await _set_preferences(account_id, topics={"product_watch": False})
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder())
    sender = _sender(monkeypatch)

    assert await worker.process_once(now=moment) == 1
    assert [message.title for batch in sender.batches for message in batch] == ["Your look for today"]
    assert await _watch_deliveries(account_id) == []

    # Switching Product Watch back on re-baselines: the event is not a backlog.
    response = await app_client.patch(
        "/api/v2/today/notifications", headers={"Authorization": f"Bearer {token}"},
        json={"topics": {"product_watch": True}},
    )
    assert response.status_code == 200, response.text
    assert await worker.process_once(now=moment + timedelta(days=1)) in {0, 1}
    assert await _watch_deliveries(account_id) == []


@pytest.mark.asyncio
async def test_t_f_continuing_past_opt_outs_still_sends_exactly_one_notification(
    db_clean, app_client, registered_supabase_user, fake_provider, monkeypatch,
):
    moment = _at_local_hour(9)
    _, account_id = await _customer_with_a_due_routine(app_client, registered_supabase_user)
    await _due_maintenance(account_id, _plan_date(moment))
    await _confirmed_event_tomorrow(account_id, moment)
    await _set_preferences(account_id, topics={"care": False}, daily_cap=3)
    sender = _sender(monkeypatch)

    assert await worker.process_once(now=moment) == 1
    assert await worker.process_once(now=moment) == 0, "a repeated run in the same hour"
    assert sender.messages_sent == 1
    live = [row for row in await _rows(account_id) if row.status not in {notifications.STATUS_SUPPRESSED}]
    assert len(live) == 1 and live[0].status == notifications.STATUS_PROVIDER_ACCEPTED


# ===========================================================================
# R — a claim is not an attempt; an abandoned Product Watch claim is recovered
# ===========================================================================
@pytest.mark.asyncio
async def test_r_a_claim_records_ownership_and_never_an_attempt(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    async with _factory()() as session:
        row = NotificationDelivery(
            account_id=account_id, plan_date=utcnow().date(), notification_key="claim-only",
            dedup_hash=uuid.uuid4().hex, title="Claim", status=notifications.STATUS_QUEUED,
        )
        session.add(row)
        await session.commit()
        delivery_id = row.id
    async with _factory()() as session:
        token = await notifications.claim_delivery(session, delivery_id)
        await session.commit()
        claimed = await session.get(NotificationDelivery, delivery_id, populate_existing=True)
    assert token and claimed.status == notifications.STATUS_SENDING and claimed.claim_token == token
    assert claimed.claimed_at is not None and claimed.attempted_at is None, "claiming is not attempting"

    async with _factory()() as session:
        assert not await notifications.mark_attempt_started(session, delivery_id, "someone-else")
        assert await notifications.mark_attempt_started(session, delivery_id, token)
        await session.commit()
    async with _factory()() as session:
        assert not await notifications.mark_attempt_started(session, delivery_id, token), "an attempt is marked once"
        attempted = await session.get(NotificationDelivery, delivery_id)
        assert attempted.attempted_at is not None


@pytest.mark.asyncio
async def test_r_an_attempted_claim_is_never_claimed_again_however_old(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    async with _factory()() as session:
        ambiguous = NotificationDelivery(
            account_id=account_id, plan_date=utcnow().date(), notification_key="ambiguous",
            dedup_hash=uuid.uuid4().hex, title="Ambiguous", status=notifications.STATUS_SENDING,
            claimed_at=utcnow() - timedelta(days=1), claim_token="old",
            attempted_at=utcnow() - timedelta(days=1), provider_ticket_id=None,
        )
        session.add(ambiguous)
        await session.commit()
        delivery_id = ambiguous.id
    async with _factory()() as session:
        assert await notifications.claim_delivery(session, delivery_id, lease_seconds=0) is None
        await session.rollback()
        row = await session.get(NotificationDelivery, delivery_id)
        assert (row.status, row.claim_token, row.attempted_at is not None) == (notifications.STATUS_SENDING, "old", True)


async def _watched_customer(app_client, registered_supabase_user, tmp_path):
    """A watched pack with one material notice waiting; only Product Watch is on."""
    token, account_id = await registered_supabase_user()
    device = await _device(app_client)
    await _claim(app_client, device, token)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    await _set_preferences(account_id, topics={
        "today_style": False, "care": False, "event_preparation": False, "maintenance": False,
    })
    await _ingest(tmp_path)
    return token, account_id, device


async def _dies(monkeypatch, name: str, target=worker) -> None:
    async def died(*_args, **_kwargs):
        raise ProcessDied()
    monkeypatch.setattr(target, name, died)


async def _strand(monkeypatch, moment, *, where: str = "before_marker"):
    """Run one real cycle that dies at ``where``, then restore the worker."""
    if where == "before_marker":
        original = worker._settle_if_account_inactive
        await _dies(monkeypatch, "_settle_if_account_inactive")
    else:
        original = worker.push.send
        await _dies(monkeypatch, "send", target=worker.push)
    with pytest.raises(ProcessDied):
        await worker.process_once(now=moment)
    if where == "before_marker":
        monkeypatch.setattr(worker, "_settle_if_account_inactive", original)
    else:
        monkeypatch.setattr(worker.push, "send", original)


async def _let_the_lease_expire(account_id, *, by: timedelta = timedelta(minutes=10)) -> None:
    """Move this account's whole timeline back ``by``, so the claim's lease has expired.

    Every recorded instant moves together — the watch's start and the claim —
    so their order, which is what decides the watch epoch, is unchanged.
    """
    async with _factory()() as session:
        await session.execute(update(ProductWatch).where(ProductWatch.account_id == account_id).values(
            started_at=ProductWatch.started_at - by,
        ))
        await session.execute(update(NotificationDelivery).where(
            NotificationDelivery.account_id == account_id, NotificationDelivery.claimed_at.is_not(None),
        ).values(claimed_at=NotificationDelivery.claimed_at - by))
        await session.commit()


def _watch_messages(sender) -> list:
    return [
        message for batch in sender.batches for message in batch
        if message.title == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    ]


@pytest.mark.asyncio
async def test_r_a_a_claim_abandoned_before_the_provider_is_recovered_and_sent_exactly_once(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """The mandatory reproduction: queued, cursor advanced, claimed, committed, then death."""
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    await _strand(monkeypatch, moment)

    [stranded] = await _watch_deliveries(account_id)
    assert stranded.status == notifications.STATUS_SENDING
    assert stranded.claim_token and stranded.attempted_at is None, "died before the attempt marker"
    cursor = (await _watch_row(account_id)).notice_cursor
    assert sender.batches == []

    # Within the lease another cycle leaves it alone: its owner may still be alive.
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == []

    await _let_the_lease_expire(account_id)
    at_send: list[NotificationDelivery] = []
    send = sender.send

    async def observe_the_committed_row_then_send(messages):
        async with _factory()() as session:
            at_send.append(await session.get(NotificationDelivery, stranded.id))
        return await send(messages)

    monkeypatch.setattr(worker.push, "send", observe_the_committed_row_then_send)
    assert await worker.process_once(now=moment) == 1

    [committed_before_send] = at_send
    assert committed_before_send.status == notifications.STATUS_SENDING
    assert committed_before_send.claim_token not in (None, stranded.claim_token), "a new claim was acquired"
    assert committed_before_send.attempted_at is not None, "the attempt marker was committed first"
    assert len(_watch_messages(sender)) == 1 and sender.messages_sent == 1
    [settled] = await _watch_deliveries(account_id)
    assert settled.id == stranded.id, "the same outbox row, not a regenerated notice"
    assert settled.status == notifications.STATUS_PROVIDER_ACCEPTED and settled.claim_token is None
    assert (await _watch_row(account_id)).notice_cursor == cursor, "the cursor did not move twice"

    assert await worker.process_once(now=moment) == 0, "and it is not sent again"
    assert sender.messages_sent == 1


@pytest.mark.asyncio
async def test_r_b_a_claim_abandoned_after_the_attempt_marker_is_never_resent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, caplog,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment, where="during_provider")
    [ambiguous] = await _watch_deliveries(account_id)
    assert ambiguous.status == notifications.STATUS_SENDING and ambiguous.attempted_at is not None
    assert ambiguous.provider_ticket_id is None

    sender = _sender(monkeypatch)
    await _let_the_lease_expire(account_id, by=timedelta(hours=2))
    with caplog.at_level(logging.WARNING, logger="app.domains.product.watch"):
        assert await worker.process_once(now=moment) == 0
    assert sender.batches == []
    observed = [record.getMessage() for record in caplog.records if "outcome_unknown" in record.getMessage()]
    assert observed == [f"product_watch_delivery_outcome_unknown delivery={ambiguous.id}"], (
        "the ambiguous row is observed generically: its id, and no title, body or token"
    )
    [still] = await _watch_deliveries(account_id)
    assert (still.status, still.claim_token, still.attempted_at) == (
        notifications.STATUS_SENDING, ambiguous.claim_token, ambiguous.attempted_at,
    ), "an ambiguous attempt is left exactly as it was"
    async with _factory()() as session:
        assert await notifications.claim_delivery(session, ambiguous.id, lease_seconds=0) is None
        await session.rollback()


@pytest.mark.asyncio
async def test_r_c_a_watch_stopped_after_the_crash_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    assert (await _delete_watch(app_client, token)).status_code == 200
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == []
    [row] = await _watch_deliveries(account_id)
    assert (row.status, row.suppressed_reason) == (notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_WATCH_ENDED)
    assert row.claim_token is None


@pytest.mark.asyncio
async def test_r_d_a_watch_re_anchored_to_a_newer_pack_after_the_crash_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, device = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    old_anchor = (await _watch_row(account_id)).anchor_scan_event_id

    await _confirm(app_client, device, token, account_id, batch_number="NEWER-PACK")
    state = await _read_watch(app_client, token, device)
    assert state.json()["watchable"] is True
    assert (await _put_watch(app_client, device, token, label_version=state.json()["anchorable_label_version"])).status_code == 200
    assert (await _watch_row(account_id)).anchor_scan_event_id != old_anchor, "re-anchored to the newer pack"

    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert _watch_messages(sender) == []
    stranded = [row for row in await _watch_deliveries(account_id) if row.notification_key.startswith("pw:a:")]
    assert [row.suppressed_reason for row in stranded] == [notifications.SUPPRESSED_WATCH_ENDED]


@pytest.mark.asyncio
async def test_r_d_a_watch_restarted_as_a_new_epoch_after_the_crash_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, device = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    assert (await _delete_watch(app_client, token)).status_code == 200
    assert (await _put_watch(app_client, device, token)).status_code == 200
    assert (await _watch_row(account_id)).active is True

    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert _watch_messages(sender) == []
    [row] = [row for row in await _watch_deliveries(account_id) if row.notification_key.startswith("pw:a:")]
    assert row.suppressed_reason == notifications.SUPPRESSED_WATCH_ENDED


@pytest.mark.asyncio
async def test_r_e_the_product_watch_topic_switched_off_after_the_crash_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    await _set_preferences(account_id, topics={
        "product_watch": False, "today_style": False, "care": False, "event_preparation": False, "maintenance": False,
    })
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == []
    [row] = await _watch_deliveries(account_id)
    assert (row.status, row.suppressed_reason) == (notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_MODULE_OFF)


@pytest.mark.asyncio
async def test_r_f_no_active_device_means_no_send_and_the_row_waits_for_a_device_today(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    async with _factory()() as session:
        await session.execute(update(NotificationDevice).where(NotificationDevice.account_id == account_id).values(
            status="disabled", disabled_at=utcnow(),
        ))
        await session.commit()
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == []
    [waiting] = await _watch_deliveries(account_id)
    assert waiting.status == notifications.STATUS_SENDING and waiting.attempted_at is None, "untouched"

    async with _factory()() as session:
        await notifications.register_device(
            session, account_id, device_key="install-returned", platform="android",
            expo_push_token=f"ExponentPushToken[{uuid.uuid4().hex}]",
        )
        await session.commit()
    assert await worker.process_once(now=moment) == 1
    assert len(_watch_messages(sender)) == 1


@pytest.mark.asyncio
async def test_r_g_a_cap_another_delivery_has_taken_is_not_exceeded_by_the_recovered_row(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    [stranded] = await _watch_deliveries(account_id)
    async with _factory()() as session:
        other = await notifications.queue(
            session, account_id=account_id, plan_date=stranded.plan_date, notification_key="another-delivery",
            title="Another", module="outfit", topic="today_style", timezone_name=TZ, moment=moment,
        )
        # Queued past the cap check, as a manual run could; then accepted by the provider.
        other.status, other.suppressed_reason = notifications.STATUS_PROVIDER_ACCEPTED, None
        await session.commit()
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == []
    row = next(row for row in await _watch_deliveries(account_id) if row.id == stranded.id)
    assert (row.status, row.suppressed_reason) == (notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_CAP)


@pytest.mark.asyncio
async def test_r_g_the_recovering_row_does_not_count_against_itself(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """Daily cap 1, and the only row of the day is the stranded one: it may be recovered."""
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    assert (await _preference(account_id)).daily_cap == 1
    await _strand(monkeypatch, moment)
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 1
    assert len(_watch_messages(sender)) == 1


@pytest.mark.asyncio
async def test_r_an_abandoned_claim_from_an_earlier_day_is_never_sent_late(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment + timedelta(days=1)) == 0
    assert _watch_messages(sender) == []
    [row] = await _watch_deliveries(account_id)
    assert row.status == notifications.STATUS_SENDING and row.attempted_at is None


@pytest.mark.asyncio
async def test_r_the_requeue_and_settle_guards_refuse_anything_but_the_exact_unattempted_abandoned_claim(
    db_clean, registered_supabase_user,
):
    """The last line, whatever the caller checked before it.

    Requeueing or settling touches a row only if it is still ``sending``, never
    attempted, lease expired, and still carrying the claim it was read with.
    """
    _, account_id = await registered_supabase_user()
    old = utcnow() - timedelta(minutes=10)

    async def row(**values) -> NotificationDelivery:
        fields = {
            "status": notifications.STATUS_SENDING, "claim_token": "read-with", "claimed_at": old, **values,
        }
        async with _factory()() as session:
            delivery = NotificationDelivery(
                account_id=account_id, plan_date=utcnow().date(), notification_key=uuid.uuid4().hex[:16],
                dedup_hash=uuid.uuid4().hex, title="Guarded", source_kind=product_watch.SOURCE_KIND, **fields,
            )
            session.add(delivery)
            await session.commit()
            return delivery

    attempted = await row(attempted_at=old)
    fresh = await row(claimed_at=utcnow())
    queued = await row(status=notifications.STATUS_QUEUED)
    reclaimed = await row()
    async with _factory()() as session:
        await session.execute(update(NotificationDelivery).where(NotificationDelivery.id == reclaimed.id).values(
            claim_token="someone-else",
        ))
        await session.commit()
    for refused in (attempted, fresh, queued, reclaimed):
        async with _factory()() as session:
            # As read by the recovering cycle: the claim it saw is the one written above.
            seen = await session.get(NotificationDelivery, refused.id)
            seen.claim_token = "read-with"
            session.expunge(seen)
            session.add(seen)
            assert not await notifications.requeue_abandoned_claim(session, seen)
            assert not await notifications.settle_abandoned_claim(session, seen, notifications.SUPPRESSED_CAP)
            await session.rollback()
    abandoned = await row()
    async with _factory()() as session:
        seen = await session.get(NotificationDelivery, abandoned.id)
        assert await notifications.requeue_abandoned_claim(session, seen)
        assert seen.status == notifications.STATUS_QUEUED and seen.claim_token is None
        await session.rollback()


@pytest.mark.asyncio
async def test_r_f_a_device_removed_while_the_cycle_is_deciding_blocks_the_recovered_send(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """Recovery re-reads the devices under its own lock, not the list the cycle began with."""
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)

    compiled = race.pause()
    compile_day = worker.compiler.compile_day

    async def compile_then_pause(session, **kwargs):
        result = await compile_day(session, **kwargs)
        await compiled()
        return result

    monkeypatch.setattr(worker.compiler, "compile_day", compile_then_pause)
    run = race.spawn(worker.process_once(now=moment))
    await compiled.wait_reached()
    async with _factory()() as session:
        await session.execute(update(NotificationDevice).where(NotificationDevice.account_id == account_id).values(
            status="disabled", disabled_at=utcnow(),
        ))
        await session.commit()
    compiled.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == []
    [waiting] = await _watch_deliveries(account_id)
    assert waiting.status == notifications.STATUS_SENDING and waiting.attempted_at is None


@pytest.mark.asyncio
async def test_r_h_two_workers_recovering_the_same_row_give_one_claim_and_one_send(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    [stranded] = await _watch_deliveries(account_id)
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)

    requeued = race.pause()
    requeue = notifications.requeue_abandoned_claim
    requeues: list[bool] = []

    async def requeue_then_pause(session, row, **kwargs):
        requeues.append(await requeue(session, row, **kwargs))
        if len(requeues) == 1:
            await requeued()
        return requeues[-1]

    claims: list[str | None] = []
    claim_delivery = notifications.claim_delivery

    async def recorded_claim(session, delivery_id, **kwargs):
        token = await claim_delivery(session, delivery_id, **kwargs)
        if delivery_id == stranded.id:
            claims.append(token)
        return token

    monkeypatch.setattr(notifications, "requeue_abandoned_claim", requeue_then_pause)
    monkeypatch.setattr(notifications, "claim_delivery", recorded_claim)
    first_summary, second_summary = worker.RunSummary(), worker.RunSummary()
    first = race.spawn(worker.process_once(now=moment, summary=first_summary))
    await requeued.wait_reached()
    second = race.spawn(worker.process_once(now=moment, summary=second_summary))
    await _until_a_backend_waits_on_a_lock()
    requeued.release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), timeout=60)

    assert sorted(results) == [0, 1]
    assert requeues == [True], "the second worker never found the row abandoned"
    assert [token for token in claims if token] and len([token for token in claims if token]) == 1
    assert len(_watch_messages(sender)) == 1 and sender.messages_sent == 1
    assert first_summary.accounts_failed == 0 and second_summary.accounts_failed == 0
    [row] = await _watch_deliveries(account_id)
    assert row.status == notifications.STATUS_PROVIDER_ACCEPTED


@pytest.mark.asyncio
async def test_r_i_a_deletion_requested_before_the_recovered_send_sends_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """A recovered delivery passes the same final lifecycle gate as a new one."""
    from app.domains.privacy import deletion_service

    moment = _at_local_hour(9)
    _, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)

    requeued = race.pause()
    requeue = notifications.requeue_abandoned_claim

    async def requeue_then_pause(session, row, **kwargs):
        recovered = await requeue(session, row, **kwargs)
        await requeued()
        return recovered

    monkeypatch.setattr(notifications, "requeue_abandoned_claim", requeue_then_pause)
    run = race.spawn(worker.process_once(now=moment))
    await requeued.wait_reached()
    async with _factory()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    requeued.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0

    assert sender.batches == []
    [row] = await _watch_deliveries(account_id)
    assert (row.status, row.suppressed_reason) == (
        notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_ACCOUNT_INACTIVE,
    )
    assert row.attempted_at is None and row.claim_token is None
