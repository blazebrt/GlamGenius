"""Lane D correction — the customer's authority across the claim/provider boundary.

Lane D split a claim from an attempt: a claimed delivery with no
``attempted_at`` has certainly not reached Expo and may be recovered; an
attempted one may have, and is never sent again. That stays. What these tests
hold is the preference authority on either side of that line:

* **An opt-out settles what has not been attempted.** Switching Product Watch
  or the master switch off settles, in the same transaction, every Product
  Watch delivery that is queued or claimed with no attempt recorded. Before
  this, a notice stranded before the provider survived an off-then-on toggle
  made with no worker cycle in between, and was sent after the re-enable — a
  backlog replay.
* **The final gate re-proves the authority to send.** Immediately before the
  attempt marker, in one short transaction, with the preference row locked:
  account active, notifications on, native push on, the devices read now
  (``FOR SHARE``), and for Product Watch the topic, the watch and its epoch.
  Before this, the gate checked only that the account was active, so an
  opt-out committed after the claim and before the marker still sent.
* **One linearization point.** An opt-out, native-push change or device
  removal that commits before the gate takes its locks is obeyed. One that
  arrives while the gate holds them waits, and then finds the attempt in
  flight, which it does not rewrite.

Every test runs the real worker against PostgreSQL 16 and the real HTTP routes.
No real push is sent: the provider is a stand-in that records what it is given.
No timing sleeps. Every interleaving is an explicit pause, and the only waiting
is a bounded poll of PostgreSQL's own lock tables.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from app.domains.planning import notifications, push
from app.domains.planning.models import NotificationDelivery
from app.domains.product import watch as product_watch
from app.domains.product.models import ProductWatch
from app.shared.database.base import utcnow
from app.workers import notifications as worker
from sqlalchemy import text, update

from tests.conftest import auth
from tests.test_notification_delivery_integrity import (
    _devices,
    _factory,
    _let_the_lease_expire,
    _ordinary_reminder,
    _preference,
    _rows,
    _sender,
    _strand,
    _watch_messages,
    _watched_customer,
    race,  # noqa: F401 - re-exported fixture
)
from tests.test_notification_worker_operations import _at_local_hour, _CountingPush, _opt_in
from tests.test_official_records_api import off_clean  # noqa: F401 - re-exported fixture
from tests.test_step12c_product_watch import (
    _delete_watch,
    _put_watch,
    _set_preferences,
    _watch_deliveries,
    _watch_row,
)

SENDING = notifications.STATUS_SENDING
SUPPRESSED = notifications.STATUS_SUPPRESSED
ACCEPTED = notifications.STATUS_PROVIDER_ACCEPTED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _patch(app_client, token, **body) -> dict:
    """The customer changing a notification setting, through the production route."""
    response = await app_client.patch("/api/v2/today/notifications", json=body, headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()["preferences"]


async def _register(app_client, token, device_key: str, expo_push_token: str) -> None:
    response = await app_client.post(
        "/api/v2/today/notifications/devices", headers=auth(token),
        json={"device_key": device_key, "platform": "android", "expo_push_token": expo_push_token},
    )
    assert response.status_code == 200, response.text


async def _unregister(app_client, token, device_key: str) -> dict:
    response = await app_client.delete(f"/api/v2/today/notifications/devices/{device_key}", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _row(delivery_id) -> NotificationDelivery:
    async with _factory()() as session:
        return await session.get(NotificationDelivery, delivery_id)


def _pause_before_the_gate(monkeypatch, pauses):
    """Hold the worker after its claim has committed and before the final gate."""
    paused = pauses.pause()
    gate = worker._authorize_provider_attempt

    async def paused_then_gate(*args, **kwargs):
        await paused()
        return await gate(*args, **kwargs)

    monkeypatch.setattr(worker, "_authorize_provider_attempt", paused_then_gate)
    return paused


def _pause_inside_the_gate(monkeypatch, pauses):
    """Hold the worker inside the gate: preference locked, devices read and held, no attempt yet."""
    paused = pauses.pause()
    devices_for_attempt = notifications.devices_for_attempt

    async def read_then_hold(session, account_id):
        devices = await devices_for_attempt(session, account_id)
        await paused()
        return devices

    monkeypatch.setattr(notifications, "devices_for_attempt", read_then_hold)
    return paused


class _HeldProvider(_CountingPush):
    """Expo with the request in flight: the call is recorded, then held until released."""

    def __init__(self, held) -> None:
        super().__init__()
        self.held = held

    async def send(self, messages):
        result = await super().send(messages)
        await self.held()
        return result


def _provider_held_in_flight(monkeypatch, pauses) -> _HeldProvider:
    provider = _HeldProvider(pauses.pause())
    monkeypatch.setattr(push, "send", provider.send)
    return provider


async def _until_it_waits_on_a_lock(task, *, on: str | None = None) -> None:
    """Return once a backend of this database waits on a lock; fail if ``task`` ends first.

    ``on`` narrows it to a waiting statement that names that table.
    """
    sql = (
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND wait_event_type = 'Lock'"
    )
    if on is not None:
        sql += " AND query ILIKE :pattern"
    for _ in range(3000):
        if task.done():
            raise AssertionError(f"it never waited on a lock; it finished: {task.exception() or task.result()!r}")
        async with _factory()() as watcher:
            waiting = await watcher.scalar(text(sql), {"pattern": f"%{on}%"} if on else {})
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("nothing ever waited on a lock")


def _assert_monotonic(before: dict, after: dict) -> None:
    earlier = product_watch.WatchCursor.from_json(before)
    later = product_watch.WatchCursor.from_json(after)
    assert product_watch.merge_rebaseline_cursor(later, earlier) == later, "the cursor never moves backwards"


def _withdrawn(row: NotificationDelivery, reason: str) -> None:
    assert (row.status, row.suppressed_reason) == (SUPPRESSED, reason)
    assert row.claim_token is None and row.claimed_at is None, "the claim is cleared"
    assert row.attempted_at is None and row.sent_at is None, "nothing was attempted"


# ===========================================================================
# The opt-out's own settlement authority
# ===========================================================================
@pytest.mark.asyncio
async def test_an_opt_out_settles_exactly_this_accounts_unattempted_product_watch_deliveries(
    db_clean, app_client, registered_supabase_user,
):
    """Queued, or claimed with no attempt — fresh or stale — and nothing else."""
    token, account_id = await registered_supabase_user()
    _, other_account = await registered_supabase_user()
    now = utcnow()

    async def delivery(account, *, source_kind=product_watch.SOURCE_KIND, **fields) -> NotificationDelivery:
        async with _factory()() as session:
            row = NotificationDelivery(
                account_id=account, plan_date=now.date(), notification_key=uuid.uuid4().hex[:16],
                dedup_hash=uuid.uuid4().hex, title="Watched", source_kind=source_kind, **fields,
            )
            session.add(row)
            await session.commit()
            return row

    queued = await delivery(account_id, status=notifications.STATUS_QUEUED)
    fresh_claim = await delivery(account_id, status=SENDING, claim_token="fresh", claimed_at=now)
    stale_claim = await delivery(account_id, status=SENDING, claim_token="stale", claimed_at=now - timedelta(hours=1))
    attempted = await delivery(account_id, status=SENDING, claim_token="attempted", claimed_at=now, attempted_at=now)
    delivered = await delivery(account_id, status=ACCEPTED, attempted_at=now, sent_at=now)
    reminder = await delivery(account_id, source_kind="agenda", status=SENDING, claim_token="other", claimed_at=now)
    someone_elses = await delivery(other_account, status=SENDING, claim_token="theirs", claimed_at=now)

    await _patch(app_client, token, topics={"product_watch": False})

    for settled in (queued, fresh_claim, stale_claim):
        _withdrawn(await _row(settled.id), notifications.SUPPRESSED_MODULE_OFF)
    still = await _row(attempted.id)
    assert (still.status, still.claim_token, still.attempted_at) == (SENDING, "attempted", attempted.attempted_at), (
        "an attempted delivery may have reached the provider; the opt-out does not rewrite it"
    )
    assert (await _row(delivered.id)).status == ACCEPTED
    assert (await _row(reminder.id)).status == SENDING, "only Product Watch deliveries are settled here"
    assert (await _row(someone_elses.id)).claim_token == "theirs", "only this account's"

    # The master switch settles with its own reason.
    again = await delivery(account_id, status=SENDING, claim_token="again", claimed_at=now)
    await _patch(app_client, token, topics={"product_watch": True})
    assert (await _row(again.id)).status == SENDING, "switching on settles nothing"
    await _patch(app_client, token, enabled=False)
    _withdrawn(await _row(again.id), notifications.SUPPRESSED_DISABLED)


# ===========================================================================
# R1, R2 — off then on, with no worker cycle between, cannot replay D
# ===========================================================================
async def _toggled_with_no_cycle_between(
    app_client, registered_supabase_user, tmp_path, monkeypatch, *, off: dict, on: dict, reason: str,
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    [stranded] = await _watch_deliveries(account_id)
    assert stranded.status == SENDING and stranded.claim_token and stranded.attempted_at is None
    cursor_before = (await _watch_row(account_id)).notice_cursor

    await _patch(app_client, token, **off)
    _withdrawn(await _row(stranded.id), reason)
    await _patch(app_client, token, **on)
    cursor_re_enabled = (await _watch_row(account_id)).notice_cursor
    _assert_monotonic(cursor_before, cursor_re_enabled)

    await _let_the_lease_expire(account_id)
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == [], "the notice decided before the opt-out is not sent"
    [only] = await _watch_deliveries(account_id)
    assert only.id == stranded.id, "no new Product Watch delivery for the old event"
    _withdrawn(only, reason)
    assert (await _watch_row(account_id)).notice_cursor == cursor_re_enabled, "the cursor was not rolled back"
    assert await worker.process_once(now=moment) == 0 and sender.batches == []


@pytest.mark.asyncio
async def test_r1_product_watch_off_then_on_with_no_cycle_between_cannot_replay_the_stranded_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    await _toggled_with_no_cycle_between(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
        off={"topics": {"product_watch": False}}, on={"topics": {"product_watch": True}},
        reason=notifications.SUPPRESSED_MODULE_OFF,
    )


@pytest.mark.asyncio
async def test_r2_notifications_off_then_on_with_no_cycle_between_cannot_replay_the_stranded_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    await _toggled_with_no_cycle_between(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
        off={"enabled": False}, on={"enabled": True},
        reason=notifications.SUPPRESSED_DISABLED,
    )


# ===========================================================================
# R3–R6 — a change committed after the claim and before the marker
# ===========================================================================
async def _opt_out_after_the_claim(app_client, registered_supabase_user, tmp_path, monkeypatch, pauses, **change):
    """A watched notice is claimed; ``change`` commits; then the worker reaches the gate."""
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, pauses)
    run = pauses.spawn(worker.process_once(now=moment))
    await paused.wait_reached()
    [claimed] = await _watch_deliveries(account_id)
    assert claimed.status == SENDING and claimed.claim_token and claimed.attempted_at is None
    await _patch(app_client, token, **change)
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == [], "the change committed first: no provider call"
    return claimed, await _row(claimed.id)


@pytest.mark.asyncio
async def test_r3_a_product_watch_opt_out_before_the_marker_means_no_provider_call(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    _, row = await _opt_out_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, topics={"product_watch": False},
    )
    _withdrawn(row, notifications.SUPPRESSED_MODULE_OFF)


@pytest.mark.asyncio
async def test_r5_the_master_switch_before_the_marker_means_no_provider_call(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    _, row = await _opt_out_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, enabled=False,
    )
    _withdrawn(row, notifications.SUPPRESSED_DISABLED)


@pytest.mark.asyncio
async def test_r5_the_master_switch_before_the_marker_stops_any_notification_not_only_product_watch(
    db_clean, app_client, registered_supabase_user, monkeypatch, race,  # noqa: F811
):
    """An ordinary reminder: the opt-out does not settle it, so the gate is what stops it."""
    moment = _at_local_hour(9)
    token, account_id = await registered_supabase_user()
    await _opt_in(account_id, hour=9)
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder())
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await paused.wait_reached()
    [claimed] = await _rows(account_id)
    assert claimed.status == SENDING and claimed.source_kind != product_watch.SOURCE_KIND

    await _patch(app_client, token, enabled=False)
    assert (await _row(claimed.id)).status == SENDING, "still claimed: the gate decides this one"
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == []
    _withdrawn(await _row(claimed.id), notifications.SUPPRESSED_DISABLED)


@pytest.mark.asyncio
async def test_r6_native_push_switched_off_before_the_marker_means_no_provider_call(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    claimed, row = await _opt_out_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, native_push_enabled=False,
    )
    # A temporary state, as for a claim whose worker died: never attempted,
    # left for the same-day recovery, not settled as the customer's opt-out.
    assert (row.status, row.claim_token, row.attempted_at) == (SENDING, claimed.claim_token, None)


# ===========================================================================
# R4 — the attempt marker first, then the opt-out
# ===========================================================================
@pytest.mark.asyncio
async def test_r4_an_opt_out_after_the_marker_finds_the_notice_in_flight_and_never_sends_twice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    provider = _provider_held_in_flight(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await provider.held.wait_reached()
    [attempted] = await _watch_deliveries(account_id)
    assert attempted.status == SENDING and attempted.attempted_at is not None, "the marker committed first"

    await _patch(app_client, token, topics={"product_watch": False})
    during = await _row(attempted.id)
    assert (during.status, during.claim_token, during.attempted_at, during.suppressed_reason) == (
        SENDING, attempted.claim_token, attempted.attempted_at, None,
    ), "the opt-out does not rewrite an attempt as unsent"

    provider.held.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1, "the one attempt in flight completes"
    assert provider.messages_sent == 1
    settled = await _row(attempted.id)
    assert settled.status == ACCEPTED and settled.attempted_at == attempted.attempted_at

    await _patch(app_client, token, topics={"product_watch": True})
    await _let_the_lease_expire(account_id)
    assert await worker.process_once(now=moment) == 0
    assert provider.messages_sent == 1, "no second attempt"


# ===========================================================================
# R7, R8 — device removal on either side of the gate
# ===========================================================================
@pytest.mark.asyncio
async def test_r7_a_device_unregistered_before_the_gate_is_never_sent_to(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """Two devices; one is removed after the claim. Only the other is sent to."""
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [kept] = await _devices(account_id)
    await _register(app_client, token, "install-leaving", f"ExponentPushToken[{uuid.uuid4().hex}]")
    removed = next(device for device in await _devices(account_id) if device.device_key == "install-leaving")
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await paused.wait_reached()

    body = await _unregister(app_client, token, "install-leaving")
    assert body["active_devices_remaining"] is True and body["native_push_enabled"] is True
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    [batch] = sender.batches
    assert [message.to for message in batch] == [kept.expo_push_token]
    assert removed.expo_push_token not in {message.to for message in batch}, "nothing to the removed device"


@pytest.mark.asyncio
async def test_r7_the_only_device_unregistered_before_the_gate_means_no_provider_call(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [device] = await _devices(account_id)
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await paused.wait_reached()
    [claimed] = await _watch_deliveries(account_id)

    assert (await _unregister(app_client, token, device.device_key))["native_push_enabled"] is False
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == []
    row = await _row(claimed.id)
    assert (row.status, row.claim_token, row.attempted_at) == (SENDING, claimed.claim_token, None)


@pytest.mark.asyncio
async def test_r7_another_account_taking_over_this_phones_token_before_the_gate_gets_nothing_of_ours(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """A shared phone: B registers the token A was using. A's notice must not follow it."""
    moment = _at_local_hour(9)
    _, account_a, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [device_a] = await _devices(account_a)
    token_b, _ = await registered_supabase_user()
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await paused.wait_reached()

    await _register(app_client, token_b, "install-b", device_a.expo_push_token)
    [handed_over] = await _devices(account_a)
    assert handed_over.status == "disabled"
    assert (await _preference(account_a)).native_push_enabled is True, "only the device changed"
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == [], "nothing of A's goes to the token B now owns"


@pytest.mark.asyncio
async def test_r8_a_device_removed_after_the_gate_leaves_one_attempt_in_flight_and_no_second(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [device] = await _devices(account_id)
    provider = _provider_held_in_flight(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await provider.held.wait_reached()
    [attempted] = await _watch_deliveries(account_id)
    assert attempted.attempted_at is not None

    # Not held up by the provider call: the gate committed before it began.
    body = await asyncio.wait_for(_unregister(app_client, token, device.device_key), timeout=30)
    assert body["native_push_enabled"] is False
    provider.held.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    assert [message.to for batch in provider.batches for message in batch] == [device.expo_push_token]
    assert (await _row(attempted.id)).status == ACCEPTED

    await _let_the_lease_expire(account_id)
    assert await worker.process_once(now=moment) == 0
    assert provider.messages_sent == 1, "no second attempt"


# ===========================================================================
# R9 — an attempted, ambiguous delivery survives every opt-out unchanged
# ===========================================================================
@pytest.mark.asyncio
async def test_r9_an_attempted_ambiguous_delivery_is_never_made_recoverable_by_preference_changes(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment, where="during_provider")
    [ambiguous] = await _watch_deliveries(account_id)
    assert ambiguous.status == SENDING and ambiguous.attempted_at is not None and ambiguous.claim_token

    def unchanged(row: NotificationDelivery) -> tuple:
        return (row.status, row.claim_token, row.claimed_at, row.attempted_at, row.suppressed_reason)

    for change in (
        {"topics": {"product_watch": False}}, {"topics": {"product_watch": True}},
        {"enabled": False}, {"enabled": True},
    ):
        await _patch(app_client, token, **change)
        assert unchanged(await _row(ambiguous.id)) == unchanged(ambiguous), change

    await _let_the_lease_expire(account_id, by=timedelta(hours=2))
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert sender.batches == [], "never sent again"
    row = await _row(ambiguous.id)
    assert (row.status, row.claim_token, row.attempted_at) == (SENDING, ambiguous.claim_token, ambiguous.attempted_at)
    async with _factory()() as session:
        assert await notifications.claim_delivery(session, ambiguous.id, lease_seconds=0) is None
        await session.rollback()


# ===========================================================================
# The gate re-proves on its own, whatever did or did not settle the row
# ===========================================================================
async def _changed_after_the_claim(app_client, registered_supabase_user, tmp_path, monkeypatch, pauses, change):
    """A watched notice is claimed; ``change`` runs; then the worker reaches the gate."""
    moment = _at_local_hour(9)
    token, account_id, device = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    paused = _pause_before_the_gate(monkeypatch, pauses)
    run = pauses.spawn(worker.process_once(now=moment))
    await paused.wait_reached()
    [claimed] = await _watch_deliveries(account_id)
    await change(token=token, account_id=account_id, device=device)
    paused.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == []
    return claimed, await _row(claimed.id)


@pytest.mark.asyncio
async def test_the_gate_refuses_a_product_watch_topic_switched_off_even_where_nothing_settled_the_row(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    async def switched_off_directly(*, account_id, **_):
        # Written straight to the row: no route, so no settlement. The gate alone decides.
        await _set_preferences(account_id, topics={
            "product_watch": False, "today_style": False, "care": False, "event_preparation": False,
            "maintenance": False,
        })

    _, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, switched_off_directly,
    )
    _withdrawn(row, notifications.SUPPRESSED_MODULE_OFF)


@pytest.mark.asyncio
async def test_a_watch_stopped_after_the_claim_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """The stop itself settles the claim (the watch lifecycle); the gate then finds nothing to attempt."""
    async def stopped(*, token, **_):
        assert (await _delete_watch(app_client, token)).status_code == 200

    _, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, stopped,
    )
    _withdrawn(row, notifications.SUPPRESSED_WATCH_ENDED)


@pytest.mark.asyncio
async def test_a_notice_from_an_epoch_the_watch_has_since_left_is_not_sent(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    async def restarted(*, token, device, account_id):
        assert (await _delete_watch(app_client, token)).status_code == 200
        assert (await _put_watch(app_client, device, token)).status_code == 200
        assert (await _watch_row(account_id)).active is True

    _, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, restarted,
    )
    _withdrawn(row, notifications.SUPPRESSED_WATCH_ENDED)


async def _write_the_watch_directly(account_id, **values):
    """Change the watch row without the lifecycle, so nothing settles the claim: the gate decides alone."""
    async with _factory()() as session:
        await session.execute(update(ProductWatch).where(ProductWatch.account_id == account_id).values(**values))
        await session.commit()


@pytest.mark.asyncio
async def test_the_gate_refuses_a_stopped_watch_even_where_nothing_settled_the_row(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    async def stopped_directly(*, account_id, **_):
        await _write_the_watch_directly(account_id, active=False, stopped_at=utcnow())

    _, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, stopped_directly,
    )
    _withdrawn(row, notifications.SUPPRESSED_WATCH_ENDED)


@pytest.mark.asyncio
async def test_the_gate_refuses_a_restarted_epoch_even_where_nothing_settled_the_row(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """Defence in depth only: honest timestamps, and a restart the lifecycle did not see."""
    async def restarted_directly(*, account_id, **_):
        await _write_the_watch_directly(account_id, started_at=utcnow())

    _, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, restarted_directly,
    )
    _withdrawn(row, notifications.SUPPRESSED_WATCH_ENDED)


@pytest.mark.asyncio
async def test_the_gate_holds_a_claim_back_for_quiet_hours_or_a_lowered_cap_set_after_it(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """The customer's current quiet hours and daily cap, not the ones the claim saw."""
    async def quiet_now(*, token, **_):
        await _patch(app_client, token, quiet_hours_start=8, quiet_hours_end=10)

    claimed, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, quiet_now,
    )
    assert (row.status, row.claim_token, row.attempted_at) == (SENDING, claimed.claim_token, None)


@pytest.mark.asyncio
async def test_the_gate_holds_a_claim_back_when_the_daily_cap_is_lowered_after_it(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    async def no_more_today(*, token, **_):
        await _patch(app_client, token, daily_cap=0)

    claimed, row = await _changed_after_the_claim(
        app_client, registered_supabase_user, tmp_path, monkeypatch, race, no_more_today,
    )
    assert (row.status, row.claim_token, row.attempted_at) == (SENDING, claimed.claim_token, None)


# ===========================================================================
# L — overlapping transactions: the lock decides the order
# ===========================================================================
@pytest.mark.asyncio
async def test_l1_an_opt_out_holding_the_preference_lock_makes_the_gate_wait_and_wins(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    before_gate = _pause_before_the_gate(monkeypatch, race)
    inside_opt_out = race.pause()
    withdraw = product_watch.withdraw_unsent_after_opt_out

    async def withdraw_then_hold(session, preference):
        withdrawn = await withdraw(session, preference)
        await inside_opt_out()
        return withdrawn

    monkeypatch.setattr(product_watch, "withdraw_unsent_after_opt_out", withdraw_then_hold)
    run = race.spawn(worker.process_once(now=moment))
    await before_gate.wait_reached()
    opt_out = race.spawn(_patch(app_client, token, topics={"product_watch": False}))
    await inside_opt_out.wait_reached()  # the opt-out holds the preference lock, not yet committed
    before_gate.release.set()
    await _until_it_waits_on_a_lock(run, on="notification_preferences")
    inside_opt_out.release.set()
    await asyncio.wait_for(opt_out, timeout=30)
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == []
    [row] = await _watch_deliveries(account_id)
    _withdrawn(row, notifications.SUPPRESSED_MODULE_OFF)


@pytest.mark.asyncio
async def test_l2_the_gate_holding_the_preference_lock_makes_an_opt_out_wait_and_the_attempt_wins(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    in_gate = _pause_inside_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await in_gate.wait_reached()
    [claimed] = await _watch_deliveries(account_id)
    assert claimed.attempted_at is None, "inside the gate, before the marker"

    opt_out = race.spawn(_patch(app_client, token, topics={"product_watch": False}))
    await _until_it_waits_on_a_lock(opt_out, on="notification_preferences")
    in_gate.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    await asyncio.wait_for(opt_out, timeout=30)
    assert sender.messages_sent == 1
    row = await _row(claimed.id)
    assert row.status == ACCEPTED and row.attempted_at is not None and row.suppressed_reason is None


@pytest.mark.asyncio
async def test_l3_the_gate_holding_the_preference_lock_makes_an_unregister_wait_for_it_not_for_the_provider(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [device] = await _devices(account_id)
    in_gate = _pause_inside_the_gate(monkeypatch, race)
    provider = _provider_held_in_flight(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await in_gate.wait_reached()

    removal = race.spawn(_unregister(app_client, token, device.device_key))
    await _until_it_waits_on_a_lock(removal, on="notification_preferences")
    in_gate.release.set()
    await provider.held.wait_reached()
    # The attempt is in flight and the provider is still holding it: the
    # unregister, which waited for the gate, does not wait for the provider.
    assert (await asyncio.wait_for(removal, timeout=30))["removed"] is True
    provider.held.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    assert [message.to for batch in provider.batches for message in batch] == [device.expo_push_token]
    assert await _devices(account_id) == []


@pytest.mark.asyncio
async def test_l4_the_gate_holding_this_phones_device_row_makes_a_token_takeover_wait(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    _, account_a, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    [device_a] = await _devices(account_a)
    token_b, _ = await registered_supabase_user()
    sender = _sender(monkeypatch)
    in_gate = _pause_inside_the_gate(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await in_gate.wait_reached()

    takeover = race.spawn(_register(app_client, token_b, "install-b", device_a.expo_push_token))
    await _until_it_waits_on_a_lock(takeover, on="notification_devices")
    in_gate.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    await asyncio.wait_for(takeover, timeout=30)
    # Authorised before the takeover committed: that one attempt was in flight.
    assert [message.to for batch in sender.batches for message in batch] == [device_a.expo_push_token]
    [handed_over] = await _devices(account_a)
    assert handed_over.status == "disabled"
    assert _watch_messages(sender) and sender.messages_sent == 1


@pytest.mark.asyncio
async def test_l5_registering_and_unregistering_the_same_device_at_once_cannot_deadlock(
    db_clean, app_client, registered_supabase_user, monkeypatch, race,  # noqa: F811
):
    """Both take the preference lock before the device row, so one simply waits for the other."""
    token, account_id = await registered_supabase_user()
    expo_push_token = f"ExponentPushToken[{uuid.uuid4().hex}]"
    await _register(app_client, token, "install-same", expo_push_token)
    # Native push off, so registering again writes the preference row as well
    # as the device row, and unregistering writes both too: the pair that
    # would deadlock if either took them in the other order.
    await _patch(app_client, token, native_push_enabled=False)
    inside_register = race.pause()
    register_device = notifications.register_device

    async def register_then_hold(session, account, **kwargs):
        device = await register_device(session, account, **kwargs)
        await inside_register()
        return device

    monkeypatch.setattr(notifications, "register_device", register_then_hold)
    again = race.spawn(_register(app_client, token, "install-same", expo_push_token))
    await inside_register.wait_reached()  # the device row is written, not yet committed
    removal = race.spawn(_unregister(app_client, token, "install-same"))
    await _until_it_waits_on_a_lock(removal)
    inside_register.release.set()
    await asyncio.wait_for(again, timeout=30)
    assert (await asyncio.wait_for(removal, timeout=30))["removed"] is True
    assert await _devices(account_id) == []
    assert (await _preference(account_id)).native_push_enabled is False
