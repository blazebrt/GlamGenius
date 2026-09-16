"""PostgreSQL/API acceptance proof for Step 10A exact scan ownership."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.ai_gateway.models import AIRun
from app.domains.inventory import scan_ownership
from app.domains.inventory.models import InventoryItem, InventoryProductLink
from app.domains.inventory.schemas import ItemCreate, ScanOwnershipCreate
from app.domains.inventory.service import create_item, serialize_item
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.privacy import deletion_service
from app.domains.product.devices import _hash
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDecisionEvent, ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


async def _chain(account_id, *, barcode="8901234567890", version=1, fingerprint="a" * 64, facts=None, product_id=None):
    raw = f"device-token-{uuid.uuid4().hex}"
    async with get_sessionmaker()() as s:
        d = ScanDevice(device_key=f"step10a-{uuid.uuid4().hex}", token_hash=_hash(raw), claimed_by_account_id=account_id)
        if product_id is None:
            product = ProductRecord(barcode=barcode, origin="label_capture", confidence="unverified")
            s.add(product)
        else:
            product = await s.get(ProductRecord, product_id)
            assert product is not None
        s.add(d); await s.flush()
        run = AIRun(account_id=account_id, feature="label_capture", provider="test", model="test", prompt_version="test", schema_version="test", status="succeeded", validation_passed=True)
        s.add(run); await s.flush()
        f = facts if facts is not None else {"product_category": "beauty", "product_name": "Verified Cleanser", "brand": "Verified Brand"}
        e = ScanEvent(device_id=d.id, account_id=account_id, barcode=barcode, outcome="label_captured", client_scan_id=uuid.uuid4().hex, label_facts=f, ai_run_id=run.id)
        s.add(e); await s.flush()
        snap = LabelSnapshot(barcode=barcode, device_id=d.id, scan_event_id=e.id, facts=f, confidence="unverified", content_fingerprint=fingerprint, version_number=version, changed_fields=[], completeness="complete_for_grading")
        s.add(snap); await s.commit()
        return raw, product.id, snap


def _headers(auth_token, device_token): return {**auth(auth_token), "X-Device-Token": device_token}
def _body(snapshot, key="step10a-key"):
    return {"barcode": snapshot.barcode, "label_snapshot_id": str(snapshot.id), "label_version": snapshot.version_number, "content_fingerprint": snapshot.content_fingerprint, "client_mutation_id": key}


async def _rows(account_id):
    async with get_sessionmaker()() as s:
        return ((await s.execute(select(InventoryItem).where(InventoryItem.account_id == account_id))).scalars().all(), (await s.execute(select(InventoryProductLink).where(InventoryProductLink.account_id == account_id))).scalars().all())


async def test_add_and_exact_status(app_client: AsyncClient, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user(); device, product_id, snap = await _chain(account); body = _body(snap); headers = _headers(token, device)
    before = await app_client.get(f"/api/v2/inventory/from-scan/{body['barcode']}/status", headers=headers, params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert before.status_code == 200 and before.json()["status"] == "eligible_not_owned"
    added = await app_client.post("/api/v2/inventory/from-scan", headers=headers, json=body)
    assert added.status_code == 200 and added.json()["status"] == "owned"
    items, links = await _rows(account); assert len(items) == len(links) == 1
    assert links[0].product_record_id == product_id and links[0].label_snapshot_id == snap.id and links[0].content_fingerprint == "a" * 64
    after = await app_client.get(f"/api/v2/inventory/from-scan/{body['barcode']}/status", headers=headers, params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert after.json()["status"] == "owned" and after.json()["inventory_item_id"] == added.json()["inventory_item_id"]


async def test_foreign_authority_mismatch_and_off_like_facts_create_nothing(app_client: AsyncClient, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user(); token_b, account_b = await registered_supabase_user(); device, _, snap = await _chain(account_a)
    response = await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token_b, device), json=_body(snap))
    assert response.status_code == 422 and await _rows(account_b) == ([], [])
    device, _, snap = await _chain(account_a, barcode="8901234567891", facts={"ingredients_text": "public fallback is prohibited"})
    response = await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token_a, device), json=_body(snap, "off-key"))
    assert response.status_code == 422 and await _rows(account_a) == ([], [])


async def test_store_a_facts_never_cross_the_odbl_wall_into_shelf_ownership(app_client: AsyncClient, db_clean, off_clean, registered_supabase_user):
    """OFF may know a product, but Store B pack facts alone establish a shelf item."""
    token, account = await registered_supabase_user()
    barcode = "8901234567892"
    async with get_off_sessionmaker()() as off_session:
        off_session.add(OffProduct(
            barcode=barcode, product_name="OFF catalogue name", brands="OFF catalogue brand",
            ingredients_text="OFF catalogue ingredients",
        ))
        await off_session.commit()
    device, _, snapshot = await _chain(account, barcode=barcode, facts={})
    response = await app_client.post(
        "/api/v2/inventory/from-scan", headers=_headers(token, device), json=_body(snapshot, "odbl-wall"),
    )
    assert response.status_code == 422
    assert await _rows(account) == ([], [])
    async with get_off_sessionmaker()() as off_session:
        stored = await off_session.get(OffProduct, barcode)
    assert stored is not None and stored.product_name == "OFF catalogue name"


async def test_same_key_replays_and_changed_identity_conflicts(app_client: AsyncClient, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user(); device, _, snap = await _chain(account); body = _body(snap); headers = _headers(token, device)
    first = await app_client.post("/api/v2/inventory/from-scan", headers=headers, json=body)
    replay = await app_client.post("/api/v2/inventory/from-scan", headers=headers, json=body)
    assert first.status_code == replay.status_code == 200 and first.json()["inventory_item_id"] == replay.json()["inventory_item_id"]
    changed = await app_client.post("/api/v2/inventory/from-scan", headers=headers, json={**body, "content_fingerprint": "b" * 64})
    assert changed.status_code == 422
    items, links = await _rows(account); assert len(items) == len(links) == 1


async def test_later_formula_version_does_not_inherit_owned_status(app_client: AsyncClient, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user(); device1, product_id, first = await _chain(account)
    first_body = _body(first); assert (await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device1), json=first_body)).status_code == 200
    device2, _, second = await _chain(account, version=2, fingerprint="b" * 64, product_id=product_id)
    second_body = _body(second, "second-version-key")
    status = await app_client.get(f"/api/v2/inventory/from-scan/{second.barcode}/status", headers=_headers(token, device2), params={k: second_body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert status.status_code == 200 and status.json()["status"] == "eligible_not_owned"
    _, links = await _rows(account); assert len(links) == 1
    assert links[0].label_snapshot_id == first.id and links[0].label_version == 1 and links[0].content_fingerprint == "a" * 64


@pytest.mark.parametrize("change", ["barcode", "snapshot", "version", "fingerprint"])
async def test_exact_identity_mismatches_create_no_ownership(app_client: AsyncClient, db_clean, registered_supabase_user, change):
    token, account = await registered_supabase_user(); device, _, snapshot = await _chain(account); body = _body(snapshot, f"bad-{change}")
    if change == "barcode": body["barcode"] = "8900000000001"
    elif change == "snapshot": body["label_snapshot_id"] = str(uuid.uuid4())
    elif change == "version": body["label_version"] = 2
    else: body["content_fingerprint"] = "b" * 64
    response = await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device), json=body)
    assert response.status_code == 422 and await _rows(account) == ([], [])


async def test_unauthenticated_shelf_routes_are_private(app_client: AsyncClient, db_clean, registered_supabase_user):
    _, account = await registered_supabase_user(); _, _, snapshot = await _chain(account); body = _body(snapshot)
    post = await app_client.post("/api/v2/inventory/from-scan", json=body)
    status = await app_client.get(f"/api/v2/inventory/from-scan/{snapshot.barcode}/status", params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert post.status_code == status.status_code == 401


async def test_real_snapshot_from_another_barcode_is_rejected(app_client: AsyncClient, db_clean, registered_supabase_user):
    token, account = await registered_supabase_user()
    device_a, _, snapshot_a = await _chain(account, barcode="8901234567890")
    _, _, snapshot_b = await _chain(account, barcode="8901234567891")
    body = _body(snapshot_b, "cross-barcode-key")
    body["barcode"] = snapshot_a.barcode
    response = await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device_a), json=body)
    assert response.status_code == 422 and await _rows(account) == ([], [])


async def test_status_is_account_scoped_for_shared_global_product(app_client: AsyncClient, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user(); token_b, account_b = await registered_supabase_user()
    device_a, product_id, snapshot_a = await _chain(account_a)
    assert (await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token_a, device_a), json=_body(snapshot_a))).status_code == 200
    device_b, _, snapshot_b = await _chain(account_b, version=2, fingerprint="b" * 64, product_id=product_id)
    body_b = _body(snapshot_b, "account-b-status")
    response = await app_client.get(f"/api/v2/inventory/from-scan/{snapshot_b.barcode}/status", headers=_headers(token_b, device_b), params={k: body_b[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert response.status_code == 200
    assert response.json()["status"] == "eligible_not_owned" and response.json()["inventory_item_id"] is None


async def test_status_never_leaks_another_account_link_for_the_exact_same_snapshot(app_client: AsyncClient, db_clean, registered_supabase_user):
    token_a, account_a = await registered_supabase_user(); token_b, account_b = await registered_supabase_user()
    device_b, product_id, snapshot = await _chain(account_b)
    async with get_sessionmaker()() as session:
        item = await create_item(session, account_a, ItemCreate(category="beauty", display_name="A private item", client_mutation_id="a-seeded-link"))
        session.add(InventoryProductLink(account_id=account_a, inventory_item_id=item.id, product_record_id=product_id, barcode=snapshot.barcode, label_snapshot_id=snapshot.id, label_version=snapshot.version_number, content_fingerprint=snapshot.content_fingerprint, source="explicit_scan"))
        await session.commit()
    body = _body(snapshot, "b-exact-status")
    response = await app_client.get(f"/api/v2/inventory/from-scan/{snapshot.barcode}/status", headers=_headers(token_b, device_b), params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")})
    assert response.status_code == 200
    assert response.json()["status"] == "eligible_not_owned" and response.json()["inventory_item_id"] is None


@pytest.mark.parametrize("decision", ["BUY", "WAIT", "SKIP"])
async def test_purchase_decisions_never_create_scan_shelf_ownership(app_client: AsyncClient, db_clean, registered_supabase_user, decision):
    token, account = await registered_supabase_user(); _, _, snapshot = await _chain(account)
    response = await app_client.post(f"/api/v2/scan/verdict/{snapshot.barcode}/memory", headers=auth(token), json={"decision": decision, "label_snapshot_id": str(snapshot.id), "label_version": snapshot.version_number, "content_fingerprint": snapshot.content_fingerprint, "idempotency_key": f"decision-{decision.lower()}"})
    assert response.status_code == 200 and await _rows(account) == ([], [])


async def test_concurrent_same_key_replays_one_exact_ownership(app_client: AsyncClient, db_clean, registered_supabase_user):
    """Two real, simultaneous same-key requests settle on one shelf row.

    No synchronisation barrier inside ``create_item`` here, and deliberately
    so. Both requests must present the same device, because the exact identity
    is only provable for the device that scanned the pack — and resolving a
    device token marks ``last_seen_at``, so each request takes a row lock on
    that device for the life of its transaction. Holding one request inside
    ``create_item`` until the other arrives therefore cannot happen: the second
    is still waiting for the first request's device row. The barrier did not
    expose a race, it deadlocked, and the 5-second wait made it a guaranteed
    failure rather than a flake.

    That serialisation is production's real behaviour and is worth asserting as
    it stands. The interleaving the barrier was reaching for is exercised
    directly against PostgreSQL in the service-level test below, where there is
    no device row in the way.
    """
    token, account = await registered_supabase_user(); device, _, snapshot = await _chain(account); body = _body(snapshot, "concurrent-same-key")
    first, second = await asyncio.gather(*[
        app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device), json=body)
        for _ in range(2)
    ])
    assert first.status_code == second.status_code == 200
    assert first.json()["inventory_item_id"] == second.json()["inventory_item_id"]
    items, links = await _rows(account); assert len(items) == len(links) == 1


async def test_truly_interleaved_same_key_inserts_resolve_to_one_row(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The unique constraint, exercised by two transactions that really do overlap.

    Two independent sessions are both held inside ``create_item`` and released
    together, so both attempt the insert before either commits. PostgreSQL
    blocks the second on the unique index until the first commits, and the
    second then takes the ``IntegrityError`` path in ``add_from_scan``: roll
    back to the savepoint, re-read the winning row, and confirm it carries the
    same exact identity before reporting it as owned.

    Sessions are opened directly rather than through HTTP because the device
    row lock taken while resolving a device token would serialise the two
    requests before they ever reached the contended insert.
    """
    _, account = await registered_supabase_user()
    device_token, _, snapshot = await _chain(account)
    async with get_sessionmaker()() as session:
        device_row = (await session.execute(
            select(ScanDevice).where(ScanDevice.token_hash == _hash(device_token))
        )).scalars().one()
        device_id = device_row.id

    body = ScanOwnershipCreate(
        barcode=snapshot.barcode, label_snapshot_id=snapshot.id,
        label_version=snapshot.version_number, content_fingerprint=snapshot.content_fingerprint,
        client_mutation_id="interleaved-same-key",
    )

    original = scan_ownership.inventory_service.create_item
    arrived = 0; lock = asyncio.Lock(); open_gate = asyncio.Event()

    async def synchronized_create(*args, **kwargs):
        nonlocal arrived
        async with lock:
            arrived += 1
            if arrived == 2: open_gate.set()
        await asyncio.wait_for(open_gate.wait(), timeout=10)
        return await original(*args, **kwargs)

    monkeypatch.setattr(scan_ownership.inventory_service, "create_item", synchronized_create)

    async def add_in_its_own_session():
        async with get_sessionmaker()() as session:
            device = await session.get(ScanDevice, device_id)
            result = await scan_ownership.add_from_scan(
                session, account_id=account, device=device, body=body,
            )
            await session.commit()
            return result

    first, second = await asyncio.gather(add_in_its_own_session(), add_in_its_own_session())

    # Both transactions really were inside create_item together.
    assert arrived == 2
    assert first["status"] == second["status"] == "owned"
    assert first["inventory_item_id"] == second["inventory_item_id"]
    items, links = await _rows(account)
    assert len(items) == 1 and len(links) == 1
    assert links[0].label_snapshot_id == snapshot.id
    assert links[0].content_fingerprint == snapshot.content_fingerprint


async def test_concurrent_same_key_different_identity_conflicts_without_hybrid_link(app_client: AsyncClient, db_clean, registered_supabase_user, monkeypatch):
    token, account = await registered_supabase_user()
    device_a, product_a, snapshot_a = await _chain(account, barcode="8901234567890")
    device_b, product_b, snapshot_b = await _chain(account, barcode="8901234567891", fingerprint="b" * 64)
    original = scan_ownership.inventory_service.create_item
    arrived = 0; lock = asyncio.Lock(); open_gate = asyncio.Event()

    async def synchronized_create(*args, **kwargs):
        nonlocal arrived
        async with lock:
            arrived += 1
            if arrived == 2: open_gate.set()
        await asyncio.wait_for(open_gate.wait(), timeout=5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(scan_ownership.inventory_service, "create_item", synchronized_create)
    key = "concurrent-changed-key"
    first, second = await asyncio.gather(
        app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device_a), json=_body(snapshot_a, key)),
        app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device_b), json=_body(snapshot_b, key)),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 422]
    items, links = await _rows(account); assert len(items) == len(links) == 1
    link = links[0]
    assert (link.product_record_id, link.barcode, link.label_snapshot_id, link.label_version, link.content_fingerprint) in {
        (product_a, snapshot_a.barcode, snapshot_a.id, snapshot_a.version_number, snapshot_a.content_fingerprint),
        (product_b, snapshot_b.barcode, snapshot_b.id, snapshot_b.version_number, snapshot_b.content_fingerprint),
    }


async def test_manual_inventory_remains_valid_without_product_link(db_clean, registered_supabase_user):
    _, account = await registered_supabase_user()
    async with get_sessionmaker()() as session:
        item = await create_item(session, account, ItemCreate(category="beauty", display_name="Manual cleanser", client_mutation_id="manual-without-link"))
        await session.commit()
        payload = await serialize_item(session, item)
    items, links = await _rows(account)
    assert payload["id"] == str(item.id) and len(items) == 1 and links == []


async def test_manual_unlinked_inventory_still_works_through_the_real_user_paths(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """A link is new in Step 10A, and must not have become a requirement.

    Calling ``serialize_item`` directly proves the serialiser copes. It does
    not prove the screens do: every item a person already owns was entered by
    hand and has no ``InventoryProductLink``, so if the list or the detail
    route had started joining on one, an existing shelf would have emptied
    itself on upgrade. These are the canonical paths those screens use.
    """
    token, account = await registered_supabase_user()
    async with get_sessionmaker()() as session:
        manual = await create_item(session, account, ItemCreate(
            category="beauty", display_name="Hand entered cleanser", brand="Manual Brand",
            client_mutation_id="manual-through-real-paths",
        ))
        await session.commit()
        manual_id = manual.id

    listed = await app_client.get("/api/v2/inventory/items", headers=auth(token))
    assert listed.status_code == 200
    listed_ids = [row["id"] for row in listed.json()["items"]]
    assert str(manual_id) in listed_ids
    assert listed.json()["pagination"]["total"] == 1

    detail = await app_client.get(f"/api/v2/inventory/items/{manual_id}", headers=auth(token))
    assert detail.status_code == 200
    assert detail.json()["id"] == str(manual_id)
    assert detail.json()["display_name"] == "Hand entered cleanser"

    # Still genuinely unlinked, and the shelf reasoning that reads this active
    # category still sees it.
    items, links = await _rows(account)
    assert len(items) == 1 and links == []
    from datetime import date

    from app.domains.routines import shelf as routines_shelf
    async with get_sessionmaker()() as session:
        context = await routines_shelf.gather(session, account_id=account, today=date.today())
    assert [owned.id for owned in context.owned] == [manual_id]


async def test_exact_shelf_link_exports_and_deletion_anonymises_pack_authority(
    app_client: AsyncClient, db_clean, registered_supabase_user, monkeypatch,
):
    """The real privacy paths remove A's shelf row but retain global pack records."""
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    device, product_id, snapshot = await _chain(account_a)
    added = await app_client.post(
        "/api/v2/inventory/from-scan", headers=_headers(token_a, device), json=_body(snapshot, "privacy-link"),
    )
    assert added.status_code == 200
    # Account B gets a real explicit-scan item and link of its own, through the
    # same route A used. A manual item would leave the interesting question
    # unasked: a manual item has no InventoryProductLink, so it cannot show
    # whether *B's link* survives A's deletion.
    device_b, product_b_id, snapshot_b = await _chain(account_b, barcode="8901234567891", fingerprint="b" * 64)
    added_b = await app_client.post(
        "/api/v2/inventory/from-scan", headers=_headers(token_b, device_b), json=_body(snapshot_b, "privacy-link-b"),
    )
    assert added_b.status_code == 200
    item_b_id = uuid.UUID(added_b.json()["inventory_item_id"])
    async with get_sessionmaker()() as session:
        link_b = (await session.execute(select(InventoryProductLink).where(
            InventoryProductLink.account_id == account_b
        ))).scalars().all()
    assert len(link_b) == 1
    link_b_id = link_b[0].id
    exported = await app_client.get("/api/v2/privacy/export", headers=auth(token_a))
    assert exported.status_code == 200
    links = exported.json()["domains"]["inventory"]["product_links"]
    assert [link["inventory_item_id"] for link in links] == [added.json()["inventory_item_id"]]
    # B's real link exists, and is nowhere in A's export.
    exported_ids = {link["id"] for link in links if "id" in link}
    assert str(link_b_id) not in exported_ids
    assert str(item_b_id) not in {link["inventory_item_id"] for link in links}
    assert snapshot_b.content_fingerprint not in exported.text

    class _Admin:
        class auth:
            class admin:
                @staticmethod
                def delete_user(_uid):
                    return None

    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: _Admin())
    requested = await app_client.delete("/api/v2/privacy/account", headers=auth(token_a))
    assert requested.status_code == 202
    async with get_sessionmaker()() as session:
        await deletion_service.drain_all(session)
        await session.commit()
    async with get_sessionmaker()() as session:
        assert await session.get(InventoryItem, uuid.UUID(added.json()["inventory_item_id"])) is None
        assert (await session.execute(select(InventoryProductLink).where(InventoryProductLink.account_id == account_a))).scalars().all() == []
        assert await session.get(InventoryItem, item_b_id) is not None
        surviving_b = (await session.execute(select(InventoryProductLink).where(
            InventoryProductLink.account_id == account_b
        ))).scalars().all()
        assert len(surviving_b) == 1 and surviving_b[0].id == link_b_id
        assert surviving_b[0].inventory_item_id == item_b_id
        # No proprietary row is left pointing at a deleted owner.
        orphans = (await session.execute(select(InventoryProductLink).where(
            InventoryProductLink.account_id.is_(None)
        ))).scalars().all()
        assert orphans == []
        assert await session.get(ProductRecord, product_id) is not None
        assert await session.get(ProductRecord, product_b_id) is not None
        assert await session.get(LabelSnapshot, snapshot_b.id) is not None
        preserved = await session.get(LabelSnapshot, snapshot.id)
        assert preserved is not None
        event = await session.get(ScanEvent, preserved.scan_event_id)
        assert event is not None and event.account_id is None


# ---------------------------------------------------------------------------
# A decision and an ownership record are two independent memories
# ---------------------------------------------------------------------------


async def test_buy_then_explicit_add_are_two_independent_records(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """BUY is a decision. Ownership is a separate statement the person makes.

    The parameterised test above proves a decision creates no ownership. This
    proves the other half: that adding to the shelf afterwards does not consume
    or rewrite the decision. Both memories must survive as separate records,
    because inferring ownership from BUY would put a product on someone's shelf
    that they considered and never bought.
    """
    token, account = await registered_supabase_user()
    device, _, snapshot = await _chain(account)
    body = _body(snapshot, "buy-then-add")
    headers = _headers(token, device)

    decision = await app_client.post(
        f"/api/v2/scan/verdict/{snapshot.barcode}/memory", headers=auth(token),
        json={
            "decision": "BUY", "label_snapshot_id": str(snapshot.id),
            "label_version": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "idempotency_key": "buy-before-add",
        },
    )
    assert decision.status_code == 200

    # The decision alone owns nothing.
    assert await _rows(account) == ([], [])
    async with get_sessionmaker()() as session:
        decisions = (await session.execute(
            select(ScanDecisionEvent).where(ScanDecisionEvent.account_id == account)
        )).scalars().all()
    assert len(decisions) == 1 and decisions[0].decision == "BUY"
    decision_id = decisions[0].id

    added = await app_client.post("/api/v2/inventory/from-scan", headers=headers, json=body)
    assert added.status_code == 200 and added.json()["status"] == "owned"

    # The decision event is untouched by the add.
    async with get_sessionmaker()() as session:
        still_there = await session.get(ScanDecisionEvent, decision_id)
        assert still_there is not None and still_there.decision == "BUY"
        remaining = (await session.execute(
            select(ScanDecisionEvent).where(ScanDecisionEvent.account_id == account)
        )).scalars().all()
    assert len(remaining) == 1

    # And exactly one ownership record now exists, carrying the exact identity.
    items, links = await _rows(account)
    assert len(items) == 1 and len(links) == 1
    link = links[0]
    assert link.barcode == snapshot.barcode
    assert link.label_snapshot_id == snapshot.id
    assert link.label_version == snapshot.version_number
    assert link.content_fingerprint == snapshot.content_fingerprint
    assert link.inventory_item_id == items[0].id


# ---------------------------------------------------------------------------
# Physical pack authority is not account authentication
# ---------------------------------------------------------------------------


async def test_one_device_cannot_claim_a_pack_proven_only_on_another_device(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """Same account, same person, two genuinely scanned packs.

    Cross-account rejection only proves authentication. This proves authority:
    the submitted snapshot must be the pack the *submitting device* currently
    has proven. Pack B's identity is entirely valid and belongs to this very
    account, and it is still refused when presented with device A's authority,
    because device A never scanned it.
    """
    token, account = await registered_supabase_user()
    device_a, _, snapshot_a = await _chain(account, barcode="8901234567890")
    _, _, snapshot_b = await _chain(account, barcode="8901234567891", fingerprint="b" * 64)

    # Every field is snapshot B's own; only the device authority is A's.
    response = await app_client.post(
        "/api/v2/inventory/from-scan",
        headers=_headers(token, device_a), json=_body(snapshot_b, "wrong-pack-authority"),
    )
    assert response.status_code == 422
    assert await _rows(account) == ([], [])

    # The same submission on B's own device is accepted, which is what makes
    # the refusal above about authority rather than a malformed request.
    device_b_token, _, snapshot_b_again = await _chain(
        account, barcode="8901234567892", fingerprint="c" * 64,
    )
    accepted = await app_client.post(
        "/api/v2/inventory/from-scan",
        headers=_headers(token, device_b_token), json=_body(snapshot_b_again, "right-pack-authority"),
    )
    assert accepted.status_code == 200
    items, links = await _rows(account)
    assert len(items) == 1 and len(links) == 1
    assert links[0].label_snapshot_id == snapshot_b_again.id
    assert snapshot_a.id != snapshot_b.id


async def test_status_also_refuses_a_pack_proven_only_on_another_device(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """The read side must enforce the same authority as the write side."""
    token, account = await registered_supabase_user()
    device_a, _, _ = await _chain(account, barcode="8901234567890")
    _, _, snapshot_b = await _chain(account, barcode="8901234567891", fingerprint="b" * 64)
    body = _body(snapshot_b, "wrong-pack-status")
    response = await app_client.get(
        f"/api/v2/inventory/from-scan/{snapshot_b.barcode}/status",
        headers=_headers(token, device_a),
        params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")},
    )
    assert response.status_code == 422
    assert await _rows(account) == ([], [])


# ---------------------------------------------------------------------------
# Product Truth is public; ownership is not
# ---------------------------------------------------------------------------


async def test_product_truth_is_public_while_shelf_ownership_requires_an_account(
    app_client: AsyncClient, db_clean, registered_supabase_user,
):
    """Step 10A must not have made the public verdict private.

    Product Truth answers to an anonymous device and no user account. The two
    private shelf routes answer to nobody without one. Asserting both in one
    place is what keeps a future change from quietly moving the line.
    """
    token, account = await registered_supabase_user()
    device, _, snapshot = await _chain(account)
    body = _body(snapshot, "public-truth")

    # No Authorization header anywhere in this request.
    public = await app_client.get(
        f"/api/v2/scan/verdict/{snapshot.barcode}", headers={"X-Device-Token": device},
    )
    assert public.status_code == 200, public.text
    assert "authorization" not in {key.lower() for key in public.request.headers}

    private_write = await app_client.post(
        "/api/v2/inventory/from-scan", headers={"X-Device-Token": device}, json=body,
    )
    private_read = await app_client.get(
        f"/api/v2/inventory/from-scan/{snapshot.barcode}/status",
        headers={"X-Device-Token": device},
        params={k: body[k] for k in ("label_snapshot_id", "label_version", "content_fingerprint")},
    )
    assert private_write.status_code == 401
    assert private_read.status_code == 401
    assert await _rows(account) == ([], [])
    assert token  # the account exists; it simply was not presented above
