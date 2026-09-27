"""Step 12C — Product Watch: material notices for a pack the customer chose to watch.

Three questions, three authorities, and this suite proves the boundary between
them holds from every side:

* Is it true?  Step 12A, Step 12B and the official-records matcher decide.
* Did the customer ask to hear about it?  Product Watch decides.
* May it be delivered, and how?  The existing notification outbox decides.

The rule that matters most: a notification is itself a customer-facing claim.
If the product screen may not state a fact, a push may not state it either.
Every test below that forces an evidence gate open does so only to prove that
the wiring on the far side of that gate works; the gates are closed in
production, and the tests that leave them closed prove Product Watch stays
silent.

Letters follow the Step 12C brief's test matrix, A to AF.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.official_records import service as official_records
from app.domains.official_records.models import OfficialRecordRevision
from app.domains.planning import clock, notification_strings, notifications, push
from app.domains.planning.models import NotificationDelivery, NotificationPreference
from app.domains.privacy import REGISTRY, Classification
from app.domains.privacy import export as export_service
from app.domains.product import label_evidence
from app.domains.product import watch as product_watch
from app.domains.product.models import LabelSnapshot, ProductWatch, ScanEvent
from app.domains.routines.safety import DIAGNOSTIC_TERMS
from app.shared.database.sql import get_sessionmaker
from app.workers import notifications as worker
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth
from tests.test_notification_worker_operations import _at_local_hour, _CountingPush, _opt_in
from tests.test_official_records import data_row, make_export
from tests.test_official_records_api import (
    BARCODE,
    BATCH,
    BRAND,
    PRODUCT,
    RECALL_ID,
    SOURCE_CHECKED_AT,
    confirm_label,
    label_facts,
    off_clean,  # noqa: F401 - re-exported fixture
)
from tests.test_privacy_erasure_completeness import (
    fake_admin,  # noqa: F401 - re-exported fixture
    fake_storage,  # noqa: F401 - re-exported fixture
)

#: A second, unrelated product for tests that need two watches.
OTHER_BARCODE = "8901058000214"
OTHER_BATCH = "C-456"
OTHER_RECALL_ID = "902"
TZ = clock.DEFAULT_TIMEZONE
LATER = SOURCE_CHECKED_AT + timedelta(days=3)
LATER_STILL = SOURCE_CHECKED_AT + timedelta(days=6)
DAY_ONE = date(2026, 9, 1)

#: Words a Product Watch notice may never use. The brief's list, plus the
#: temporal claims our own observation time cannot support.
FORBIDDEN_COPY = (
    "dangerous", "unsafe", "safe", "cleared", "warning", "urgent", "improved",
    "worse", "reformulated", "fixed", "healthier", "new recall", "newly",
    "just recalled", "resolved", "alert", "danger", "better", "stronger",
)


# ---------------------------------------------------------------------------
# Helpers — every fact below is written through the real product paths
# ---------------------------------------------------------------------------
async def _device(app_client) -> dict[str, str]:
    response = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert response.status_code == 201, response.text
    return {"X-Device-Token": response.json()["token"]}


async def _claim(app_client, device, token) -> None:
    response = await app_client.post("/api/v2/scan/device/claim", headers={**device, **auth(token)})
    assert response.status_code == 200, response.text


async def _customer(app_client, registered_supabase_user, *, claim: bool = True):
    token, account_id = await registered_supabase_user()
    device = await _device(app_client)
    if claim:
        await _claim(app_client, device, token)
    return token, account_id, device


async def _confirm(app_client, device, token, account_id, barcode=BARCODE, **facts):
    return await confirm_label(app_client, device, token, account_id, barcode, label_facts(**facts))


async def _read_watch(app_client, token, device=None, barcode=BARCODE):
    return await app_client.get(
        f"/api/v2/scan/verdict/{barcode}/watch", headers={**(device or {}), **auth(token)},
    )


async def _put_watch(app_client, device, token, barcode=BARCODE, label_version=1):
    return await app_client.put(
        f"/api/v2/scan/verdict/{barcode}/watch",
        headers={**device, **auth(token)}, json={"label_version": label_version},
    )


async def _delete_watch(app_client, token, barcode=BARCODE):
    return await app_client.delete(f"/api/v2/scan/verdict/{barcode}/watch", headers=auth(token))


async def _watching(app_client, device, token, account_id, barcode=BARCODE, **facts):
    """Confirm a pack and watch it — the whole explicit customer journey."""
    await _confirm(app_client, device, token, account_id, barcode, **facts)
    state = await _read_watch(app_client, token, device, barcode)
    assert state.status_code == 200, state.text
    assert state.json()["watchable"] is True, state.json()
    response = await _put_watch(
        app_client, device, token, barcode, state.json()["anchorable_label_version"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["watching"] is True
    return response.json()


async def _ingest_rows(tmp_path: Path, rows, *, checked_at=SOURCE_CHECKED_AT):
    path = make_export(tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx", rows=rows)
    async with get_sessionmaker()() as session:
        _fetch, counts = await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=checked_at,
        )
        await session.commit()
        return counts


def _row(*, recall_id=RECALL_ID, batch=BATCH, brand=BRAND, product=PRODUCT, **changes):
    return data_row(recall_id=int(recall_id), batch=batch, brand=brand, product=product, **changes)


async def _ingest(tmp_path, *, checked_at=SOURCE_CHECKED_AT, **row):
    return await _ingest_rows(tmp_path, [_row(**row)], checked_at=checked_at)


async def _decide(account_id, *, plan_date=DAY_ONE, hour=9, tz=TZ):
    """One Product Watch decision, committed exactly as the worker commits it."""
    moment = _moment(plan_date, hour, tz)
    async with get_sessionmaker()() as session:
        delivery = await product_watch.queue_material_notice(
            session, account_id=account_id, plan_date=plan_date, timezone_name=tz, moment=moment,
        )
        await session.commit()
        if delivery is None:
            return None
        return notifications.serialize_delivery(delivery)


def _moment(plan_date: date, hour: int, tz: str = TZ) -> datetime:
    """The UTC instant at ``hour`` o'clock local time on ``plan_date``."""
    from zoneinfo import ZoneInfo

    local = datetime(plan_date.year, plan_date.month, plan_date.day, hour, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC)


async def _set_preferences(account_id, **values):
    async with get_sessionmaker()() as session:
        preference = await notifications.preferences_for(session, account_id, TZ, lock=True)
        for name, value in values.items():
            setattr(preference, name, value)
        await session.commit()


async def _watch_row(account_id, barcode=BARCODE) -> ProductWatch | None:
    async with get_sessionmaker()() as session:
        return (await session.execute(select(ProductWatch).where(
            ProductWatch.account_id == account_id, ProductWatch.barcode == barcode,
        ))).scalar_one_or_none()


async def _watch_rows() -> list[ProductWatch]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(select(ProductWatch))).scalars().all())


async def _deliveries(account_id) -> list[NotificationDelivery]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.account_id == account_id)
            .order_by(NotificationDelivery.plan_date, NotificationDelivery.created_at)
        )).scalars().all())


async def _watch_deliveries(account_id) -> list[NotificationDelivery]:
    return [row for row in await _deliveries(account_id) if row.source_kind == product_watch.SOURCE_KIND]


async def _snapshots(barcode=BARCODE) -> list[LabelSnapshot]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == barcode)
            .order_by(LabelSnapshot.version_number)
        )).scalars().all())


def _cursor(watch: ProductWatch) -> product_watch.WatchCursor:
    return product_watch.WatchCursor.from_json(watch.notice_cursor)


def _force_regulatory_publication(monkeypatch):
    """Step 12B's evidence gate, forced open in test scope only.

    Production is untouched: ``revision_source()`` still returns ``None`` and
    the gate still refuses. This proves only that Product Watch reads the
    gate's answer rather than the fields behind it.
    """
    monkeypatch.setattr(
        official_records.change_evidence, "regulatory_change_is_publishable",
        lambda **_kwargs: True,
    )


def _force_label_publication(monkeypatch):
    """Step 12A's evidence gate, forced open in test scope only."""
    monkeypatch.setattr(label_evidence, "comparison_is_publishable", lambda **_kwargs: True)


# ---------------------------------------------------------------------------
# A. Creating a watch is explicit and idempotent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_scanning_and_confirming_never_start_a_watch(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """Context is not consent. Only the explicit request creates a watch."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, device, token, account_id)
    verdict = await app_client.get(f"/api/v2/scan/verdict/{BARCODE}", headers=device)
    assert verdict.status_code == 200
    state = await _read_watch(app_client, token, device)

    assert await _watch_rows() == [], "a scan, a confirmation or a view created a watch"
    assert state.json()["watching"] is False
    assert state.json()["watchable"] is True


@pytest.mark.asyncio
async def test_a_watching_the_same_pack_twice_is_one_watch(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    first = await _watching(app_client, device, token, account_id)
    second = await _put_watch(app_client, device, token)

    assert second.status_code == 200
    assert second.json() == first, "an idempotent repeat must return the same state"
    rows = await _watch_rows()
    assert len(rows) == 1
    assert rows[0].active is True
    assert first["label_version"] == 1
    assert first["watching_this_pack"] is True


def test_a_only_the_watch_service_constructs_a_watch_row():
    """No other code path can infer a watch: nothing else builds the row.

    Scans, shelf links, purchases, decision memory, community reports and OFF
    lookups can establish context. The row itself is written in one place.
    """
    backend_app = Path(__file__).resolve().parents[1] / "app"
    builders = []
    for path in sorted(backend_app.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name == "ProductWatch":
                    builders.append(path.relative_to(backend_app).as_posix())
    assert builders == ["domains/product/watch.py"], builders


# ---------------------------------------------------------------------------
# B. Another account cannot read or change the watch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_b_another_account_sees_nothing_and_changes_nothing(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    owner_token, owner_id, owner_device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, owner_device, owner_token, owner_id)
    stranger_token, _stranger_id, stranger_device = await _customer(app_client, registered_supabase_user)

    read = await _read_watch(app_client, stranger_token, stranger_device)
    stopped = await _delete_watch(app_client, stranger_token)

    assert read.status_code == 200
    assert read.json()["watching"] is False, "one account read another's watch"
    assert read.json()["started_at"] is None
    assert read.json()["label_version"] is None
    assert stopped.status_code == 200
    assert stopped.json()["watching"] is False
    owner_row = await _watch_row(owner_id)
    assert owner_row is not None and owner_row.active is True, "a stranger stopped someone else's watch"
    assert len(await _watch_rows()) == 1


@pytest.mark.asyncio
async def test_b_a_device_claimed_by_someone_else_cannot_anchor_a_watch(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """The Step 10A rule: owning a device is not owning every capture on it."""
    owner_token, owner_id, owner_device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, owner_device, owner_token, owner_id)
    stranger_token, stranger_id, _ = await _customer(app_client, registered_supabase_user)

    # The stranger presents the owner's device, with the owner's capture on it.
    borrowed = await _put_watch(app_client, owner_device, stranger_token)
    # The stranger confirms their own label on the owner's device. The device is
    # still the owner's, so this capture cannot be watched by the stranger —
    # and it is not the owner's capture, so the owner cannot watch it either.
    await _confirm(app_client, owner_device, stranger_token, stranger_id)
    stranger_capture = await _put_watch(app_client, owner_device, stranger_token)
    owner_on_foreign_capture = await _put_watch(app_client, owner_device, owner_token)

    for response in (borrowed, stranger_capture, owner_on_foreign_capture):
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == product_watch.REASON_CONFIRMED_PACK_REQUIRED
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_b_the_refusal_does_not_reveal_whose_capture_is_on_the_device(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """Every way of lacking a pack gives the same answer, so none can be told apart."""
    owner_token, owner_id, owner_device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, owner_device, owner_token, owner_id)
    stranger_token, _, stranger_device = await _customer(app_client, registered_supabase_user)

    someone_elses = await _put_watch(app_client, owner_device, stranger_token)
    nothing_at_all = await _put_watch(app_client, stranger_device, stranger_token)

    assert someone_elses.status_code == nothing_at_all.status_code == 409
    assert someone_elses.json() == nothing_at_all.json()


# ---------------------------------------------------------------------------
# C. A watch needs a confirmed, account-bound pack
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_c_open_food_facts_alone_cannot_create_a_watch(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, _account_id, device = await _customer(app_client, registered_supabase_user)
    async with get_off_sessionmaker()() as session:
        session.add(OffProduct(
            barcode=BARCODE, product_name=PRODUCT, brands=BRAND,
            ingredients_text="oats, sugar, salt",
            nutriments={"sugars_100g": 12.0}, fetched_at=datetime.now(UTC),
        ))
        await session.commit()
    scanned = await app_client.post(
        "/api/v2/scan/events", headers=device,
        json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
    )
    assert scanned.status_code == 201

    state = await _read_watch(app_client, token, device)
    response = await _put_watch(app_client, device, token)

    assert state.json()["watchable"] is False
    assert state.json()["reason"] == product_watch.REASON_CONFIRMED_PACK_REQUIRED
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == product_watch.REASON_CONFIRMED_PACK_REQUIRED
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_c_an_unclaimed_device_cannot_anchor_a_watch(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user, claim=False)
    await _confirm(app_client, device, token, account_id)

    response = await _put_watch(app_client, device, token)

    assert response.status_code == 409
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_c_a_later_plain_scan_means_the_pack_in_hand_is_no_longer_proven(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """The device's newest scan decides, exactly as the verdict's pack authority does."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, device, token, account_id)
    await app_client.post(
        "/api/v2/scan/events", headers=device,
        json={"barcode": BARCODE, "client_scan_id": uuid.uuid4().hex},
    )

    response = await _put_watch(app_client, device, token)

    assert response.status_code == 409
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_c_the_anchor_is_this_pack_s_version_not_the_newest_anyone_published(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """A stranger's newer label must not become the version this customer watches."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, device, token, account_id)
    stranger_token, stranger_id, stranger_device = await _customer(app_client, registered_supabase_user)
    await _confirm(
        app_client, stranger_device, stranger_token, stranger_id,
        ingredients_text="oats, jaggery, salt",
    )
    assert [s.version_number for s in await _snapshots()] == [1, 2]

    state = await _read_watch(app_client, token, device)
    wrong = await _put_watch(app_client, device, token, label_version=2)
    right = await _put_watch(app_client, device, token, label_version=1)

    assert state.json()["anchorable_label_version"] == 1
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "watch_context_changed"
    assert right.status_code == 200
    row = await _watch_row(account_id)
    first = (await _snapshots())[0]
    assert row.anchor_label_snapshot_id == first.id
    assert row.anchor_label_version == 1


@pytest.mark.asyncio
async def test_c_only_a_routable_barcode_can_be_watched(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, _account_id, device = await _customer(app_client, registered_supabase_user)
    for barcode in ("ABCDEFGH", "1234567", "123456789012345"):
        read = await _read_watch(app_client, token, device, barcode)
        put = await _put_watch(app_client, device, token, barcode)
        assert read.status_code == 422, barcode
        assert put.status_code == 422, barcode
        assert put.json()["detail"]["code"] == "barcode_not_watchable"
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_c_the_routes_require_a_signed_in_account(db_clean, off_clean, app_client):  # noqa: F811
    device = await _device(app_client)
    assert (await app_client.get(f"/api/v2/scan/verdict/{BARCODE}/watch", headers=device)).status_code == 401
    assert (await app_client.put(
        f"/api/v2/scan/verdict/{BARCODE}/watch", headers=device, json={"label_version": 1},
    )).status_code == 401
    assert (await app_client.delete(f"/api/v2/scan/verdict/{BARCODE}/watch")).status_code == 401


# ---------------------------------------------------------------------------
# D / F. Watching sends nothing; everything already known is baseline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_d_watch_creation_sends_nothing_and_records_the_baseline(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path)
    await _watching(app_client, device, token, account_id)

    assert await _deliveries(account_id) == []
    cursor = _cursor(await _watch_row(account_id))
    assert cursor.records == {RECALL_ID: product_watch.RecordBaseline(revisions=(1,), baseline_unknown=False)}
    assert cursor.label_baseline_version == 1
    assert cursor.label_notified_versions == ()
    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == []


@pytest.mark.asyncio
async def test_f_an_official_record_that_already_matched_never_notifies(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """The full hourly worker, with the record in place before the watch began."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)

    await worker.process_once(now=_at_local_hour(9))

    assert await _watch_deliveries(account_id) == []
    for batch in sender.batches:
        for message in batch:
            assert message.title != notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE


@pytest.mark.asyncio
async def test_d_existing_label_versions_are_baseline_not_change(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """Versions a stranger published before the watch began are already known."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, device, token, account_id)
    stranger_token, stranger_id, stranger_device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, stranger_device, stranger_token, stranger_id, ingredients_text="oats, jaggery")
    await _put_watch(app_client, device, token, label_version=1)

    cursor = _cursor(await _watch_row(account_id))
    assert cursor.label_baseline_version == 2


# ---------------------------------------------------------------------------
# E. An exact official record appears after the baseline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_e_a_record_that_begins_to_match_produces_one_current_state_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)

    delivery = await _decide(account_id)

    assert delivery is not None
    assert delivery["status"] == notifications.STATUS_QUEUED
    assert delivery["title"] == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    assert delivery["body"] == notification_strings.PRODUCT_WATCH_RECORD_MATCH_BODY
    assert delivery["deep_link"] == "/verdict"
    assert delivery["destination_params"] == {"barcode": BARCODE}
    assert delivery["source_kind"] == "product_watch"
    assert delivery["source_id"] == BARCODE
    assert delivery["notification_key"].startswith("pw:a:")
    # A current-state claim. Nothing about when the regulator acted, and none
    # of the register's own values ride along in the push.
    words = f"{delivery['title']} {delivery['body']}".lower()
    for forbidden in ("new", "just", "recalled", "initiated", BATCH.lower(), "synthetic reason"):
        assert forbidden not in words
    row = await _watch_row(account_id)
    assert RECALL_ID in _cursor(row).records
    assert row.last_notified_at is not None


@pytest.mark.asyncio
async def test_e_the_same_event_is_one_decision_however_often_it_is_evaluated(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)

    first = await _decide(account_id)
    again = await _decide(account_id)
    next_day = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1))

    assert first is not None
    assert again is None
    assert next_day is None
    assert len(await _watch_deliveries(account_id)) == 1


def test_e_the_event_identity_is_semantic_and_stable():
    """Two evaluations of the same state give the same key; timestamps play no part."""
    notice = product_watch.WatchNotice(
        kind=product_watch.KIND_RECORD_MATCH, barcode=BARCODE, recall_id=RECALL_ID,
        observation_key="2026-08-01",
    )
    same = product_watch.WatchNotice(
        kind=product_watch.KIND_RECORD_MATCH, barcode=BARCODE, recall_id=RECALL_ID,
        observation_key="2026-08-01",
    )
    other_record = replace(notice, recall_id=OTHER_RECALL_ID)

    assert notice.notification_key == same.notification_key
    assert notice.notification_key != other_record.notification_key
    assert len(notice.notification_key) <= 64
    assert notice.notification_key.startswith("pw:a:")


# ---------------------------------------------------------------------------
# G. Source omission is never "cleared"
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g_a_record_absent_from_a_later_export_produces_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    assert await _decide(account_id) is not None
    # A later, valid export that simply does not contain the record.
    await _ingest_rows(
        tmp_path, [_row(recall_id="903", batch="Z-000", brand="Other", product="Other")],
        checked_at=LATER,
    )

    for offset in (1, 2):
        assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=offset)) is None
    assert len(await _watch_deliveries(account_id)) == 1


@pytest.mark.asyncio
async def test_g_a_record_that_stops_matching_produces_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Revision 2 names another lot. That is not "your pack is fine"."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    assert await _decide(account_id) is not None
    await _ingest(tmp_path, checked_at=LATER, batch="B-999")

    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1)) is None
    assert len(await _watch_deliveries(account_id)) == 1


# ---------------------------------------------------------------------------
# H. An ambiguous match produces nothing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_h_ambiguous_official_records_produce_no_candidate(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id, brand=None, product_name=None)
    await _ingest_rows(tmp_path, [
        _row(recall_id="901", brand="Northstar", product="Cereal A"),
        _row(recall_id="902", brand="Southline", product="Cereal B"),
    ])

    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == []


# ---------------------------------------------------------------------------
# I. Open Food Facts identity never reaches the matcher
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_i_off_brand_and_product_cannot_make_a_record_match(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """The pack's own lot is not the recalled lot; OFF agreeing on names changes nothing."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    async with get_off_sessionmaker()() as session:
        session.add(OffProduct(
            barcode=BARCODE, product_name=PRODUCT, brands=BRAND,
            ingredients_text="oats", nutriments={}, fetched_at=datetime.now(UTC),
        ))
        await session.commit()
    await _watching(app_client, device, token, account_id, batch_number="OTHER-LOT")
    await _ingest(tmp_path)

    assert await _decide(account_id) is None


@pytest.mark.asyncio
async def test_i_the_watch_matches_on_this_capture_not_on_a_stranger_s_lot(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Batch is outside the fingerprint, so a snapshot can carry a stranger's lot.

    The customer's own capture is the matching authority. Their pack is lot
    OTHER-LOT; a stranger's identical-content capture of lot B-123 must not make
    B-123's recall this customer's notice.
    """
    stranger_token, stranger_id, stranger_device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, stranger_device, stranger_token, stranger_id)
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id, batch_number="OTHER-LOT")
    row = await _watch_row(account_id)
    assert len(await _snapshots()) == 1, "same content, one shared version"
    await _ingest(tmp_path)

    assert await _decide(account_id) is None
    async with get_sessionmaker()() as session:
        anchor = await session.get(ScanEvent, row.anchor_scan_event_id)
    assert anchor.account_id == account_id
    assert anchor.label_facts["batch_number"] == "OTHER-LOT"


# ---------------------------------------------------------------------------
# J / K / L. Step 12B's gate is Product Watch's gate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_j_an_unavailable_regulatory_change_produces_no_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """12B knows recall_status moved. It may not say so, so neither may a push."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path, status="Initiated")
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    before = (await _watch_row(account_id)).notice_cursor

    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == []
    # Unavailable is not "nothing changed": the cursor did not move.
    assert (await _watch_row(account_id)).notice_cursor == before


@pytest.mark.asyncio
async def test_k_a_publishable_regulatory_change_reaches_the_customer_through_the_real_wiring(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path, status="Initiated")
    await _watching(app_client, device, token, account_id)
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path, checked_at=LATER, status="Completed")

    delivery = await _decide(account_id)

    assert delivery is not None
    assert delivery["title"] == notification_strings.PRODUCT_WATCH_REGULATORY_CHANGE_TITLE
    assert delivery["body"] == notification_strings.PRODUCT_WATCH_REGULATORY_CHANGE_BODY
    assert delivery["notification_key"].startswith("pw:b:")
    # The before/after stays on the product screen, beside its source.
    words = f"{delivery['title']} {delivery['body']}".lower()
    for raw in ("initiated", "completed", "recall_status", BATCH.lower()):
        assert raw not in words
    assert _cursor(await _watch_row(account_id)).records[RECALL_ID].revisions == (1, 2)
    # Decided once. The same revision is never a second notice.
    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1)) is None


@pytest.mark.asyncio
async def test_l_corrupt_history_with_evidence_forced_open_still_produces_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch, caplog,  # noqa: F811
):
    """Integrity wins, and the underlying authority's operator log survives."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path, status="Initiated")
    await _watching(app_client, device, token, account_id)
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        await session.execute(
            update(OfficialRecordRevision)
            .where(OfficialRecordRevision.revision_number == 2)
            .values(content_hash="d" * 64)
        )
        await session.commit()

    with caplog.at_level(logging.WARNING):
        assert await _decide(account_id) is None
    assert "regulatory_history_invariant_failed" in caplog.text
    assert await _deliveries(account_id) == []


@pytest.mark.asyncio
async def test_l_a_record_whose_history_was_broken_at_baseline_never_produces_a_change(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """With no validated head to compare against, no later revision is provably new."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Ongoing")
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE official_records SET latest_revision = 1"))
        await session.commit()
    await _watching(app_client, device, token, account_id)
    assert _cursor(await _watch_row(account_id)).records[RECALL_ID].baseline_unknown is True
    # The ledger is later repaired and a publishable revision arrives.
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE official_records SET latest_revision = 2"))
        await session.commit()
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path, checked_at=LATER_STILL, status="Completed")

    assert await _decide(account_id) is None


# ---------------------------------------------------------------------------
# M / N / O. Step 12A's gate is Product Watch's gate
# ---------------------------------------------------------------------------
async def _two_label_versions(app_client, registered_supabase_user):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _confirm(app_client, device, token, account_id, ingredients_text="oats, jaggery, salt")
    first, second = await _snapshots()
    assert second.previous_snapshot_id == first.id
    return account_id, first, second


def _label_cursor(baseline=1, notified=()):
    return product_watch.WatchCursor(label_baseline_version=baseline, label_notified_versions=tuple(notified))


_NO_RECORDS = product_watch.OfficialState(records=(), heads={})


@pytest.mark.asyncio
async def test_m_an_unavailable_label_comparison_produces_no_notice(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    """12A knows the ingredients moved. Its evidence gate says no, so Product Watch says nothing."""
    _account_id, first, second = await _two_label_versions(app_client, registered_supabase_user)

    notices = product_watch.notices_for(
        barcode=BARCODE, cursor=_label_cursor(), official=_NO_RECORDS, label_pair=(second, first),
    )

    assert notices == []
    assert second.changed_fields, "the internal truth exists; only publication is withheld"


@pytest.mark.asyncio
async def test_n_a_publishable_label_change_is_consumed_without_bypassing_its_authority(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,  # noqa: F811
):
    _account_id, first, second = await _two_label_versions(app_client, registered_supabase_user)
    _force_label_publication(monkeypatch)

    notices = product_watch.notices_for(
        barcode=BARCODE, cursor=_label_cursor(), official=_NO_RECORDS, label_pair=(second, first),
    )
    already_known = product_watch.notices_for(
        barcode=BARCODE, cursor=_label_cursor(baseline=2), official=_NO_RECORDS,
        label_pair=(second, first),
    )
    already_told = product_watch.notices_for(
        barcode=BARCODE, cursor=_label_cursor(notified=(2,)), official=_NO_RECORDS,
        label_pair=(second, first),
    )

    assert [(n.kind, n.label_version) for n in notices] == [(product_watch.KIND_LABEL_CHANGE, 2)]
    assert notices[0].notification_key.startswith("pw:c:")
    assert already_known == []
    assert already_told == []


@pytest.mark.asyncio
async def test_n_a_broken_label_chain_with_evidence_forced_open_produces_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch, caplog,  # noqa: F811
):
    _account_id, first, second = await _two_label_versions(app_client, registered_supabase_user)
    _force_label_publication(monkeypatch)
    # A predecessor that is not the one the row names.
    forged_previous = replace_snapshot(first, version_number=7)

    with caplog.at_level(logging.WARNING):
        notices = product_watch.notices_for(
            barcode=BARCODE, cursor=_label_cursor(), official=_NO_RECORDS,
            label_pair=(second, forged_previous),
        )

    assert notices == []
    assert "product_watch_label_history_invalid" in caplog.text


def replace_snapshot(snapshot: LabelSnapshot, **changes) -> LabelSnapshot:
    """A detached copy with some fields changed — never written anywhere."""
    columns = {c.name: getattr(snapshot, c.name) for c in LabelSnapshot.__table__.columns}
    return LabelSnapshot(**{**columns, **changes})


@pytest.mark.asyncio
async def test_o_a_first_label_observation_is_never_a_change(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,  # noqa: F811
):
    _account_id, first, _second = await _two_label_versions(app_client, registered_supabase_user)
    _force_label_publication(monkeypatch)

    # Baseline 0, so the version check cannot be what refuses it: only the
    # first-observation status does.
    notices = product_watch.notices_for(
        barcode=BARCODE, cursor=_label_cursor(baseline=0), official=_NO_RECORDS,
        label_pair=(first, None),
    )
    assert notices == []


@pytest.mark.asyncio
async def test_o_the_production_worker_supplies_no_label_pair_so_formula_notices_stay_dormant(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,  # noqa: F811
):
    """Even with 12A's gate forced open, the background path picks no label for the customer."""
    account_id, _first, _second = await _two_label_versions(app_client, registered_supabase_user)
    _force_label_publication(monkeypatch)

    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == []


# ---------------------------------------------------------------------------
# P. A first regulatory observation is never a regulatory change
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_p_a_first_observed_record_is_a_match_notice_never_a_change_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    _force_regulatory_publication(monkeypatch)
    await _ingest(tmp_path)

    delivery = await _decide(account_id)

    assert delivery["notification_key"].startswith("pw:a:")
    assert delivery["title"] == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1)) is None


def test_p_only_a_published_changed_status_names_a_revision():
    assert product_watch._published_revision({"status": "changed", "current_revision": 3}) == 3
    for withheld in (
        {"status": "first_observed_record", "current_revision": 1},
        {"status": "unavailable", "current_revision": None},
        {"status": "changed", "current_revision": None},
        {"status": "changed", "current_revision": True},
        {"status": "changed", "current_revision": "3"},
        None,
        "changed",
    ):
        assert product_watch._published_revision(withheld) is None, withheld


# ---------------------------------------------------------------------------
# Q. A stopped watch is not evaluated
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_q_a_stopped_watch_produces_nothing(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    stopped = await _delete_watch(app_client, token)
    again = await _delete_watch(app_client, token)
    await _ingest(tmp_path)

    assert stopped.status_code == again.status_code == 200
    assert stopped.json()["watching"] is False
    assert await _decide(account_id) is None
    row = await _watch_row(account_id)
    assert row.active is False and row.stopped_at is not None


# ---------------------------------------------------------------------------
# R. Re-anchoring resets the baseline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_r_watching_a_different_pack_re_anchors_without_replaying_history(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id, batch_number="A-001")
    first_row = await _watch_row(account_id)
    # A record for lot B-123 exists; the customer is now holding a B-123 pack.
    await _ingest(tmp_path)
    await _confirm(app_client, device, token, account_id)
    state = await _read_watch(app_client, token, device)
    assert state.json()["watching"] is True
    assert state.json()["watching_this_pack"] is False

    response = await _put_watch(app_client, device, token, label_version=state.json()["anchorable_label_version"])

    assert response.status_code == 200
    assert response.json()["watching_this_pack"] is True
    row = await _watch_row(account_id)
    assert row.id == first_row.id, "one logical watch per product"
    assert row.anchor_scan_event_id != first_row.anchor_scan_event_id
    assert row.started_at > first_row.started_at
    assert RECALL_ID in _cursor(row).records
    assert await _decide(account_id) is None, "a re-anchor replayed a fact that was already true"


@pytest.mark.asyncio
async def test_r_watching_again_after_stopping_rebuilds_the_baseline(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _delete_watch(app_client, token)
    await _ingest(tmp_path)
    await _put_watch(app_client, device, token)

    row = await _watch_row(account_id)
    assert row.active is True and row.stopped_at is None
    assert await _decide(account_id) is None


# ---------------------------------------------------------------------------
# S / T. An explicit opt-out is the customer's decision
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_s_the_product_watch_topic_off_means_no_push_and_no_backlog(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    off = await app_client.patch(
        "/api/v2/today/notifications", headers=auth(token), json={"topics": {"product_watch": False}},
    )
    assert off.status_code == 200
    assert off.json()["preferences"]["topics"]["product_watch"] is False
    await _ingest(tmp_path)

    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == [], "an opted-out topic still wrote a decision"
    # Back on: what became true while the customer asked not to hear is baseline.
    on = await app_client.patch(
        "/api/v2/today/notifications", headers=auth(token), json={"topics": {"product_watch": True}},
    )
    assert on.status_code == 200
    assert RECALL_ID in _cursor(await _watch_row(account_id)).records
    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1)) is None


@pytest.mark.asyncio
async def test_s_other_topics_off_leave_product_watch_on(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """``product_watch`` is its own topic, never an alias of an existing one."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await app_client.patch("/api/v2/today/notifications", headers=auth(token), json={"topics": {
        "today_style": False, "care": False, "event_preparation": False, "maintenance": False,
    }})
    await _ingest(tmp_path)

    delivery = await _decide(account_id)
    assert delivery is not None and delivery["status"] == notifications.STATUS_QUEUED


@pytest.mark.asyncio
async def test_t_master_notifications_off_means_no_push_and_no_backlog(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await app_client.patch("/api/v2/today/notifications", headers=auth(token), json={"enabled": False})
    await _ingest(tmp_path)

    assert await _decide(account_id) is None
    assert await _deliveries(account_id) == []
    await app_client.patch("/api/v2/today/notifications", headers=auth(token), json={"enabled": True})
    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1)) is None


@pytest.mark.asyncio
async def test_s_t_an_unrelated_preference_change_does_not_rebuild_the_baseline(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Only an off→on transition rebuilds the baseline; an unrelated PATCH changes nothing."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    await app_client.patch("/api/v2/today/notifications", headers=auth(token), json={"daily_cap": 2})

    delivery = await _decide(account_id)
    assert delivery is not None and delivery["status"] == notifications.STATUS_QUEUED


def test_s_t_an_explicit_opt_out_consumes_the_event_and_a_temporary_limit_does_not():
    class Decision:
        def __init__(self, status, reason=None):
            self.status, self.suppressed_reason = status, reason

    consumes = product_watch._consumes_event
    assert consumes(Decision(notifications.STATUS_QUEUED))
    assert consumes(Decision(notifications.STATUS_SENDING))
    assert consumes(Decision(notifications.STATUS_PROVIDER_ACCEPTED))
    assert consumes(Decision(notifications.STATUS_PROVIDER_FAILED))
    assert consumes(Decision(notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_DISABLED))
    assert consumes(Decision(notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_MODULE_OFF))
    assert not consumes(Decision(notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_QUIET))
    assert not consumes(Decision(notifications.STATUS_SUPPRESSED, notifications.SUPPRESSED_CAP))


# ---------------------------------------------------------------------------
# U. Native push is a separate consent, and watching never asks for it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_u_watching_without_native_push_reaches_no_device(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    state = await _watching(app_client, device, token, account_id)
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    await _ingest(tmp_path)

    await worker.process_once(now=_at_local_hour(7))

    assert sender.messages_sent == 0
    assert state["delivery"] == {
        "notifications_enabled": True, "product_watch_enabled": True, "native_push_enabled": False,
    }
    async with get_sessionmaker()() as session:
        preference = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_id,
        ))).scalar_one_or_none()
    # Watching wrote no delivery preference and enabled no native push.
    assert preference is None
    row = await _watch_row(account_id)
    assert row.active is True


# ---------------------------------------------------------------------------
# V / W. Temporary limits keep the event eligible, without infinite rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_v_quiet_hours_hold_the_event_for_a_later_day(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    await _set_preferences(account_id, quiet_hours_start=21, quiet_hours_end=7)
    before = (await _watch_row(account_id)).notice_cursor

    quiet = await _decide(account_id, hour=22)
    quiet_again = await _decide(account_id, hour=23)

    assert quiet["status"] == notifications.STATUS_SUPPRESSED
    assert quiet["suppressed_reason"] == notifications.SUPPRESSED_QUIET
    assert quiet_again["id"] == quiet["id"], "one decision row per event per day"
    assert (await _watch_row(account_id)).notice_cursor == before, "a temporary limit consumed the event"

    later = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1), hour=9)
    assert later["status"] == notifications.STATUS_QUEUED
    assert await _decide(account_id, plan_date=DAY_ONE + timedelta(days=2), hour=9) is None
    assert len(await _watch_deliveries(account_id)) == 2


@pytest.mark.asyncio
async def test_w_the_daily_cap_holds_the_event_for_a_later_day(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    async with get_sessionmaker()() as session:
        ordinary = await notifications.queue(
            session, account_id=account_id, plan_date=DAY_ONE, notification_key="ordinary",
            title="Today", module="outfit", topic="today_style", timezone_name=TZ,
            moment=_moment(DAY_ONE, 8),
        )
        assert ordinary.status == notifications.STATUS_QUEUED
        await session.commit()
    before = (await _watch_row(account_id)).notice_cursor

    capped = await _decide(account_id)

    assert capped["status"] == notifications.STATUS_SUPPRESSED
    assert capped["suppressed_reason"] == notifications.SUPPRESSED_CAP
    assert (await _watch_row(account_id)).notice_cursor == before
    assert (await _decide(account_id))["id"] == capped["id"]
    next_day = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1))
    assert next_day["status"] == notifications.STATUS_QUEUED
    assert next_day["notification_key"] == capped["notification_key"], "the same event, a later day"


# ---------------------------------------------------------------------------
# X / Y. One slot, one deterministic winner
# ---------------------------------------------------------------------------
def _ordinary_reminder(calls: list):
    async def queue_for_agenda(session, *, account_id, plan_date, timezone_name, moment=None):
        calls.append(account_id)
        return await notifications.queue(
            session, account_id=account_id, plan_date=plan_date, notification_key="agenda:ordinary",
            title="Your look for today", module="outfit", topic="today_style",
            timezone_name=timezone_name, moment=moment, scheduled_for=moment,
        )
    return queue_for_agenda


@pytest.mark.asyncio
async def test_x_a_material_notice_takes_the_day_s_single_slot(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    calls: list = []
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder(calls))
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    await _ingest(tmp_path)

    sent = await worker.process_once(now=_at_local_hour(9))

    assert sent == 1
    assert sender.messages_sent == 1
    message = sender.batches[0][0]
    assert message.title == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    assert message.data["destination"] == "/verdict"
    assert message.data["barcode"] == BARCODE
    assert calls == [], "the ordinary reminder was evaluated after the slot was taken"


@pytest.mark.asyncio
async def test_x_without_a_material_notice_the_ordinary_reminder_keeps_its_slot(
    db_clean, off_clean, app_client, registered_supabase_user, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    calls: list = []
    monkeypatch.setattr(notifications, "queue_for_agenda", _ordinary_reminder(calls))
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)

    assert await worker.process_once(now=_at_local_hour(9)) == 1
    assert sender.batches[0][0].title == "Your look for today"
    assert calls == [account_id]


async def _two_watched_products(app_client, registered_supabase_user):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _watching(app_client, device, token, account_id, OTHER_BARCODE, batch_number=OTHER_BATCH)
    return account_id


@pytest.mark.asyncio
async def test_y_several_material_notices_give_exactly_one_deterministic_winner_per_cycle(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Observation order decides before barcode order: the earlier-dated record goes first."""
    account_id = await _two_watched_products(app_client, registered_supabase_user)
    # BARCODE sorts first, but OTHER_BARCODE's record carries the earlier date.
    await _ingest_rows(tmp_path, [
        _row(recall_id=RECALL_ID, start="05-08-2026"),
        _row(recall_id=OTHER_RECALL_ID, batch=OTHER_BATCH, start="01-08-2026"),
    ])

    first = await _decide(account_id)
    second = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1))
    third = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=2))

    assert first["destination_params"] == {"barcode": OTHER_BARCODE}
    assert second["destination_params"] == {"barcode": BARCODE}
    assert third is None
    per_day = {}
    for row in await _watch_deliveries(account_id):
        per_day.setdefault(row.plan_date, []).append(row)
    assert all(len(rows) == 1 for rows in per_day.values())


@pytest.mark.asyncio
async def test_y_the_worker_sends_one_push_for_several_waiting_notices(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    account_id = await _two_watched_products(app_client, registered_supabase_user)
    await _opt_in(account_id, hour=9)
    await _set_preferences(account_id, daily_cap=5)
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    await _ingest_rows(tmp_path, [
        _row(recall_id=RECALL_ID), _row(recall_id=OTHER_RECALL_ID, batch=OTHER_BATCH),
    ])

    assert await worker.process_once(now=_at_local_hour(9)) == 1
    assert sender.messages_sent == 1
    assert len(await _watch_deliveries(account_id)) == 1


def test_y_class_priority_is_fixed_and_is_not_a_severity_score():
    """Record match, then regulatory change, then label change — whatever the barcode or date."""
    label = product_watch.WatchNotice(
        kind=product_watch.KIND_LABEL_CHANGE, barcode="00000001", label_version=2,
        observation_key="0000000002",
    )
    regulatory = product_watch.WatchNotice(
        kind=product_watch.KIND_REGULATORY_CHANGE, barcode="00000002", recall_id="1", revision=2,
        observation_key="2000-01-01",
    )
    match = product_watch.WatchNotice(
        kind=product_watch.KIND_RECORD_MATCH, barcode="99999999", recall_id="2",
        observation_key="2099-12-31",
    )
    ordered = sorted([label, regulatory, match], key=lambda n: n.sort_key)
    assert [n.kind for n in ordered] == [
        product_watch.KIND_RECORD_MATCH, product_watch.KIND_REGULATORY_CHANGE, product_watch.KIND_LABEL_CHANGE,
    ]


@pytest.mark.asyncio
async def test_y_a_record_match_outranks_a_regulatory_change_in_the_same_cycle(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _ingest(tmp_path, status="Initiated")
    await _watching(app_client, device, token, account_id)
    await _watching(app_client, device, token, account_id, OTHER_BARCODE, batch_number=OTHER_BATCH)
    _force_regulatory_publication(monkeypatch)
    # One export: a revision for the first product's known record, and a new
    # record for the second product.
    await _ingest_rows(tmp_path, [
        _row(recall_id=RECALL_ID, status="Completed"),
        _row(recall_id=OTHER_RECALL_ID, batch=OTHER_BATCH),
    ], checked_at=LATER)

    first = await _decide(account_id)
    second = await _decide(account_id, plan_date=DAY_ONE + timedelta(days=1))

    assert first["notification_key"].startswith("pw:a:")
    assert first["destination_params"] == {"barcode": OTHER_BARCODE}
    assert second["notification_key"].startswith("pw:b:")
    assert second["destination_params"] == {"barcode": BARCODE}


# ---------------------------------------------------------------------------
# Z / AA. Replays and overlapping cycles never duplicate a notice
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_z_replaying_the_worker_sends_nothing_twice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    await _ingest(tmp_path)
    moment = _at_local_hour(9)

    assert await worker.process_once(now=moment) == 1
    assert await worker.process_once(now=moment) == 0
    await worker.process_once(now=moment + timedelta(days=1))
    watch_messages = [
        m for batch in sender.batches for m in batch
        if m.title == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    ]
    assert len(watch_messages) == 1
    assert len(await _watch_deliveries(account_id)) == 1


@pytest.mark.asyncio
async def test_aa_two_overlapping_decisions_produce_one_delivery(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Two sessions deciding at once: the preference lock serialises them."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    # The worker only ever visits an account that has a preference row.
    await _set_preferences(account_id, enabled=True)
    factory = get_sessionmaker()
    moment = _moment(DAY_ONE, 9)

    async def decide():
        async with factory() as session:
            delivery = await product_watch.queue_material_notice(
                session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ, moment=moment,
            )
            await asyncio.sleep(0.2)
            await session.commit()
            return delivery

    results = await asyncio.gather(decide(), decide())

    assert sum(result is not None for result in results) == 1
    assert len(await _watch_deliveries(account_id)) == 1


@pytest.mark.asyncio
async def test_aa_two_overlapping_worker_cycles_send_one_push(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _opt_in(account_id, hour=9)
    sender = _CountingPush()
    monkeypatch.setattr(push, "send", sender.send)
    monkeypatch.setattr(worker.push, "send", sender.send)
    await _ingest(tmp_path)
    moment = _at_local_hour(9)

    await asyncio.gather(worker.process_once(now=moment), worker.process_once(now=moment))

    watch_messages = [
        m for batch in sender.batches for m in batch
        if m.title == notification_strings.PRODUCT_WATCH_RECORD_MATCH_TITLE
    ]
    assert len(watch_messages) == 1
    assert len(await _watch_deliveries(account_id)) == 1


# ---------------------------------------------------------------------------
# AB. Stopping a watch while a cycle is deciding
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ab_a_stop_waits_for_a_decision_already_in_progress(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """The watch rows stay locked until the decision is durable.

    A stop that arrives mid-evaluation cannot commit first, so no notice is
    ever committed for a watch whose stop had already been committed.
    """
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    evaluating, release = asyncio.Event(), asyncio.Event()
    real_official_state = product_watch.official_state

    async def paused_official_state(session, facts):
        evaluating.set()
        await release.wait()
        return await real_official_state(session, facts)

    monkeypatch.setattr(product_watch, "official_state", paused_official_state)
    order: list[str] = []

    async def cycle():
        async with get_sessionmaker()() as session:
            delivery = await product_watch.queue_material_notice(
                session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ,
                moment=_moment(DAY_ONE, 9),
            )
            await session.commit()
            order.append("decision_committed")
            return delivery

    async def stop():
        await evaluating.wait()
        response = await _delete_watch(app_client, token)
        order.append("stop_committed")
        return response

    cycle_task = asyncio.create_task(cycle())
    stop_task = asyncio.create_task(stop())
    await evaluating.wait()
    await asyncio.sleep(0.5)
    assert not stop_task.done(), "a stop committed while the decision was still being made"
    release.set()
    delivery, response = await asyncio.gather(cycle_task, stop_task)

    assert response.status_code == 200
    assert order == ["decision_committed", "stop_committed"]
    assert delivery is not None
    row = await _watch_row(account_id)
    assert row.active is False
    assert len(await _watch_deliveries(account_id)) == 1


@pytest.mark.asyncio
async def test_ab_a_stop_committed_first_means_no_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    async with get_sessionmaker()() as session:
        # A worker session that already read the watch before the stop.
        stale = (await session.execute(select(ProductWatch))).scalar_one()
        assert stale.active is True
        await session.commit()
        await _delete_watch(app_client, token)
        delivery = await product_watch.queue_material_notice(
            session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ,
            moment=_moment(DAY_ONE, 9),
        )
        await session.commit()

    assert delivery is None
    assert await _deliveries(account_id) == []


@pytest.mark.asyncio
async def test_ab_an_opt_out_committed_after_the_worker_read_the_preference_still_wins(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """The worker reads the preference before it locks it; the locked value decides."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    await _set_preferences(account_id, enabled=True)
    async with get_sessionmaker()() as session:
        stale = (await session.execute(select(NotificationPreference).where(
            NotificationPreference.account_id == account_id,
        ))).scalar_one()
        assert stale.topics.get("product_watch", True) is True
        await session.commit()
        off = await app_client.patch(
            "/api/v2/today/notifications", headers=auth(token), json={"topics": {"product_watch": False}},
        )
        assert off.status_code == 200
        delivery = await product_watch.queue_material_notice(
            session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ,
            moment=_moment(DAY_ONE, 9),
        )
        await session.commit()

    assert delivery is None
    assert await _deliveries(account_id) == []


@pytest.mark.asyncio
async def test_ab_a_rolled_back_decision_leaves_the_cursor_untouched(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    """The cursor moves in the outbox row's transaction, so both are lost together."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    before = (await _watch_row(account_id)).notice_cursor
    async with get_sessionmaker()() as session:
        delivery = await product_watch.queue_material_notice(
            session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ,
            moment=_moment(DAY_ONE, 9),
        )
        assert delivery is not None
        await session.rollback()

    assert (await _watch_row(account_id)).notice_cursor == before
    assert await _deliveries(account_id) == []
    assert await _decide(account_id) is not None


@pytest.mark.asyncio
async def test_ab_a_watch_created_while_a_cycle_is_deciding_starts_from_its_own_baseline(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path, recall_id=OTHER_RECALL_ID, batch=OTHER_BATCH)
    evaluating, release = asyncio.Event(), asyncio.Event()
    real_official_state = product_watch.official_state

    async def paused_official_state(session, facts):
        evaluating.set()
        await release.wait()
        return await real_official_state(session, facts)

    monkeypatch.setattr(product_watch, "official_state", paused_official_state)

    async def cycle():
        async with get_sessionmaker()() as session:
            delivery = await product_watch.queue_material_notice(
                session, account_id=account_id, plan_date=DAY_ONE, timezone_name=TZ,
                moment=_moment(DAY_ONE, 9),
            )
            await session.commit()
            return delivery

    cycle_task = asyncio.create_task(cycle())
    await evaluating.wait()
    monkeypatch.setattr(product_watch, "official_state", real_official_state)
    created = await _watching(app_client, device, token, account_id, OTHER_BARCODE, batch_number=OTHER_BATCH)
    release.set()

    assert created["watching"] is True
    assert await cycle_task is None
    assert await _decide(account_id) is None, "a record already true when the watch began was replayed"


# ---------------------------------------------------------------------------
# AC. Privacy export
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ac_the_export_carries_safe_watch_state_and_no_internal_ids(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    await _decide(account_id)
    row = await _watch_row(account_id)
    async with get_sessionmaker()() as session:
        payload = await export_service.build_export(session, account_id)

    [exported] = payload["domains"]["product_scans"]["product_watches"]
    assert set(exported) == {
        "barcode", "watching", "label_version", "started_at", "stopped_at",
        "last_notified_at", "created_at", "updated_at",
    }
    assert exported["barcode"] == BARCODE
    assert exported["watching"] is True
    assert exported["label_version"] == 1
    assert exported["last_notified_at"] is not None
    rendered = json.dumps(exported)
    for internal in (
        str(row.id), str(row.anchor_scan_event_id), str(row.anchor_label_snapshot_id),
        str(account_id), "baseline_unknown", "notified_versions", "revisions", "cursor",
    ):
        assert internal not in rendered
    assert REGISTRY["product_watches"] == Classification.INCLUDED
    json.dumps(payload)


# ---------------------------------------------------------------------------
# AD. Account deletion removes every watch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ad_account_deletion_removes_the_watch_rows(
    db_clean, off_clean, app_client, registered_supabase_user, fake_admin, fake_storage,  # noqa: F811
):
    from app.domains.privacy import deletion_service

    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _watching(app_client, device, token, account_id, OTHER_BARCODE, batch_number=OTHER_BATCH)
    other_token, other_id, other_device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, other_device, other_token, other_id)
    assert len(await _watch_rows()) == 3

    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    remaining = await _watch_rows()
    assert [row.account_id for row in remaining] == [other_id], "a deleted account's watch survived"


@pytest.mark.asyncio
async def test_ad_the_database_itself_cascades_and_restricts_as_documented(db_clean):
    async with get_sessionmaker()() as session:
        rules = dict((await session.execute(text("""
            SELECT a.attname::text, c.confdeltype::text
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = 'product_watches'::regclass AND c.contype = 'f'
        """))).all())
    assert rules == {
        "account_id": "c",  # CASCADE: the watch leaves with the account
        "anchor_scan_event_id": "c",  # CASCADE: no capture, no anchor
        "anchor_label_snapshot_id": "r",  # RESTRICT: shared product truth is never deleted under a watch
    }


# ---------------------------------------------------------------------------
# AE. The deep link carries a validated barcode to /verdict, and nothing else
# ---------------------------------------------------------------------------
def test_ae_the_verdict_destination_accepts_only_a_validated_barcode():
    target = notifications._target
    assert target("/verdict", {"barcode": BARCODE}) == ("/verdict", {"barcode": BARCODE})
    assert target("/verdict", {"barcode": "12345678"}) == ("/verdict", {"barcode": "12345678"})
    assert target("/verdict", {"barcode": "12345678901234"}) == ("/verdict", {"barcode": "12345678901234"})
    # Everything else rides along never.
    assert target(
        "/verdict", {"barcode": BARCODE, "source_url": "https://evil.example", "snapshot_id": "x"},
    ) == ("/verdict", {"barcode": BARCODE})
    for bad in (
        None, "", "1234567", "123456789012345", "ABCDEFGH", f"{BARCODE}\n", f"{BARCODE}/../x",
        f"{BARCODE}?x=1", " 8901058000191", 8901058000191, ["8901058000191"], "٨٩٠١٠٥٨٠٠٠١٩١",
    ):
        assert target("/verdict", {"barcode": bad}) == (None, {}), bad
    assert target("/verdict", None) == (None, {})
    for destination in ("/verdict/8901058000191", "/verdict?barcode=8901058000191", "https://x", "/admin"):
        assert target(destination, {"barcode": BARCODE}) == (None, {}), destination


@pytest.mark.asyncio
async def test_ae_the_stored_delivery_carries_only_the_validated_destination(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    async with get_sessionmaker()() as session:
        good = await notifications.queue(
            session, account_id=account_id, plan_date=DAY_ONE, notification_key="pw:a:test",
            title="t", module="product_watch", topic="product_watch", timezone_name=TZ,
            moment=_moment(DAY_ONE, 9), deep_link="/verdict",
            destination_params={"barcode": BARCODE, "account_id": str(account_id)},
        )
        bad = await notifications.queue(
            session, account_id=account_id, plan_date=DAY_ONE + timedelta(days=1),
            notification_key="pw:a:test2", title="t", module="product_watch", topic="product_watch",
            timezone_name=TZ, moment=_moment(DAY_ONE + timedelta(days=1), 9), deep_link="/verdict",
            destination_params={"barcode": "../../admin"},
        )
        await session.commit()
    assert (good.deep_link, good.destination_params) == ("/verdict", {"barcode": BARCODE})
    assert (bad.deep_link, bad.destination_params) == (None, {})


def test_ae_the_topic_is_typed_and_unknown_topics_stay_closed():
    assert "product_watch" in notifications.NOTIFICATION_TOPICS
    assert notifications.DEFAULT_TOPIC_NOTIFICATIONS["product_watch"] is True
    assert product_watch.DESTINATION == "/verdict"


# ---------------------------------------------------------------------------
# AF. Customer copy states a current fact and interprets nothing
# ---------------------------------------------------------------------------
def _watch_copy() -> dict[str, str]:
    return {
        name: value for name, value in vars(notification_strings).items()
        if name.startswith("PRODUCT_WATCH_") and isinstance(value, str)
    }


def test_af_every_product_watch_string_is_keyed_and_free_of_interpretation():
    copy = _watch_copy()
    assert len(copy) == 6
    for name, value in copy.items():
        lowered = value.lower()
        for forbidden in FORBIDDEN_COPY:
            assert forbidden not in lowered, f"{name} says {forbidden!r}"
        for term in DIAGNOSTIC_TERMS:
            assert term.lower() not in lowered, f"{name} uses diagnostic term {term!r}"
    # Every notice class draws its copy from the keyed registry.
    for kind in product_watch.NOTICE_KINDS:
        title, body = product_watch._COPY[kind]
        assert title in copy.values() and body in copy.values()


def test_af_the_watch_module_composes_no_customer_sentence_of_its_own():
    """Copy lives in the string registry. The authority builds no prose."""
    source = inspect.getsource(product_watch)
    tree = ast.parse(source)
    docstrings = {
        node.body[0].value for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    allowed_prose = {
        "This product cannot be watched.",
        "Confirm this pack's label on this device to watch it.",
        "This pack's details changed. Refresh and try again.",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings:
            value = node.value
            if " " in value and not value.startswith("product_watch_") and "%s" not in value:
                assert value in allowed_prose, f"unexpected prose in the watch authority: {value!r}"
    for message in allowed_prose:
        for forbidden in FORBIDDEN_COPY:
            assert forbidden not in message.lower()


# ---------------------------------------------------------------------------
# Public state and schema
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_public_state_exposes_no_internal_identifier(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    body = await _watching(app_client, device, token, account_id)
    read = (await _read_watch(app_client, token, device)).json()
    stopped = (await _delete_watch(app_client, token)).json()
    row = await _watch_row(account_id)
    snapshot = (await _snapshots())[0]

    for state in (body, read, stopped):
        assert set(state) == {
            "contract_version", "barcode", "watching", "label_version", "started_at", "watchable",
            "anchorable_label_version", "watching_this_pack", "reason", "delivery",
        }
        rendered = json.dumps(state)
        for internal in (
            str(row.id), str(row.anchor_scan_event_id), str(row.anchor_label_snapshot_id),
            str(account_id), snapshot.content_fingerprint, "notice_cursor", "records",
        ):
            assert internal not in rendered


@pytest.mark.asyncio
async def test_the_request_contract_is_the_label_version_and_nothing_else(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _confirm(app_client, device, token, account_id)
    snapshot = (await _snapshots())[0]
    for body in (
        {"label_version": 1, "label_snapshot_id": str(snapshot.id)},
        {"label_version": 0},
        {"label_version": "1x"},
        {},
    ):
        response = await app_client.put(
            f"/api/v2/scan/verdict/{BARCODE}/watch", headers={**device, **auth(token)}, json=body,
        )
        assert response.status_code == 422, body
    assert await _watch_rows() == []


@pytest.mark.asyncio
async def test_the_table_allows_one_watch_per_account_and_product(
    db_clean, off_clean, app_client, registered_supabase_user,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    row = await _watch_row(account_id)
    async with get_sessionmaker()() as session:
        session.add(ProductWatch(
            account_id=account_id, barcode=BARCODE, anchor_scan_event_id=row.anchor_scan_event_id,
            anchor_label_snapshot_id=row.anchor_label_snapshot_id, anchor_label_version=1,
            active=True, started_at=row.started_at, notice_cursor={},
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.parametrize("values", [
    {"active": True, "stopped_at": "now()"},
    {"active": False, "stopped_at": None},
    {"anchor_label_version": 0},
])
@pytest.mark.asyncio
async def test_the_table_refuses_an_incoherent_watch(
    db_clean, off_clean, app_client, registered_supabase_user, values,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    assignments = ", ".join(
        f"{name} = {'NULL' if value is None else value}" for name, value in values.items()
    )
    async with get_sessionmaker()() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text(f"UPDATE product_watches SET {assignments}"))
            await session.commit()


@pytest.mark.asyncio
async def test_a_malformed_cursor_is_skipped_not_repaired(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, caplog,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE product_watches SET notice_cursor = '{}'::jsonb"))
        await session.commit()

    with caplog.at_level(logging.WARNING):
        assert await _decide(account_id) is None
    assert "product_watch_cursor_invalid" in caplog.text
    assert (await _watch_row(account_id)).notice_cursor == {}


@pytest.mark.asyncio
async def test_reenable_never_repairs_a_malformed_cursor(
    db_clean, off_clean, app_client, registered_supabase_user, caplog,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE product_watches SET notice_cursor = '{}'::jsonb"))
        await session.commit()

    off = await app_client.patch(
        "/api/v2/today/notifications", headers=auth(token), json={"topics": {"product_watch": False}},
    )
    assert off.status_code == 200
    with caplog.at_level(logging.WARNING):
        on = await app_client.patch(
            "/api/v2/today/notifications", headers=auth(token), json={"topics": {"product_watch": True}},
        )
    assert on.status_code == 200
    assert (await _watch_row(account_id)).notice_cursor == {}
    assert "product_watch_cursor_invalid" in caplog.text


@pytest.mark.asyncio
async def test_a_new_exact_record_with_corrupt_ledger_is_not_proactively_notified(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, caplog,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    async with get_sessionmaker()() as session:
        await session.execute(update(OfficialRecordRevision).values(content_hash="f" * 64))
        await session.commit()
    async with get_sessionmaker()() as session:
        assert (await official_records.validated_revision_heads(session, [RECALL_ID]))[RECALL_ID] is None

    with caplog.at_level(logging.WARNING):
        assert await _decide(account_id) is None
    assert "regulatory_history_invariant_failed" in caplog.text
    assert await _watch_deliveries(account_id) == []
    assert RECALL_ID not in _cursor(await _watch_row(account_id)).records


@pytest.mark.parametrize("raw", [
    {},
    {"v": 2, "records": {}, "label": {"baseline_version": 1, "notified_versions": []}},
    {"v": 1, "records": [], "label": {"baseline_version": 1, "notified_versions": []}},
    {"v": 1, "records": {"901": {"revisions": [0], "baseline_unknown": False}},
     "label": {"baseline_version": 1, "notified_versions": []}},
    {"v": 1, "records": {"901": {"revisions": [1], "baseline_unknown": "no"}},
     "label": {"baseline_version": 1, "notified_versions": []}},
    {"v": 1, "records": {}, "label": {"baseline_version": True, "notified_versions": []}},
    {"v": 1, "records": {}, "label": {"baseline_version": 1, "notified_versions": [], "x": 1}},
    {"v": 1, "records": {"901": {"revisions": [1]}}, "label": {"baseline_version": 1, "notified_versions": []}},
])
def test_the_cursor_accepts_only_its_own_shape(raw):
    with pytest.raises(product_watch.WatchCursorInvalid):
        product_watch.WatchCursor.from_json(raw)


def test_the_cursor_round_trips_deterministically():
    cursor = product_watch.WatchCursor(
        records={
            "902": product_watch.RecordBaseline(revisions=(2, 1), baseline_unknown=False),
            "901": product_watch.RecordBaseline(revisions=(), baseline_unknown=True),
        },
        label_baseline_version=3, label_notified_versions=(5, 4, 5),
    )
    raw = cursor.to_json()
    assert list(raw["records"]) == ["901", "902"]
    assert raw["records"]["902"]["revisions"] == [1, 2]
    assert raw["label"]["notified_versions"] == [4, 5]
    assert product_watch.WatchCursor.from_json(raw).to_json() == raw


@pytest.mark.asyncio
async def test_an_anchor_capture_withdrawn_by_erasure_is_never_used(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path, caplog,  # noqa: F811
):
    """A capture whose facts are gone is no longer a capture; the watch goes quiet."""
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    row = await _watch_row(account_id)
    async with get_sessionmaker()() as session:
        await session.execute(
            update(ScanEvent).where(ScanEvent.id == row.anchor_scan_event_id).values(label_facts=None)
        )
        await session.commit()

    with caplog.at_level(logging.WARNING):
        assert await _decide(account_id) is None
    assert "product_watch_anchor_invalid reason=anchor_capture_invalid" in caplog.text


@pytest.mark.asyncio
async def test_only_an_openable_official_source_supports_a_current_state_notice(
    db_clean, off_clean, app_client, registered_supabase_user, tmp_path,  # noqa: F811
):
    token, account_id, device = await _customer(app_client, registered_supabase_user)
    await _watching(app_client, device, token, account_id)
    await _ingest(tmp_path)
    async with get_sessionmaker()() as session:
        await session.execute(text("UPDATE official_records SET source_url = 'http://foscos.fssai.gov.in/food-recall'"))
        await session.commit()

    assert await _decide(account_id) is None
    for url in (
        None, "", "http://foscos.fssai.gov.in/x", "https://example.org/x",
        "https://foscos.fssai.gov.in.evil/x", "https://foscos.fssai.gov.in/anything-else",
        "https://foscos.fssai.gov.in/food-recall/anything",
        "https://user@foscos.fssai.gov.in/food-recall",
        "https://foscos.fssai.gov.in:443/food-recall",
        "https://foscos.fssai.gov.in/food-recall?x=1",
        "https://foscos.fssai.gov.in/food-recall#x",
    ):
        assert product_watch.current_record_source_is_openable(url) is False, url
    assert product_watch.current_record_source_is_openable("https://foscos.fssai.gov.in/food-recall") is True


def test_step12c_adds_exactly_one_table_to_the_schema():
    from app.shared.database.registry import Base

    watch_tables = [name for name in Base.metadata.tables if "watch" in name]
    assert watch_tables == ["product_watches"]
    columns = set(Base.metadata.tables["product_watches"].c.keys())
    # Barcode and Store B references only: no Open Food Facts field is kept.
    for off_field in ("product_name", "brands", "brand", "ingredients_text", "nutriments", "image_url"):
        assert off_field not in columns


def _record_state(revision_status: str, current_revision, *, recall_id=RECALL_ID, match_state="matched"):
    return product_watch.OfficialState(
        records=({
            "recall_id": recall_id, "match_state": match_state,
            "source_url": "https://foscos.fssai.gov.in/food-recall", "recall_start_date": "2026-08-01",
            "regulatory_change": {"status": revision_status, "current_revision": current_revision},
        },),
        heads={recall_id: current_revision},
    )


def test_a_published_revision_no_newer_than_one_already_known_is_not_news():
    """Set membership and ordering both: an older or equal revision never notifies."""
    cursor = product_watch.WatchCursor(records={
        RECALL_ID: product_watch.RecordBaseline(revisions=(1, 3)),
    })
    for revision in (1, 2, 3):
        assert product_watch.notices_for(
            barcode=BARCODE, cursor=cursor, official=_record_state("changed", revision),
        ) == [], revision
    [notice] = product_watch.notices_for(
        barcode=BARCODE, cursor=cursor, official=_record_state("changed", 4),
    )
    assert (notice.kind, notice.revision) == (product_watch.KIND_REGULATORY_CHANGE, 4)


def test_a_matched_record_without_a_validated_head_is_never_a_notice():
    """Proactive publication refuses a corrupt official ledger, even if new."""
    state = _record_state("first_observed_record", 1)
    state = product_watch.OfficialState(records=state.records, heads={RECALL_ID: None})
    assert product_watch.notices_for(
        barcode=BARCODE, cursor=product_watch.WatchCursor(), official=state,
    ) == []


def test_rebaseline_cursor_is_monotonic_for_records_uncertainty_and_labels():
    existing = product_watch.WatchCursor(
        records={
            "old": product_watch.RecordBaseline(revisions=(1,), baseline_unknown=True),
            "kept": product_watch.RecordBaseline(revisions=(2,), baseline_unknown=False),
        },
        label_baseline_version=7,
        label_notified_versions=(3, 7),
    )
    fresh = product_watch.WatchCursor(
        records={
            "kept": product_watch.RecordBaseline(revisions=(4,), baseline_unknown=False),
            "new": product_watch.RecordBaseline(revisions=(5,), baseline_unknown=False),
        },
        label_baseline_version=4,
        label_notified_versions=(4,),
    )
    merged = product_watch.merge_rebaseline_cursor(existing, fresh)
    assert merged.records["old"] == product_watch.RecordBaseline((1,), True)
    assert merged.records["kept"] == product_watch.RecordBaseline((2, 4), False)
    assert merged.records["new"] == product_watch.RecordBaseline((5,), False)
    assert merged.label_baseline_version == 7
    assert merged.label_notified_versions == (3, 4, 7)


def test_rebaseline_keeps_record_match_identity_after_it_temporarily_disappears():
    existing = product_watch.WatchCursor(records={
        RECALL_ID: product_watch.RecordBaseline(revisions=(1,)),
    })
    fresh = product_watch.WatchCursor()
    cursor = product_watch.merge_rebaseline_cursor(existing, fresh)
    assert product_watch.notices_for(
        barcode=BARCODE, cursor=cursor,
        official=_record_state("first_observed_record", 1),
    ) == []


def test_a_record_that_is_not_an_exact_match_is_never_a_notice():
    cursor = product_watch.WatchCursor()
    for state in ("possible", "ambiguous", "conflict", None):
        assert product_watch.notices_for(
            barcode=BARCODE, cursor=cursor,
            official=_record_state("first_observed_record", 1, match_state=state),
        ) == [], state
