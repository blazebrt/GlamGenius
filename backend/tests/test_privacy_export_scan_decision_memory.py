from __future__ import annotations

import uuid

import pytest
from app.domains.privacy.export import build_export
from app.domains.product.models import LabelSnapshot, ScanDecisionEvent, ScanDevice, ScanEvent
from app.shared.database.sql import get_sessionmaker

pytestmark = pytest.mark.asyncio

async def test_export_scan_decision_memory_isolation(db_clean, registered_supabase_user):
    _, user_a = await registered_supabase_user()
    _, user_b = await registered_supabase_user()

    factory = get_sessionmaker()
    async with factory() as session:
        device = ScanDevice(
            id=uuid.uuid4(), device_key=f"export-device-{uuid.uuid4().hex}",
            token_hash="export-token-hash", claimed_by_account_id=user_a,
        )
        session.add(device)
        await session.flush()
        event = ScanEvent(
            id=uuid.uuid4(), device_id=device.id, account_id=user_a,
            barcode="export111", outcome="found_local", client_scan_id=uuid.uuid4().hex,
        )
        session.add(event)
        await session.flush()
        snapshot = LabelSnapshot(
            id=uuid.uuid4(), barcode="export111", device_id=device.id, scan_event_id=event.id,
            facts={}, confidence="unverified", content_fingerprint="f", version_number=1,
            changed_fields=[], completeness="complete_for_grading"
        )
        session.add(snapshot)
        await session.flush()
        
        session.add(ScanDecisionEvent(account_id=user_a, barcode="export111", label_snapshot_id=snapshot.id, label_version=1, content_fingerprint="f", decision="BUY", note="a-note", idempotency_key="ka"))
        session.add(ScanDecisionEvent(account_id=user_b, barcode="export111", label_snapshot_id=snapshot.id, label_version=1, content_fingerprint="f", decision="SKIP", idempotency_key="kb"))
        await session.commit()
    
    async with factory() as session:
        exported = await build_export(session, user_a)
        
    decisions = exported["domains"]["product_scans"]["scan_decision_events"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "BUY"
    assert decisions[0]["note"] == "a-note"
    assert decisions[0]["barcode"] == "export111"
    assert decisions[0]["label_version"] == 1
    assert decisions[0]["content_fingerprint"] == "f"
