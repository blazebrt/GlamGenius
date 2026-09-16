"""PostgreSQL/API acceptance proof for Step 10A exact scan ownership."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.domains.ai_gateway.models import AIRun
from app.domains.inventory import scan_ownership
from app.domains.inventory.models import InventoryItem, InventoryProductLink
from app.domains.inventory.schemas import ItemCreate
from app.domains.inventory.service import create_item
from app.domains.product.devices import _hash
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice, ScanEvent
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
    device, _, snap = await _chain(account_a, facts={"ingredients_text": "public fallback is prohibited"})
    response = await app_client.post("/api/v2/inventory/from-scan", headers=_headers(token_a, device), json=_body(snap, "off-key"))
    assert response.status_code == 422 and await _rows(account_a) == ([], [])


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


async def test_concurrent_same_key_replays_one_exact_ownership(app_client: AsyncClient, db_clean, registered_supabase_user, monkeypatch):
    token, account = await registered_supabase_user(); device, _, snapshot = await _chain(account); body = _body(snapshot, "concurrent-same-key")
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
    first, second = await asyncio.gather(*[
        app_client.post("/api/v2/inventory/from-scan", headers=_headers(token, device), json=body)
        for _ in range(2)
    ])
    assert first.status_code == second.status_code == 200
    assert first.json()["inventory_item_id"] == second.json()["inventory_item_id"]
    items, links = await _rows(account); assert len(items) == len(links) == 1


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
