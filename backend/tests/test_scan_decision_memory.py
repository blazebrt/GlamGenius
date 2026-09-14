import uuid

import pytest
from app.db import get_sessionmaker
from app.domains.product.models import LabelSnapshot


@pytest.fixture
async def seeded_snapshot():
    factory = get_sessionmaker()
    async with factory() as session:
        snapshot = LabelSnapshot(
            id=uuid.uuid4(),
            barcode="4000123456789",
            device_id=uuid.uuid4(),
            scan_event_id=None,
            facts={"product_name": "Test Product"},
            confidence="unverified",
            content_fingerprint="test-fingerprint-123",
            version_number=1,
            changed_fields=[],
            completeness="missing_ingredients"
        )
        session.add(snapshot)
        await session.commit()
        return snapshot

@pytest.mark.asyncio
async def test_read_memory_no_snapshot(db_clean, app_client, registered_supabase_user):
    headers = {"Authorization": f"Bearer {registered_supabase_user['access_token']}"}
    resp = await app_client.get("/api/v2/scan/verdict/4000123456789/memory", headers=headers)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_save_memory_success(db_clean, app_client, registered_supabase_user, seeded_snapshot):
    headers = {"Authorization": f"Bearer {registered_supabase_user['access_token']}"}
    payload = {
        "decision": "WAIT",
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "my-client-key-1"
    }
    resp = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "WAIT"

    # read it back
    resp2 = await app_client.get("/api/v2/scan/verdict/4000123456789/memory", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["decision"]["decision"] == "WAIT"

@pytest.mark.asyncio
async def test_save_memory_idempotency_success(db_clean, app_client, registered_supabase_user, seeded_snapshot):
    headers = {"Authorization": f"Bearer {registered_supabase_user['access_token']}"}
    payload = {
        "decision": "BUY",
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "my-client-key-2"
    }
    resp1 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload, headers=headers)
    assert resp1.status_code == 200

    resp2 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload, headers=headers)
    assert resp2.status_code == 200
    assert resp1.json()["id"] == resp2.json()["id"]

@pytest.mark.asyncio
async def test_save_memory_idempotency_conflict(db_clean, app_client, registered_supabase_user, seeded_snapshot):
    headers = {"Authorization": f"Bearer {registered_supabase_user['access_token']}"}
    payload1 = {
        "decision": "BUY",
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "my-client-key-3"
    }
    await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload1, headers=headers)

    payload2 = {
        "decision": "SKIP",
        "label_version": 1,
        "content_fingerprint": "test-fingerprint-123",
        "idempotency_key": "my-client-key-3"
    }
    resp2 = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload2, headers=headers)
    assert resp2.status_code == 409

@pytest.mark.asyncio
async def test_save_memory_stale_identity(db_clean, app_client, registered_supabase_user, seeded_snapshot):
    headers = {"Authorization": f"Bearer {registered_supabase_user['access_token']}"}
    payload = {
        "decision": "BUY",
        "label_version": 1,
        "content_fingerprint": "wrong-fingerprint",
        "idempotency_key": "my-client-key-4"
    }
    resp = await app_client.post("/api/v2/scan/verdict/4000123456789/memory", json=payload, headers=headers)
    assert resp.status_code == 409
