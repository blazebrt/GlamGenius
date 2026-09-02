from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.domains.official_records import service
from app.domains.official_records.matching import match_recall
from app.domains.official_records.models import OfficialRecord, OfficialRecordRevision
from app.domains.official_records.source import HEADERS, SOURCE_ADAPTER_VERSION, parse_recall_xlsx
from app.shared.database.sql import get_sessionmaker
from openpyxl import Workbook
from sqlalchemy import select

LICENCE = "10012345678901"
FIXTURE = Path(__file__).parent / "fixtures" / "fssai_food_recall_v1.xlsx"


def make_export(path, *, status: str = "Initiated", batch: str = "B-123"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "data"
    sheet.append(HEADERS)
    sheet.append([1, 901, " Synthetic Foods ", "Synthetic Brand", batch, "Synthetic cereal", "Synthetic reason", "01-08-2026", status, "NA", LICENCE, "Central License", "Initiated by Authority"])
    workbook.save(path)


def pack(**changes):
    return {"fssai_licence": LICENCE, "batch_number": "B-123", "brand": "Synthetic Brand", "product_name": "Synthetic cereal", **changes}


def test_public_xlsx_contract_preserves_identifiers_dates_and_original_bytes():
    rows, digest = parse_recall_xlsx(FIXTURE)
    assert digest == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert SOURCE_ADAPTER_VERSION.endswith("xlsx.v1")
    assert rows[0]["fbo_name"] == "Synthetic Foods"
    assert rows[0]["licence"] == LICENCE
    assert rows[0]["batch_lot"] == "B-123"
    assert rows[0]["recall_start_date"].isoformat() == "2026-08-01"
    assert rows[0]["recall_termination_date"] is None


def test_exact_match_rejects_source_placeholders_and_retains_real_short_batches():
    record = {"licence": LICENCE, "batch_lot": "B-123", "brand_name": "Synthetic Brand", "product_name": "Synthetic cereal"}
    assert match_recall(pack(), record) == "matched"
    for placeholder in ("NA", "nil", "other", "00", "0"):
        assert match_recall(pack(batch_number=placeholder), {**record, "batch_lot": placeholder}) == "not_matched"
    assert match_recall(pack(batch_number="C"), {**record, "batch_lot": "c"}) == "matched"
    assert match_recall(pack(batch_number="B-123"), {**record, "batch_lot": "B 123"}) == "identity_mismatch"


@pytest.mark.anyio
async def test_source_checked_lineage_revisions_and_reobservation(tmp_path, db_clean):
    first_path = tmp_path / "first.xlsx"
    second_path = tmp_path / "second.xlsx"
    make_export(first_path)
    make_export(second_path, status="Completed")
    first = datetime(2026, 8, 1, tzinfo=UTC)
    second = datetime(2026, 8, 4, tzinfo=UTC)
    factory = get_sessionmaker()
    async with factory() as session:
        fetch_one, created = await service.ingest_recall_xlsx(session, first_path, source_checked_at=first)
        await session.commit()
        fetch_two, revised = await service.ingest_recall_xlsx(session, second_path, source_checked_at=second)
        await session.commit()
        record = (await session.execute(select(OfficialRecord))).scalar_one()
        revisions = (await session.execute(select(OfficialRecordRevision).order_by(OfficialRecordRevision.revision_number))).scalars().all()
        assert created["records_created"] == 1 and revised["records_revised"] == 1
        assert [revision.source_fetch_id for revision in revisions] == [fetch_one.id, fetch_two.id]
        assert record.last_seen_fetch_id == fetch_two.id
        envelope = await service.official_records_envelope(session, pack())
        assert envelope["last_successful_check_at"] == second.isoformat()
