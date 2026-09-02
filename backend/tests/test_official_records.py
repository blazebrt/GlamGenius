from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.domains.official_records import service
from app.domains.official_records.matching import match_recall
from app.domains.official_records.models import (
    OfficialRecord,
    OfficialRecordRevision,
    OfficialSourceFetch,
)
from app.domains.official_records.source import SOURCE_ADAPTER_VERSION, SOURCE_URL, parse_recall_rows
from app.shared.database.sql import get_sessionmaker

FIXTURE = Path(__file__).parent / "fixtures" / "fssai_food_recall_v1.json"
LICENCE = "10012345678901"


def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def record() -> dict:
    return parse_recall_rows(payload())[0]


def pack(**changes) -> dict:
    return {"fssai_licence": LICENCE, "batch_number": "LOT-2026-A", "brand": "Example Brand", "product_name": "Example Cereal", **changes}


def test_adapter_uses_committed_official_export_shape():
    row = record()
    assert SOURCE_URL == "https://foscos.fssai.gov.in/food-recall"
    assert SOURCE_ADAPTER_VERSION == "fssai-foscos-food-recall.v1"
    assert row["fbo_name"] == "Example Foods Private Limited"


def test_exact_match_requires_valid_licence_batch_and_compatible_identity():
    source = record()
    assert match_recall(pack(), source) == "matched"
    assert match_recall(pack(batch_number="OTHER"), source) == "identity_mismatch"
    assert match_recall(pack(fssai_licence="10012345678902"), source) == "identity_mismatch"
    assert match_recall(pack(fssai_licence=None), source) == "not_matched"
    assert match_recall(pack(batch_number=None), source) == "not_matched"
    assert match_recall(pack(fssai_licence="123"), {**source, "licence": "123"}) == "not_matched"
    assert match_recall(pack(fssai_licence="11111111111111"), {**source, "licence": "11111111111111"}) == "not_matched"
    assert match_recall(pack(brand="Other Brand"), source) == "identity_mismatch"


@pytest.mark.anyio
async def test_ingestion_is_idempotent_revisioned_and_preserves_fbo(db_clean):
    factory = get_sessionmaker()
    first = datetime(2026, 8, 1, tzinfo=UTC)
    second = datetime(2026, 8, 2, tzinfo=UTC)
    async with factory() as session:
        await service.ingest_recall_export(session, payload(), fetched_at=first)
        await session.commit()
    async with factory() as session:
        rows = (await session.execute(select(OfficialRecord))).scalars().all()
        revisions = (await session.execute(select(OfficialRecordRevision))).scalars().all()
        assert len(rows) == len(revisions) == 1
        assert rows[0].fbo_name == "Example Foods Private Limited"
        assert revisions[0].revision_number == 1
        await service.ingest_recall_export(session, payload(), fetched_at=second)
        await session.commit()
    async with factory() as session:
        row = (await session.execute(select(OfficialRecord))).scalar_one()
        assert row.latest_revision == 1
        assert row.last_seen_at == second


@pytest.mark.anyio
async def test_changes_and_failures_preserve_records_and_success_freshness(db_clean):
    factory = get_sessionmaker()
    first = datetime(2026, 8, 1, tzinfo=UTC)
    changed = datetime(2026, 8, 3, tzinfo=UTC)
    failed = datetime(2026, 8, 4, tzinfo=UTC)
    async with factory() as session:
        await service.ingest_recall_export(session, payload(), fetched_at=first)
        updated = payload(); updated["rows"][0]["recall_status"] = "Terminated"
        await service.ingest_recall_export(session, updated, fetched_at=changed)
        await service.record_fetch_failure(session, fetched_at=failed)
        await session.commit()
    async with factory() as session:
        row = (await session.execute(select(OfficialRecord))).scalar_one()
        revisions = (await session.execute(select(OfficialRecordRevision).order_by(OfficialRecordRevision.revision_number))).scalars().all()
        assert row.recall_status == "Terminated" and row.latest_revision == 2
        assert [revision.revision_number for revision in revisions] == [1, 2]
        envelope = await service.official_records_envelope(session, pack())
        assert envelope["last_successful_check_at"] == changed.isoformat()
        assert len(envelope["records"]) == 1
        assert "id" not in envelope["records"][0]
        assert (await session.execute(select(OfficialSourceFetch).where(OfficialSourceFetch.status == "failed"))).scalar_one()
