from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import OfficialRecord, OfficialRecordRevision, OfficialSourceFetch
from .source import (
    AUTHORITY_FSSAI_FOSCOS,
    RECORD_TYPE_FOOD_RECALL,
    SOURCE_ADAPTER_VERSION,
    SOURCE_FORMAT,
    SOURCE_URL,
    parse_recall_xlsx,
    stable_content_hash,
)


async def ingest_recall_xlsx(
    session: AsyncSession, path: Path, *, source_checked_at: datetime,
) -> tuple[OfficialSourceFetch, dict[str, int]]:
    """Insert one manually acquired public FoSCoS XLSX artifact atomically."""
    rows, source_file_sha256 = parse_recall_xlsx(path)
    fetch = OfficialSourceFetch(
        authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
        source_url=SOURCE_URL, adapter_version=SOURCE_ADAPTER_VERSION,
        source_checked_at=source_checked_at, status="succeeded",
        source_file_sha256=source_file_sha256, source_format=SOURCE_FORMAT,
        row_count=len(rows), original_filename=path.name[:256],
    )
    session.add(fetch)
    await session.flush()
    counts = {"records_created": 0, "records_revised": 0, "records_unchanged": 0}
    for row in rows:
        serial = {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()}
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.authority == AUTHORITY_FSSAI_FOSCOS,
            OfficialRecord.record_type == RECORD_TYPE_FOOD_RECALL,
            OfficialRecord.external_record_id == row["external_record_id"],
        ))).scalar_one_or_none()
        revision_hash = stable_content_hash(row)
        if record is None:
            record = OfficialRecord(
                authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
                external_record_id=row["external_record_id"], source_url=SOURCE_URL,
                first_seen_at=source_checked_at, last_seen_at=source_checked_at,
                last_seen_fetch_id=fetch.id, latest_revision=1,
                **{key: value for key, value in row.items() if key != "external_record_id"},
            )
            session.add(record)
            await session.flush()
            session.add(OfficialRecordRevision(
                record_id=record.id, source_fetch_id=fetch.id, revision_number=1,
                observed_at=source_checked_at, content_hash=revision_hash, payload=serial,
            ))
            counts["records_created"] += 1
            continue
        record.last_seen_at = source_checked_at
        record.last_seen_fetch_id = fetch.id
        latest = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.record_id == record.id,
            OfficialRecordRevision.revision_number == record.latest_revision,
        ))).scalar_one()
        if latest.content_hash == revision_hash:
            counts["records_unchanged"] += 1
            continue
        record.latest_revision += 1
        for key, value in row.items():
            if key != "external_record_id":
                setattr(record, key, value)
        session.add(OfficialRecordRevision(
            record_id=record.id, source_fetch_id=fetch.id, revision_number=record.latest_revision,
            observed_at=source_checked_at, content_hash=revision_hash, payload=serial,
        ))
        counts["records_revised"] += 1
    return fetch, counts


async def record_fetch_failure(
    session: AsyncSession, *, source_checked_at: datetime, error_code: str,
) -> OfficialSourceFetch:
    fetch = OfficialSourceFetch(
        authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
        source_url=SOURCE_URL, adapter_version=SOURCE_ADAPTER_VERSION,
        source_checked_at=source_checked_at, status="failed", error_code=error_code,
    )
    session.add(fetch)
    return fetch


async def recalls_for_pack(session: AsyncSession, facts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (await session.execute(select(OfficialRecord).where(
        OfficialRecord.authority == AUTHORITY_FSSAI_FOSCOS,
        OfficialRecord.record_type == RECORD_TYPE_FOOD_RECALL,
    ).order_by(desc(OfficialRecord.recall_start_date).nulls_last(), OfficialRecord.external_record_id.asc()))).scalars().all()
    from .matching import match_recall
    result = []
    for row in rows:
        material = {"recall_id": row.external_record_id, "fbo_name": row.fbo_name, "brand_name": row.brand_name,
            "product_name": row.product_name, "batch_lot": row.batch_lot, "licence": row.licence,
            "reason": row.reason, "recall_status": row.recall_status,
            "recall_start_date": row.recall_start_date.isoformat() if row.recall_start_date else None,
            "recall_termination_date": row.recall_termination_date.isoformat() if row.recall_termination_date else None,
            "nature_of_recall": row.nature_of_recall, "source_url": row.source_url}
        if match_recall(facts, material) == "matched":
            material["match_state"] = "matched"
            result.append(material)
    return result


async def official_records_envelope(session: AsyncSession, facts: dict[str, Any] | None) -> dict[str, Any]:
    last_success = await session.scalar(select(OfficialSourceFetch.source_checked_at).where(
        OfficialSourceFetch.authority == AUTHORITY_FSSAI_FOSCOS,
        OfficialSourceFetch.record_type == RECORD_TYPE_FOOD_RECALL,
        OfficialSourceFetch.status == "succeeded",
    ).order_by(OfficialSourceFetch.source_checked_at.desc()).limit(1))
    return {"authority": "FSSAI / FoSCoS", "record_type": RECORD_TYPE_FOOD_RECALL,
        "source_url": SOURCE_URL, "last_successful_check_at": last_success.isoformat() if last_success else None,
        "records": await recalls_for_pack(session, facts) if facts else []}
