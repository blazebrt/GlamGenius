import uuid

import pytest
from app.db import get_sessionmaker
from app.domains.product.models import InventoryShelf, LabelSnapshot, ScanDevice, ScanEvent
from httpx import AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

@pytest.fixture
async def seeded_snapshot(registered_supabase_user):
    factory = get_sessionmaker()
    async with factory() as session:
        account_id = uuid.UUID(registered_supabase_user["account_id"])
        device = ScanDevice(id=uuid.uuid4(), identity_token="test-device-token", registered_account_id=account_id)
        session.add(device)
        event = ScanEvent(id=uuid.uuid4(), device_id=device.id, account_id=account_id, barcode="4000123456789")
        session.add(event)
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
        await session.commit()
        return snapshot

async def test_no_memory_before_decision(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    response = await authenticated_client.get("/v2/scan/verdict/4000123456789/memory")
    assert response.status_code == 200
    assert response.json() == {"scan_decision_memory_version": "v1", "identity": None, "decision": None, "history": []}

async def test_decision_lifecycle_buy_wait_skip(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    for decision in ["BUY", "WAIT", "SKIP"]:
        response = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
            "decision": decision,
            "label_snapshot_id": str(seeded_snapshot.id),
            "label_version": 1,
            "content_fingerprint": "test-fingerprint-123",
            "idempotency_key": f"key-{decision}"
        })
        assert response.status_code == 200

async def test_invalid_decision_rejected(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    response = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "MAYBE",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-maybe"
    })
    assert response.status_code == 422

async def test_changed_snapshot_fails_closed(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    response = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(uuid.uuid4()), # wrong ID
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-wrong-id"
    })
    assert response.status_code == 409

async def test_unauthenticated_denial(client: AsyncClient, seeded_snapshot: LabelSnapshot):
    response = await client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-unauth"
    })
    assert response.status_code == 401

async def test_stable_retry_does_not_append(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    payload = {
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-stable"
    }
    r1 = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json=payload)
    r2 = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]

async def test_same_key_changed_payload_conflicts(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-conflict"
    })
    r2 = await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "SKIP",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-conflict"
    })
    assert r2.status_code == 409

async def test_reconsideration_appends(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "WAIT",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-recon-1"
    })
    await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-recon-2"
    })
    r = await authenticated_client.get("/v2/scan/verdict/4000123456789/memory")
    assert len(r.json()["history"]) == 2
    assert r.json()["decision"]["decision"] == "BUY"

async def test_buy_creates_no_inventory_ownership(authenticated_client: AsyncClient, seeded_snapshot: LabelSnapshot):
    await authenticated_client.post("/v2/scan/verdict/4000123456789/memory", json={
        "decision": "BUY",
        "label_snapshot_id": str(seeded_snapshot.id),
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "key-buy-inv"
    })
    async with get_sessionmaker()() as session:
        shelves = (await session.execute(select(InventoryShelf))).scalars().all()
        assert len(shelves) == 0
