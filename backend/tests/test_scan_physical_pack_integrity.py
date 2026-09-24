"""Lane C: physical-pack authority is the current capture, not snapshot provenance.

A ``LabelSnapshot`` is semantic label content. Identical confirmed content is
deduplicated into one snapshot, and that row keeps the first capture that wrote
it as its ``scan_event_id``: historical provenance for the content. A
``ScanEvent`` is one device capturing one physical pack. Shelf ownership is
about the second, so it has to be proved from this device's newest capture —
and a snapshot's provenance event must never become the exclusive owner of
every later capture of the same label.

Every capture here goes through the confirmation route's own service calls, in
the route's order (``lock_label_version`` → ``record_scan`` →
``apply_confirmed_label`` → ``store_label_snapshot``), so snapshot reuse is the
real deduplication, not a fixture's imitation of it. Devices are registered and
claimed through the real API. Plain scans go through ``/scan/events``.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun, AIRunOutput
from app.domains.inventory.models import InventoryItem, InventoryProductLink
from app.domains.product import service as product_service
from app.domains.product.devices import _hash
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice, ScanEvent
from app.domains.product.service import label_content_fingerprint
from app.shared.database.sql import get_sessionmaker
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

BARCODE = "8904000000017"


def _facts(name: str = "Pack Cleanser", **extra) -> dict:
    """Confirmed Store-B facts for a shelf-eligible body product."""
    return {
        "product_category": "beauty", "product_name": name, "brand": "Pack Brand",
        "ingredients_text": "aqua, glycerin", **extra,
    }


async def _device(app_client: AsyncClient, token: str | None = None) -> dict[str, str]:
    """A phone registered through the API, and claimed by ``token``'s account if given."""
    registered = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert registered.status_code == 201, registered.text
    headers = {"X-Device-Token": registered.json()["token"]}
    if token is not None:
        claimed = await app_client.post("/api/v2/scan/device/claim", headers={**headers, **auth(token)})
        assert claimed.status_code == 200, claimed.text
    return headers


async def _device_row(headers: dict[str, str]) -> ScanDevice:
    async with get_sessionmaker()() as session:
        return (await session.execute(
            select(ScanDevice).where(ScanDevice.token_hash == _hash(headers["X-Device-Token"]))
        )).scalar_one()


async def _capture(
    headers: dict[str, str], account_id: uuid.UUID, facts: dict, *, barcode: str = BARCODE,
) -> tuple[ScanEvent, LabelSnapshot]:
    """Confirm a label exactly as the confirmation route does, and return the
    capture it recorded and the semantic snapshot that capture resolved to."""
    device = await _device_row(headers)
    async with get_sessionmaker()() as session:
        run = AIRun(
            account_id=account_id, feature="product_label_transcribe", provider="test", model="test-model",
            prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await product_service.lock_label_version(session, barcode)
        event, created = await product_service.record_scan(
            session, barcode=barcode, outcome=product_service.OUTCOME_LABEL,
            client_scan_id=uuid.uuid4().hex, device_id=device.id, account_id=account_id,
            label_facts=facts, ai_run_id=run.id,
        )
        assert created
        await product_service.apply_confirmed_label(session, barcode=barcode, facts=facts)
        snapshot = await product_service.store_label_snapshot(
            session, barcode=barcode, facts=facts, device_id=device.id, scan_event_id=event.id,
        )
        await session.commit()
        return event, snapshot


async def _plain_scan(app_client: AsyncClient, headers: dict[str, str], barcode: str = BARCODE) -> None:
    response = await app_client.post(
        "/api/v2/scan/events", headers=headers,
        json={"barcode": barcode, "client_scan_id": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text


def _identity(snapshot: LabelSnapshot) -> dict:
    return {
        "barcode": snapshot.barcode, "label_snapshot_id": str(snapshot.id),
        "label_version": snapshot.version_number, "content_fingerprint": snapshot.content_fingerprint,
    }


async def _add(app_client, token, headers, snapshot, key):
    return await app_client.post(
        "/api/v2/inventory/from-scan", headers={**headers, **auth(token)},
        json={**_identity(snapshot), "client_mutation_id": key},
    )


async def _status(app_client, token, headers, snapshot):
    identity = _identity(snapshot)
    return await app_client.get(
        f"/api/v2/inventory/from-scan/{identity['barcode']}/status", headers={**headers, **auth(token)},
        params={k: identity[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")},
    )


async def _links(account_id: uuid.UUID) -> list[InventoryProductLink]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(InventoryProductLink).where(InventoryProductLink.account_id == account_id)
        )).scalars().all())


async def _snapshot(snapshot_id: uuid.UUID) -> LabelSnapshot:
    async with get_sessionmaker()() as session:
        row = await session.get(LabelSnapshot, snapshot_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# A. The first capture, which wrote the snapshot, can add it
# ---------------------------------------------------------------------------


async def test_a_the_capture_that_created_the_snapshot_can_add_it_to_the_shelf(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    event, snapshot = await _capture(phone, account, _facts())
    assert snapshot.scan_event_id == event.id  # it wrote the row

    before = await _status(app_client, token, phone, snapshot)
    assert before.status_code == 200 and before.json()["status"] == "eligible_not_owned"
    added = await _add(app_client, token, phone, snapshot, "a-first-capture")
    assert added.status_code == 200, added.text
    assert added.json()["status"] == "owned"
    links = await _links(account)
    assert [(link.label_snapshot_id, link.label_version, link.content_fingerprint) for link in links] == [
        (snapshot.id, 1, label_content_fingerprint(_facts())),
    ]


# ---------------------------------------------------------------------------
# B. A second identical capture on the same phone reuses S1 and still owns
# ---------------------------------------------------------------------------


async def test_b_a_second_identical_capture_on_the_same_phone_can_add_and_read_status(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """The defect: E2 reused S1, S1 named E1, and ``E2 != E1`` refused E2 forever."""
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    first_event, s1 = await _capture(phone, account, _facts())
    second_event, reused = await _capture(phone, account, _facts())
    # Semantic content was deduplicated, correctly: one snapshot, provenance E1.
    assert reused.id == s1.id and second_event.id != first_event.id
    assert (await _snapshot(s1.id)).scan_event_id == first_event.id

    status = await _status(app_client, token, phone, s1)
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "eligible_not_owned"
    added = await _add(app_client, token, phone, s1, "b-second-capture")
    assert added.status_code == 200, added.text
    assert added.json()["status"] == "owned"
    owned = await _status(app_client, token, phone, s1)
    assert owned.json()["status"] == "owned"
    assert owned.json()["inventory_item_id"] == added.json()["inventory_item_id"]


# ---------------------------------------------------------------------------
# C. Another account's own phone, identical label: its own ownership
# ---------------------------------------------------------------------------


async def test_c_another_account_capturing_identical_content_owns_its_own_pack(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _device(app_client, token_a)
    phone_b = await _device(app_client, token_b)
    event_a, s1 = await _capture(phone_a, account_a, _facts())
    event_b, reused = await _capture(phone_b, account_b, _facts())
    assert reused.id == s1.id  # shared semantic snapshot, named for A's capture
    assert event_b.account_id == account_b and event_b.device_id != event_a.device_id

    added_b = await _add(app_client, token_b, phone_b, s1, "c-account-b")
    assert added_b.status_code == 200, added_b.text
    assert added_b.json()["status"] == "owned"
    links_b = await _links(account_b)
    assert len(links_b) == 1 and links_b[0].label_snapshot_id == s1.id
    # B owning the pack gives A nothing, and A can still add A's own.
    assert await _links(account_a) == []
    assert (await _status(app_client, token_a, phone_a, s1)).json()["status"] == "eligible_not_owned"
    added_a = await _add(app_client, token_a, phone_a, s1, "c-account-a")
    assert added_a.status_code == 200 and added_a.json()["inventory_item_id"] != added_b.json()["inventory_item_id"]


# ---------------------------------------------------------------------------
# D. Snapshot provenance is never rewritten
# ---------------------------------------------------------------------------


async def test_d_neither_later_capture_rewrites_the_snapshots_provenance(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _device(app_client, token_a)
    phone_b = await _device(app_client, token_b)
    first_event, s1 = await _capture(phone_a, account_a, _facts())
    await _capture(phone_a, account_a, _facts())
    await _capture(phone_b, account_b, _facts())
    assert (await _add(app_client, token_a, phone_a, s1, "d-account-a")).status_code == 200
    assert (await _add(app_client, token_b, phone_b, s1, "d-account-b")).status_code == 200

    after = await _snapshot(s1.id)
    assert after.scan_event_id == first_event.id
    assert after.device_id == first_event.device_id
    assert after.version_number == 1
    async with get_sessionmaker()() as session:
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
        )).scalars().all()
    # No snapshot was minted to work around the shelf.
    assert [row.id for row in snapshots] == [s1.id]


# ---------------------------------------------------------------------------
# E. A current capture with different content cannot claim S1
# ---------------------------------------------------------------------------


async def test_e_a_current_capture_with_different_content_cannot_claim_s1(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _device(app_client, token_a)
    phone_b = await _device(app_client, token_b)
    _, s1 = await _capture(phone_a, account_a, _facts())
    _, s2 = await _capture(phone_b, account_b, _facts("Reformulated Cleanser"))
    assert s2.id != s1.id and s2.version_number == 2

    # B is really holding the reformulated pack; S1 describes other content.
    for response in (
        await _add(app_client, token_b, phone_b, s1, "e-wrong-content"),
        await _status(app_client, token_b, phone_b, s1),
    ):
        assert response.status_code == 422, response.text
    assert await _links(account_b) == []
    # And B's own content is ownable, which is what makes the refusal about content.
    assert (await _add(app_client, token_b, phone_b, s2, "e-right-content")).status_code == 200


async def test_e_batch_differences_are_the_same_content_and_do_not_block_ownership(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """Batch numbers are observations of one capture, not label content."""
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _device(app_client, token_a)
    phone_b = await _device(app_client, token_b)
    _, s1 = await _capture(phone_a, account_a, _facts(batch_number="LOT-A"))
    event_b, reused = await _capture(phone_b, account_b, _facts(batch_number="LOT-B"))
    assert reused.id == s1.id
    added = await _add(app_client, token_b, phone_b, s1, "e-batch")
    assert added.status_code == 200, added.text
    # The shelf row is built from B's own capture, never from the snapshot's facts.
    async with get_sessionmaker()() as session:
        item = await session.get(InventoryItem, uuid.UUID(added.json()["inventory_item_id"]))
        assert item is not None and item.display_name == event_b.label_facts["product_name"]


# ---------------------------------------------------------------------------
# F. A later plain scan withdraws the proof
# ---------------------------------------------------------------------------


async def test_f_a_later_plain_scan_invalidates_ownership_for_a_reused_snapshot(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    await _capture(phone, account, _facts())
    _, s1 = await _capture(phone, account, _facts())
    await _plain_scan(app_client, phone)

    # The newest event is a plain scan: a packet the server cannot vouch for.
    for response in (
        await _add(app_client, token, phone, s1, "f-after-plain"),
        await _status(app_client, token, phone, s1),
    ):
        assert response.status_code == 422, response.text
    assert await _links(account) == []

    # Capturing again restores the proof: the rule is "newest", not "ever".
    await _capture(phone, account, _facts())
    assert (await _add(app_client, token, phone, s1, "f-recaptured")).status_code == 200


async def test_f_a_plain_scan_on_a_first_capture_still_invalidates(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    _, s1 = await _capture(phone, account, _facts())
    await _plain_scan(app_client, phone)
    assert (await _add(app_client, token, phone, s1, "f-first")).status_code == 422
    assert await _links(account) == []


# ---------------------------------------------------------------------------
# G. Wrong account, wrong or unclaimed device
# ---------------------------------------------------------------------------


async def test_g_another_account_cannot_use_the_capturing_phone(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _device(app_client, token_a)
    _, s1 = await _capture(phone_a, account_a, _facts())
    # B presents A's phone: the phone is not B's.
    assert (await _add(app_client, token_b, phone_a, s1, "g-foreign-phone")).status_code == 422
    assert (await _status(app_client, token_b, phone_a, s1)).status_code == 422
    assert await _links(account_b) == []


async def test_g_an_unclaimed_phone_proves_nothing_even_with_a_matching_capture(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone = await _device(app_client)  # never claimed
    _, s1 = await _capture(phone, account, _facts())
    assert (await _add(app_client, token, phone, s1, "g-unclaimed")).status_code == 422
    assert await _links(account) == []


async def test_g_a_capture_belonging_to_another_account_is_refused(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """The phone is A's now, but its newest capture was recorded for B."""
    token_a, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    phone = await _device(app_client, token_a)
    _, s1 = await _capture(phone, account_b, _facts())
    assert (await _add(app_client, token_a, phone, s1, "g-foreign-capture")).status_code == 422
    assert await _links(account_a) == []


async def test_g_a_different_phone_of_the_same_account_is_refused(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone_one = await _device(app_client, token)
    phone_two = await _device(app_client, token)
    _, s1 = await _capture(phone_one, account, _facts())
    # Phone two never scanned this barcode, so it holds no pack at all.
    assert (await _add(app_client, token, phone_two, s1, "g-other-phone")).status_code == 422
    assert await _links(account) == []


# ---------------------------------------------------------------------------
# H. A -> B -> A keeps real versions; a historic version is not the current one
# ---------------------------------------------------------------------------


async def test_h_a_b_a_history_keeps_its_versions_and_resolves_the_current_one(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    _, v1 = await _capture(phone, account, _facts())
    _, v2 = await _capture(phone, account, _facts("Reformulated Cleanser"))
    event_v3, v3 = await _capture(phone, account, _facts())
    assert [v1.version_number, v2.version_number, v3.version_number] == [1, 2, 3]
    assert v3.id != v1.id and v3.content_fingerprint == v1.content_fingerprint
    assert v3.scan_event_id == event_v3.id and v3.previous_snapshot_id == v2.id

    # The same content, but version 1 is history, not what this capture made.
    assert (await _add(app_client, token, phone, v1, "h-historic")).status_code == 422
    assert (await _status(app_client, token, phone, v1)).status_code == 422
    assert (await _add(app_client, token, phone, v2, "h-other-content")).status_code == 422
    assert await _links(account) == []

    added = await _add(app_client, token, phone, v3, "h-current")
    assert added.status_code == 200, added.text
    links = await _links(account)
    assert [(link.label_snapshot_id, link.label_version) for link in links] == [(v3.id, 3)]

    # A later identical capture of A reuses version 3, never version 1.
    token_b, account_b = await registered_supabase_user()
    phone_b = await _device(app_client, token_b)
    _, reused = await _capture(phone_b, account_b, _facts())
    assert reused.id == v3.id
    assert (await _add(app_client, token_b, phone_b, v1, "h-b-historic")).status_code == 422
    assert (await _add(app_client, token_b, phone_b, v3, "h-b-current")).status_code == 200


# ---------------------------------------------------------------------------
# Food confirmation: a lost response retried under the same key
# ---------------------------------------------------------------------------


async def test_a_lost_food_confirmation_retried_under_the_same_key_is_one_capture(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """The server accepted, the phone never heard, the person pressed Confirm again.

    The app now mints one ``client_scan_id`` per draft and reuses it, so the
    retry is a replay: the same event, the same semantic snapshot, and the
    record's confirmation count does not move.
    """
    token, account = await registered_supabase_user()
    phone = await _device(app_client, token)
    facts = {"product_name": "Retry oats", "ingredients_text": "oats",
             "nutrition_per_100g": {"energy_kcal": "370"}, "nutrition_basis": "per_100g"}
    async with get_sessionmaker()() as session:
        run = AIRun(
            account_id=account, feature="product_label_transcribe", provider="test", model="test-model",
            prompt_version="scan-label.v1", schema_version="scan-label.v1",
            status=AI_STATUS_SUCCEEDED, validation_passed=True,
        )
        session.add(run)
        await session.flush()
        session.add(AIRunOutput(ai_run_id=run.id, schema_version="scan-label.v1", payload=facts))
        await session.commit()
        run_id = run.id
    draft_key = uuid.uuid4().hex
    body = {"barcode": BARCODE, "ai_run_id": str(run_id), "client_scan_id": draft_key}
    first = await app_client.post("/api/v2/scan/label/confirm", headers={**phone, **auth(token)}, json=body)
    assert first.status_code == 201, first.text
    # The response is "lost"; the same draft is confirmed again.
    retry = await app_client.post("/api/v2/scan/label/confirm", headers={**phone, **auth(token)}, json=body)
    assert retry.status_code == 201, retry.text
    assert retry.json()["confirmations"] == first.json()["confirmations"]

    async with get_sessionmaker()() as session:
        events = (await session.execute(
            select(ScanEvent).where(ScanEvent.barcode == BARCODE, ScanEvent.outcome == product_service.OUTCOME_LABEL)
        )).scalars().all()
        snapshots = (await session.execute(
            select(LabelSnapshot).where(LabelSnapshot.barcode == BARCODE)
        )).scalars().all()
        record = (await session.execute(
            select(ProductRecord).where(ProductRecord.barcode == BARCODE)
        )).scalar_one()
    assert [event.client_scan_id for event in events] == [draft_key]
    assert len(snapshots) == 1 and snapshots[0].scan_event_id == events[0].id
    assert record.confirmation_count == first.json()["confirmations"]
