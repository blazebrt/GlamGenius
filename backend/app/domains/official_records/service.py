from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import OfficialRecord, OfficialRecordRevision, OfficialSourceFetch
from .source import (
    AUTHORITY_FSSAI_FOSCOS,
    RECORD_TYPE_FOOD_RECALL,
    SOURCE_ADAPTER_VERSION,
    SOURCE_URL,
    parse_recall_rows,
    stable_content_hash,
)


async def ingest_recall_export(
    session: AsyncSession,
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    fetched_at: datetime | None = None,
    http_status: int | None = 200,
) -> OfficialSourceFetch:
    """Ingest a reviewed official export/fixture idempotently."""
    now = fetched_at or datetime.now(UTC)
    rows = parse_recall_rows(payload)
    raw_rows = [{key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()} for row in rows]
    fetch = OfficialSourceFetch(
        authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
        source_url=SOURCE_URL, adapter_version=SOURCE_ADAPTER_VERSION,
        fetched_at=now, status="succeeded", http_status=http_status,
        content_hash=stable_content_hash({"rows": raw_rows}), raw_payload={"rows": raw_rows},
    )
    session.add(fetch)
    for row in rows:
        serial_row = {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()}
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.authority == AUTHORITY_FSSAI_FOSCOS,
            OfficialRecord.record_type == RECORD_TYPE_FOOD_RECALL,
            OfficialRecord.external_record_id == row["external_record_id"],
        ))).scalar_one_or_none()
        content_hash = stable_content_hash(row)
        if record is None:
            record = OfficialRecord(
                authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
                external_record_id=row["external_record_id"], source_url=SOURCE_URL,
                first_seen_at=now, last_seen_at=now, latest_revision=1,
                **{key: value for key, value in row.items() if key != "external_record_id"},
            )
            session.add(record)
            await session.flush()
            session.add(OfficialRecordRevision(
                record_id=record.id, revision_number=1, observed_at=now,
                content_hash=content_hash, payload=serial_row,
            ))
        else:
            record.last_seen_at = now
            if record.latest_revision:
                latest = (await session.execute(select(OfficialRecordRevision).where(
                    OfficialRecordRevision.record_id == record.id,
                    OfficialRecordRevision.revision_number == record.latest_revision,
                ))).scalar_one_or_none()
                if latest is None or latest.content_hash != content_hash:
                    record.latest_revision += 1
                    for key, value in row.items():
                        if key != "external_record_id":
                            setattr(record, key, value)
                    session.add(OfficialRecordRevision(
                        record_id=record.id, revision_number=record.latest_revision,
                        observed_at=now, content_hash=content_hash, payload=serial_row,
                    ))
    return fetch


async def record_fetch_failure(
    session: AsyncSession,
    *,
    fetched_at: datetime | None = None,
    http_status: int | None = None,
    error_code: str = "source_unavailable",
) -> OfficialSourceFetch:
    """Keep a failure ledger row without changing the last good records."""
    fetch = OfficialSourceFetch(
        authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
        source_url=SOURCE_URL, adapter_version=SOURCE_ADAPTER_VERSION,
        fetched_at=fetched_at or datetime.now(UTC), status="failed",
        http_status=http_status, error_code=error_code,
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
        material = {
            "recall_id": row.external_record_id, "fbo_name": row.fbo_name,
            "brand_name": row.brand_name, "product_name": row.product_name,
            "batch_lot": row.batch_lot, "licence": row.licence,
            "reason": row.reason, "recall_status": row.recall_status,
            "recall_start_date": row.recall_start_date.isoformat() if row.recall_start_date else None,
            "recall_termination_date": row.recall_termination_date.isoformat() if row.recall_termination_date else None,
            "nature_of_recall": row.nature_of_recall, "source_url": row.source_url,
            "source_last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        }
        state = match_recall(facts, material)
        if state == "matched":
            material["match_state"] = state
            result.append(material)
    return result


async def official_records_envelope(session: AsyncSession, facts: dict[str, Any] | None) -> dict[str, Any]:
    """Public provenance envelope. A failed read never advances freshness."""
    last_success = await session.scalar(select(OfficialSourceFetch.fetched_at).where(
        OfficialSourceFetch.authority == AUTHORITY_FSSAI_FOSCOS,
        OfficialSourceFetch.record_type == RECORD_TYPE_FOOD_RECALL,
        OfficialSourceFetch.status == "succeeded",
    ).order_by(OfficialSourceFetch.fetched_at.desc()).limit(1))
    return {
        "authority": "FSSAI / FoSCoS", "record_type": RECORD_TYPE_FOOD_RECALL,
        "source_url": SOURCE_URL,
        "last_successful_check_at": last_success.isoformat() if last_success else None,
        "records": await recalls_for_pack(session, facts) if facts else [],
    }
