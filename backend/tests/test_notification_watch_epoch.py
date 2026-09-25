"""Lane D correction 2 — a watch epoch ends by lock order, not by clock order.

Stopping, reactivating or re-anchoring a Product Watch ends its epoch. A notice
the old epoch decided and has not yet sent must never be sent afterwards. That
used to be decided by comparing two timestamps:

    watch.started_at <= delivery.claimed_at

``started_at`` is written by the API process and ``claimed_at`` by the worker,
possibly on different hosts. With the API host's clock behind the worker's, a
restart that really happened after the claim can carry an earlier timestamp,
and the old notice passed as current.

The authority now is the watch lifecycle itself. With the watch row locked, the
same transaction that ends the epoch settles every delivery of that barcode
that has not reached the provider (``watch_ended``). The worker's final gate
locks the same watch row before it records an attempt, so PostgreSQL decides
the order: a lifecycle change that commits first leaves nothing to attempt; an
attempt that commits first is in flight, and the lifecycle leaves it alone.

Every skew here is injected deliberately — the API process's clock is set
behind the worker's — and every interleaving is an explicit pause. No sleeps;
the only waiting is a bounded poll of PostgreSQL's own lock tables.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from app.domains.planning import notification_strings, notifications
from app.domains.planning.models import NotificationDelivery
from app.domains.product import watch as product_watch
from app.shared.database.base import utcnow
from app.workers import notifications as worker

from tests.test_notification_delivery_integrity import (
    _factory,
    _let_the_lease_expire,
    _sender,
    _strand,
    _watch_messages,
    _watched_customer,
    race,  # noqa: F401 - re-exported fixture
)
from tests.test_notification_send_authority import (
    _pause_before_the_gate,
    _pause_inside_the_gate,
    _provider_held_in_flight,
    _row,
    _until_it_waits_on_a_lock,
    _withdrawn,
)
from tests.test_notification_worker_operations import _at_local_hour
from tests.test_official_records_api import off_clean  # noqa: F401 - re-exported fixture
from tests.test_step12c_product_watch import (
    BARCODE,
    LATER,
    _confirm,
    _delete_watch,
    _force_regulatory_publication,
    _ingest,
    _put_watch,
    _read_watch,
    _watch_deliveries,
    _watch_row,
)

SENDING = notifications.STATUS_SENDING
SUPPRESSED = notifications.STATUS_SUPPRESSED
ACCEPTED = notifications.STATUS_PROVIDER_ACCEPTED
WATCH_ENDED = notifications.SUPPRESSED_WATCH_ENDED


@contextmanager
def _api_clock_behind(monkeypatch, instant):
    """The API process's clock reads ``instant`` while it changes the watch.

    Only the watch module's clock moves — the one ``start_watch`` stamps
    ``started_at`` with. The worker's clock, which stamps ``claimed_at``, is
    untouched: two hosts that disagree.
    """
    with monkeypatch.context() as clock:
        clock.setattr(product_watch, "utcnow", lambda: instant)
        yield


async def _stranded_behind_an_expired_lease(app_client, registered_supabase_user, tmp_path, monkeypatch):
    """A watched notice D, claimed and never attempted; its lease has expired.

    The whole timeline moves back together, so D's claim is later than the
    watch's start, exactly as it really happened.
    """
    moment = _at_local_hour(9)
    token, account_id, device = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    await _strand(monkeypatch, moment)
    await _let_the_lease_expire(account_id)
    [stranded] = await _watch_deliveries(account_id)
    assert stranded.status == SENDING and stranded.claim_token and stranded.attempted_at is None
    assert (await _watch_row(account_id)).started_at < stranded.claimed_at
    return moment, token, account_id, device, stranded


async def _re_anchor_to_a_newer_pack(app_client, device, token, account_id):
    await _confirm(app_client, device, token, account_id, batch_number="NEWER-PACK")
    state = await _read_watch(app_client, token, device)
    assert state.status_code == 200 and state.json()["watchable"] is True, state.text
    return state.json()["anchorable_label_version"]


# ===========================================================================
# The helper's scope
# ===========================================================================
@pytest.mark.asyncio
async def test_the_epoch_withdrawal_touches_only_this_accounts_unattempted_deliveries_for_this_barcode(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    _, other_account = await registered_supabase_user()
    now = utcnow()
    other_barcode = "8901234567890" if BARCODE != "8901234567890" else "8909876543210"

    async def delivery(account, *, source_kind=product_watch.SOURCE_KIND, source_id=BARCODE, **fields):
        async with _factory()() as session:
            row = NotificationDelivery(
                account_id=account, plan_date=now.date(), notification_key=uuid.uuid4().hex[:16],
                dedup_hash=uuid.uuid4().hex, title="Watched", source_kind=source_kind, source_id=source_id,
                **fields,
            )
            session.add(row)
            await session.commit()
            return row

    queued = await delivery(account_id, status=notifications.STATUS_QUEUED)
    claimed = await delivery(account_id, status=SENDING, claim_token="claimed", claimed_at=now)
    attempted = await delivery(account_id, status=SENDING, claim_token="attempted", claimed_at=now, attempted_at=now)
    accepted = await delivery(account_id, status=ACCEPTED, attempted_at=now, sent_at=now)
    failed = await delivery(account_id, status=notifications.STATUS_PROVIDER_FAILED, attempted_at=now)
    another_barcode = await delivery(account_id, source_id=other_barcode, status=SENDING, claim_token="b", claimed_at=now)
    not_product_watch = await delivery(account_id, source_kind="agenda", status=SENDING, claim_token="c", claimed_at=now)
    someone_elses = await delivery(other_account, status=SENDING, claim_token="theirs", claimed_at=now)

    async with _factory()() as session:
        withdrawn = await notifications.withdraw_unattempted(
            session, account_id, source_kind=product_watch.SOURCE_KIND, source_id=BARCODE, reason=WATCH_ENDED,
        )
        await session.commit()

    assert withdrawn == 2
    _withdrawn(await _row(queued.id), WATCH_ENDED)
    _withdrawn(await _row(claimed.id), WATCH_ENDED)
    still = await _row(attempted.id)
    assert (still.status, still.claim_token, still.attempted_at) == (SENDING, "attempted", attempted.attempted_at)
    assert (await _row(accepted.id)).status == ACCEPTED
    assert (await _row(failed.id)).status == notifications.STATUS_PROVIDER_FAILED
    assert (await _row(another_barcode.id)).claim_token == "b", "another barcode's delivery"
    assert (await _row(not_product_watch.id)).claim_token == "c", "not a Product Watch delivery"
    assert (await _row(someone_elses.id)).claim_token == "theirs", "another account's delivery"


@pytest.mark.asyncio
async def test_stopping_a_watch_settles_only_its_own_barcodes_unattempted_deliveries(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """Through the real route: the watch's own pending notice ends; nothing else moves."""
    moment, token, account_id, _, stranded = await _stranded_behind_an_expired_lease(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
    )
    now = utcnow()
    async with _factory()() as session:
        another = NotificationDelivery(
            account_id=account_id, plan_date=now.date(), notification_key="other-barcode",
            dedup_hash=uuid.uuid4().hex, title="Other", source_kind=product_watch.SOURCE_KIND,
            source_id="0000000000000", status=SENDING, claim_token="other", claimed_at=now,
        )
        history = NotificationDelivery(
            account_id=account_id, plan_date=now.date(), notification_key="history",
            dedup_hash=uuid.uuid4().hex, title="Delivered", source_kind=product_watch.SOURCE_KIND,
            source_id=BARCODE, status=ACCEPTED, attempted_at=now, sent_at=now,
        )
        session.add_all([another, history])
        await session.commit()

    assert (await _delete_watch(app_client, token)).status_code == 200
    _withdrawn(await _row(stranded.id), WATCH_ENDED)
    assert (await _row(another.id)).claim_token == "other"
    assert (await _row(history.id)).status == ACCEPTED, "delivery history is not erased"
    # A second stop is safe and finds nothing more to settle.
    assert (await _delete_watch(app_client, token)).status_code == 200
    _withdrawn(await _row(stranded.id), WATCH_ENDED)
    assert (await _row(another.id)).claim_token == "other"


# ===========================================================================
# R10 — stop, then restart, with the API clock behind the worker's
# ===========================================================================
@pytest.mark.asyncio
async def test_r10_stop_then_restart_ends_the_old_epoch_even_when_the_clocks_say_otherwise(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment, token, account_id, device, stranded = await _stranded_behind_an_expired_lease(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
    )
    assert (await _delete_watch(app_client, token)).status_code == 200
    # No worker cycle between the stop and the restart. The restart happens
    # after D's claim, but its host's clock is behind: it stamps a start
    # earlier than the claim.
    with _api_clock_behind(monkeypatch, stranded.claimed_at - timedelta(seconds=5)):
        assert (await _put_watch(app_client, device, token)).status_code == 200
    restarted = await _watch_row(account_id)
    assert restarted.active is True
    assert restarted.started_at <= stranded.claimed_at, "the timestamps would accept the old notice"

    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert _watch_messages(sender) == [] and sender.batches == [], "the old epoch's notice is never sent"
    _withdrawn(await _row(stranded.id), WATCH_ENDED)
    assert await worker.process_once(now=moment) == 0, "and never recovered"

    # The new epoch works: a genuinely new fact produces a notice.
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    assert await worker.process_once(now=moment) == 1
    [batch] = sender.batches
    assert [message.title for message in batch] == [notification_strings.PRODUCT_WATCH_REGULATORY_CHANGE_TITLE]
    _withdrawn(await _row(stranded.id), WATCH_ENDED)


# ===========================================================================
# R11 — a direct re-anchor, with the API clock behind the worker's
# ===========================================================================
@pytest.mark.asyncio
async def test_r11_a_direct_re_anchor_ends_the_old_epoch_even_when_the_clocks_say_otherwise(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment, token, account_id, device, stranded = await _stranded_behind_an_expired_lease(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
    )
    before = await _watch_row(account_id)
    newer = await _re_anchor_to_a_newer_pack(app_client, device, token, account_id)
    # No stop in between: the same active watch moves to pack B.
    with _api_clock_behind(monkeypatch, stranded.claimed_at - timedelta(seconds=5)):
        assert (await _put_watch(app_client, device, token, label_version=newer)).status_code == 200
    after = await _watch_row(account_id)
    assert after.active is True and after.anchor_scan_event_id != before.anchor_scan_event_id, "anchored to B"
    assert after.anchor_label_version == newer
    assert after.started_at <= stranded.claimed_at, "the timestamps would accept the old notice"
    assert after.notice_cursor != before.notice_cursor, "a new baseline for the new pack"
    product_watch.WatchCursor.from_json(after.notice_cursor)

    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 0
    assert _watch_messages(sender) == [] and sender.batches == []
    _withdrawn(await _row(stranded.id), WATCH_ENDED)
    assert [row.id for row in await _watch_deliveries(account_id)] == [stranded.id], (
        "nothing already known about pack B was replayed as news"
    )


# ===========================================================================
# R12 — the attempt marker wins; then the stop proceeds
# ===========================================================================
@pytest.mark.asyncio
async def test_r12_an_attempt_recorded_before_the_stop_is_in_flight_and_never_rewritten(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    in_gate = _pause_inside_the_gate(monkeypatch, race)
    provider = _provider_held_in_flight(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await in_gate.wait_reached()  # the gate holds the watch row; nothing attempted yet
    [claimed] = await _watch_deliveries(account_id)
    assert claimed.attempted_at is None

    stop = race.spawn(_delete_watch(app_client, token))
    await _until_it_waits_on_a_lock(stop, on="product_watches")
    in_gate.release.set()
    await provider.held.wait_reached()  # the marker committed; Expo has the request
    assert (await asyncio.wait_for(stop, timeout=30)).status_code == 200

    during = await _row(claimed.id)
    assert during.status == SENDING and during.attempted_at is not None, "in flight"
    assert during.claim_token is not None and during.suppressed_reason is None, "not rewritten as unsent"
    assert (await _watch_row(account_id)).active is False
    provider.held.release.set()
    assert await asyncio.wait_for(run, timeout=60) == 1
    assert provider.messages_sent == 1
    assert (await _row(claimed.id)).status == ACCEPTED

    await _let_the_lease_expire(account_id)
    assert await worker.process_once(now=moment) == 0
    assert provider.messages_sent == 1, "the one original attempt, and no other"
    async with _factory()() as session:
        assert await notifications.claim_delivery(session, claimed.id, lease_seconds=0) is None
        await session.rollback()


# ===========================================================================
# R13 — the lifecycle change wins; then the gate proceeds
# ===========================================================================
def _hold_the_lifecycle_after_it_settles(monkeypatch, pauses):
    """Pause the watch lifecycle change once it has settled the old epoch, before it commits."""
    paused = pauses.pause()
    end_epoch = product_watch._end_epoch_deliveries

    async def settle_then_hold(session, **kwargs):
        ended = await end_epoch(session, **kwargs)
        await paused()
        return ended

    monkeypatch.setattr(product_watch, "_end_epoch_deliveries", settle_then_hold)
    return paused


@pytest.mark.asyncio
async def test_r13_a_stop_holding_the_watch_before_the_marker_means_no_provider_call(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    moment = _at_local_hour(9)
    token, account_id, _ = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    sender = _sender(monkeypatch)
    before_gate = _pause_before_the_gate(monkeypatch, race)
    inside_stop = _hold_the_lifecycle_after_it_settles(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await before_gate.wait_reached()
    [claimed] = await _watch_deliveries(account_id)

    stop = race.spawn(_delete_watch(app_client, token))
    await inside_stop.wait_reached()  # the stop holds the watch and has settled D, uncommitted
    before_gate.release.set()
    await _until_it_waits_on_a_lock(run, on="product_watches")
    inside_stop.release.set()
    assert (await asyncio.wait_for(stop, timeout=30)).status_code == 200
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == [], "zero provider calls"
    _withdrawn(await _row(claimed.id), WATCH_ENDED)
    async with _factory()() as session:
        assert not await notifications.mark_attempt_started(session, claimed.id, claimed.claim_token)
        await session.rollback()


@pytest.mark.asyncio
async def test_r13_a_re_anchor_holding_the_watch_before_the_marker_means_no_provider_call_even_with_skewed_clocks(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, race,  # noqa: F811
):
    """The watch is active again when the gate runs, and its timestamps pass. The marker still cannot."""
    moment = _at_local_hour(9)
    token, account_id, device = await _watched_customer(app_client, registered_supabase_user, tmp_path)
    newer = await _re_anchor_to_a_newer_pack(app_client, device, token, account_id)
    sender = _sender(monkeypatch)
    before_gate = _pause_before_the_gate(monkeypatch, race)
    inside_re_anchor = _hold_the_lifecycle_after_it_settles(monkeypatch, race)
    run = race.spawn(worker.process_once(now=moment))
    await before_gate.wait_reached()
    [claimed] = await _watch_deliveries(account_id)

    with _api_clock_behind(monkeypatch, claimed.claimed_at - timedelta(seconds=5)):
        re_anchor = race.spawn(_put_watch(app_client, device, token, label_version=newer))
        await inside_re_anchor.wait_reached()
        before_gate.release.set()
        await _until_it_waits_on_a_lock(run, on="product_watches")
        inside_re_anchor.release.set()
        assert (await asyncio.wait_for(re_anchor, timeout=30)).status_code == 200
    moved = await _watch_row(account_id)
    assert moved.active is True and moved.started_at <= claimed.claimed_at, "the timestamps pass"
    assert await asyncio.wait_for(run, timeout=60) == 0
    assert sender.batches == [], "zero provider calls"
    _withdrawn(await _row(claimed.id), WATCH_ENDED)


# ===========================================================================
# R14 — the same pack again is not a transition
# ===========================================================================
@pytest.mark.asyncio
async def test_r14_starting_the_same_active_watch_on_the_same_pack_does_not_cancel_its_current_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    moment, token, account_id, device, stranded = await _stranded_behind_an_expired_lease(
        app_client, registered_supabase_user, tmp_path, monkeypatch,
    )
    before = await _watch_row(account_id)
    assert (await _put_watch(app_client, device, token)).status_code == 200
    after = await _watch_row(account_id)
    assert (
        after.active, after.anchor_scan_event_id, after.anchor_label_snapshot_id,
        after.started_at, after.notice_cursor,
    ) == (
        before.active, before.anchor_scan_event_id, before.anchor_label_snapshot_id,
        before.started_at, before.notice_cursor,
    ), "the watch is unchanged"
    kept = await _row(stranded.id)
    assert (kept.status, kept.claim_token, kept.suppressed_reason) == (SENDING, stranded.claim_token, None)

    # Still this epoch's notice: recovered and sent, once.
    sender = _sender(monkeypatch)
    assert await worker.process_once(now=moment) == 1
    assert len(_watch_messages(sender)) == 1
    assert (await _row(stranded.id)).status == ACCEPTED
