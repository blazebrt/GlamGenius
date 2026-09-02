"""PostgreSQL data proof for the Step-3 observed-label migration."""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.domains.product import service
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.shared.database import sql
from sqlalchemy import func, select, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRE_STEP3_REVISION = "v2w3x4y5z6"
BARCODE = "8906666666666"

A = {
    "product_name": "Legacy oats",
    "brand": "Local Foods",
    "ingredients_text": "oats, salt",
    "nutrition_per_100g": {"energy_kcal": "370", "sugars_g": "1"},
    "batch_number": "A-observation",
}
B = {
    "product_name": "Legacy oats",
    "brand": "Local Foods",
    "ingredients_text": "oats, sugar, salt",
    "nutrition_per_100g": {"energy_kcal": "380", "sugars_g": "4"},
    "batch_number": "B-observation",
}


async def _alembic(*arguments: str) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *arguments,
        cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode(errors="replace")


async def _seed_pre_migration_observations() -> list[uuid.UUID]:
    facts_sequence = (A, {**A, "batch_number": "A-repeat"}, B, B, A)
    event_ids: list[uuid.UUID] = []
    now = datetime(2026, 8, 1, tzinfo=UTC)
    async with sql.get_engine().begin() as connection:
        for index, facts in enumerate(facts_sequence):
            event_id = uuid.uuid4()
            snapshot_id = uuid.uuid4()
            event_ids.append(event_id)
            observed_at = now + timedelta(seconds=index)
            await connection.execute(
                text("""
                    INSERT INTO scan_events (
                        id, barcode, outcome, client_scan_id, scanned_at,
                        queued_offline, label_facts, created_at, updated_at
                    ) VALUES (
                        :id, :barcode, 'label_captured', :client_scan_id,
                        :observed_at, false, CAST(:facts AS jsonb),
                        :observed_at, :observed_at
                    )
                """),
                {
                    "id": event_id,
                    "barcode": BARCODE,
                    "client_scan_id": f"legacy-{index}-{uuid.uuid4().hex}",
                    "observed_at": observed_at,
                    "facts": json.dumps(facts),
                },
            )
            await connection.execute(
                text("""
                    INSERT INTO product_label_snapshots (
                        id, barcode, scan_event_id, facts, confidence,
                        created_at, updated_at
                    ) VALUES (
                        :id, :barcode, :scan_event_id, CAST(:facts AS jsonb),
                        'unverified', :observed_at, :observed_at
                    )
                """),
                {
                    "id": snapshot_id,
                    "barcode": BARCODE,
                    "scan_event_id": event_id,
                    "facts": json.dumps(facts),
                    "observed_at": observed_at,
                },
            )
    return event_ids


async def _new_event(facts: dict) -> uuid.UUID:
    factory = sql.get_sessionmaker()
    async with factory() as session:
        event = ScanEvent(
            barcode=BARCODE,
            outcome=service.OUTCOME_LABEL,
            client_scan_id=f"post-migration-{uuid.uuid4().hex}",
            scanned_at=datetime.now(UTC),
            label_facts=facts,
        )
        session.add(event)
        await session.commit()
        return event.id


@pytest.mark.asyncio
async def test_legacy_observations_upgrade_to_consecutive_semantic_versions(db_clean):
    await sql.dispose_engine()
    await _alembic("downgrade", PRE_STEP3_REVISION)
    upgraded = False
    try:
        original_event_ids = await _seed_pre_migration_observations()
        await sql.dispose_engine()
        await _alembic("upgrade", "head")
        upgraded = True

        factory = sql.get_sessionmaker()
        async with factory() as session:
            snapshots = (await session.execute(
                select(LabelSnapshot)
                .where(LabelSnapshot.barcode == BARCODE)
                .order_by(LabelSnapshot.version_number)
            )).scalars().all()
            event_count = await session.scalar(
                select(func.count(ScanEvent.id)).where(ScanEvent.id.in_(original_event_ids))
            )
            latest = await service.latest_label_snapshot(session, BARCODE)

        assert [row.version_number for row in snapshots] == [1, 2, 3]
        assert snapshots[0].content_fingerprint == snapshots[2].content_fingerprint
        assert snapshots[0].content_fingerprint != snapshots[1].content_fingerprint
        assert snapshots[0].previous_snapshot_id is None
        assert snapshots[1].previous_snapshot_id == snapshots[0].id
        assert snapshots[2].previous_snapshot_id == snapshots[1].id
        assert snapshots[1].changed_fields == ["ingredients", "nutrition"]
        assert snapshots[2].changed_fields == ["ingredients", "nutrition"]
        assert [row.completeness for row in snapshots] == [
            "incomplete_for_grading",
            "incomplete_for_grading",
            "incomplete_for_grading",
        ]
        assert event_count == 5
        assert latest is not None and latest.id == snapshots[2].id
        assert snapshots[0].content_fingerprint == service.label_content_fingerprint(
            snapshots[0].facts
        )

        same_event_id = await _new_event(A)
        async with factory() as session:
            same = await service.store_label_snapshot(
                session,
                barcode=BARCODE,
                facts=A,
                device_id=None,
                scan_event_id=same_event_id,
            )
            await session.commit()
        assert same.version_number == 3

        changed = {**A, "nutrition_per_100g": {"energy_kcal": "370", "sugars_g": "2"}}
        changed_event_id = await _new_event(changed)
        async with factory() as session:
            fourth = await service.store_label_snapshot(
                session,
                barcode=BARCODE,
                facts=changed,
                device_id=None,
                scan_event_id=changed_event_id,
            )
            await session.commit()
        assert fourth.version_number == 4
        assert fourth.previous_snapshot_id == snapshots[2].id
    finally:
        await sql.dispose_engine()
        if not upgraded:
            await _alembic("upgrade", "head")
