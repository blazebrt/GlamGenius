import uuid

import pytest
from app.domains.inventory.models import InventoryItem
from app.domains.product.models import LabelSnapshot, ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def seeded_snapshot(db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    async with factory() as session:
        device = ScanDevice(
            id=uuid.uuid4(), device_key=f"test-device-{uuid.uuid4().hex}",
            token_hash="test-token-hash", platform="android",
            claimed_by_account_id=account_id,
        )
        session.add(device)
        await session.flush()
        event = ScanEvent(
            id=uuid.uuid4(), device_id=device.id, account_id=account_id,
            barcode="4000123456789", outcome="found_local",
            client_scan_id=uuid.uuid4().hex,
        )
        session.add(event)
        await session.flush()
        snapshot = LabelSnapshot(
            id=uuid.uuid4(),
            barcode="4000123456789",
            device_id=device.id,
            scan_event_id=event.id,
            facts={"product_name": "Test Product"},
            confidence="unverified",
            content_fingerprint="test-fingerprint-123",
            version_number=1,
            changed_fields=[],
            completeness="complete_for_grading"
        )
        session.add(snapshot)
        await session.flush()
        await session.commit()
        return {"snapshot": snapshot, "token": token, "account_id": account_id}

async def test_no_memory_before_decision(app_client: AsyncClient, seeded_snapshot):
    response = await app_client.get("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == {"barcode": "4000123456789", "label_snapshot_id": str(seeded_snapshot["snapshot"].id), "label_version": 1, "content_fingerprint": "test-fingerprint-123"}
    assert body["decision"] is None and body["history"] == []

async def test_decision_lifecycle_buy_wait_skip(app_client: AsyncClient, seeded_snapshot):
    for decision in ["BUY", "WAIT", "SKIP"]:
        response = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
            "decision": decision,
            "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
            "label_version": 1,
            "content_fingerprint": "test-fingerprint-123",
            "idempotency_key": f"key-{decision}"
        })
        assert response.status_code == 200

async def test_invalid_decision_rejected(app_client: AsyncClient, seeded_snapshot):
    response = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "MAYBE",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-maybe"
    })
    assert response.status_code == 422

async def test_changed_snapshot_fails_closed(app_client: AsyncClient, seeded_snapshot):
    response = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "BUY",
        "label_snapshot_id": str(uuid.uuid4()), # wrong ID
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-wrong-id"
    })
    assert response.status_code == 409

async def test_unauthenticated_denial(app_client: AsyncClient, seeded_snapshot):
    response = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-unauth"
    })
    assert response.status_code == 401

async def test_stable_retry_does_not_append(app_client: AsyncClient, seeded_snapshot):
    payload = {
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-stable"
    }
    r1 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json=payload)
    r2 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]

async def test_same_key_changed_payload_conflicts(app_client: AsyncClient, seeded_snapshot):
    await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-conflict"
    })
    r2 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "SKIP",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-conflict"
    })
    assert r2.status_code == 409

async def test_reconsideration_appends(app_client: AsyncClient, seeded_snapshot):
    await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "WAIT",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-recon-1"
    })
    await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-recon-2"
    })
    r = await app_client.get("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]))
    assert len(r.json()["history"]) == 2
    assert r.json()["decision"]["decision"] == "BUY"

async def test_buy_creates_no_inventory_ownership(app_client: AsyncClient, seeded_snapshot):
    await app_client.post("/api/v2/scan/verdict/4000123456789/memory", headers=auth(seeded_snapshot["token"]), json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot["snapshot"].id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-buy-inv"
    })
    async with get_sessionmaker()() as session:
        items = (await session.execute(select(InventoryItem).where(InventoryItem.account_id == seeded_snapshot["account_id"]))).scalars().all()
        assert items == []
