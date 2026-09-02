from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import OfficialRecord, OfficialRecordRevision, OfficialSourceFetch
from .source import (
    AUTHORITY_FSSAI_FOSCOS,
    ERROR_CONFLICTING_SOURCE_CHECK,
    ERROR_DUPLICATE_SOURCE_CHECK,
    ERROR_OUT_OF_ORDER_SOURCE_CHECK,
    RECORD_TYPE_FOOD_RECALL,
    SOURCE_ADAPTER_VERSION,
    SOURCE_FORMAT,
    SOURCE_URL,
    SourceError,
    parse_recall_xlsx,
    stable_content_hash,
)

#: One fixed advisory-lock name for this official source. Two imports of the
#: same register must not interleave: without it both could read the same
#: "latest successful check", both pass the chronology guard, and both mutate
#: canonical records — which would make "source time only ever moves forward" a
#: claim the code does not actually keep.
SOURCE_LOCK_NAME = f"official_source:{AUTHORITY_FSSAI_FOSCOS}:{RECORD_TYPE_FOOD_RECALL}"


async def lock_official_source(session: AsyncSession) -> None:
    """Serialize official imports for this source across processes and sessions.

    The lock is transaction-scoped, so PostgreSQL releases it on commit or
    rollback. It is deliberately not a Python lock: a second uvicorn worker, a
    second operator, or a cron run on another host would not see one.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), {"name": SOURCE_LOCK_NAME},
    )


async def latest_successful_source_check(session: AsyncSession) -> OfficialSourceFetch | None:
    """The newest official artifact we have accepted, by the operator's source time."""
    return (await session.execute(
        select(OfficialSourceFetch).where(
            OfficialSourceFetch.authority == AUTHORITY_FSSAI_FOSCOS,
            OfficialSourceFetch.record_type == RECORD_TYPE_FOOD_RECALL,
            OfficialSourceFetch.status == "succeeded",
        ).order_by(desc(OfficialSourceFetch.source_checked_at), desc(OfficialSourceFetch.created_at)).limit(1)
    )).scalars().first()


def _guard_source_order(
    latest: OfficialSourceFetch | None, source_checked_at: datetime, source_file_sha256: str,
) -> None:
    """Official source time only ever moves forward.

    Replaying an older export would overwrite canonical status and reason with
    content the register has already superseded, bump ``latest_revision`` with
    stale text and pull ``last_seen_at`` backwards while
    ``last_successful_check_at`` still reported the newer check. Equal source
    times are refused outright — V1 picks no winner between two artifacts that
    claim the same instant, whether or not their bytes agree.
    """
    if latest is None:
        return
    if source_checked_at < latest.source_checked_at:
        raise SourceError(ERROR_OUT_OF_ORDER_SOURCE_CHECK, source_file_sha256=source_file_sha256)
    if source_checked_at == latest.source_checked_at:
        raise SourceError(
            ERROR_DUPLICATE_SOURCE_CHECK
            if source_file_sha256 == latest.source_file_sha256
            else ERROR_CONFLICTING_SOURCE_CHECK,
            source_file_sha256=source_file_sha256,
        )


async def ingest_recall_xlsx(
    session: AsyncSession, path: Path, *, source_checked_at: datetime,
) -> tuple[OfficialSourceFetch, dict[str, int]]:
    """Insert one manually acquired public FoSCoS XLSX artifact atomically."""
    rows, source_file_sha256 = parse_recall_xlsx(path)
    # Validation first — a malformed workbook should not hold the lock. Then the
    # lock, and only then the read the chronology guard depends on, so a
    # concurrent import cannot slip between that read and the mutation below.
    await lock_official_source(session)
    # Both gates run before any canonical mutation, and before the successful
    # fetch row exists, so a refused artifact leaves the register untouched.
    _guard_source_order(await latest_successful_source_check(session), source_checked_at, source_file_sha256)
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
        # Observation history and semantic revision history are different things:
        # seeing the same record again always advances last_seen, and only
        # changed content earns a revision.
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
    original_filename: str | None = None, source_file_sha256: str | None = None,
    source_format: str | None = None,
) -> OfficialSourceFetch:
    """Write the failure ledger row. It never advances successful freshness."""
    fetch = OfficialSourceFetch(
        authority=AUTHORITY_FSSAI_FOSCOS, record_type=RECORD_TYPE_FOOD_RECALL,
        source_url=SOURCE_URL, adapter_version=SOURCE_ADAPTER_VERSION,
        source_checked_at=source_checked_at, status="failed", error_code=error_code,
        original_filename=original_filename[:256] if original_filename else None,
        source_file_sha256=source_file_sha256, source_format=source_format,
    )
    session.add(fetch)
    return fetch


async def record_source_error(
    session: AsyncSession, error: SourceError, *, source_checked_at: datetime, path: Path,
) -> OfficialSourceFetch:
    """Keep a rejected official artifact auditable: whatever provenance survived, plus a closed code."""
    return await record_fetch_failure(
        session, source_checked_at=source_checked_at, error_code=error.code,
        original_filename=path.name,
        source_file_sha256=error.source_file_sha256,
        source_format=SOURCE_FORMAT if error.source_file_sha256 else None,
    )


async def recalls_for_pack(
    session: AsyncSession, facts: dict[str, Any], latest_fetch: OfficialSourceFetch | None = None,
) -> list[dict[str, Any]]:
    """The official records this confirmed pack resolves to, each stating when it was last seen.

    A record we hold is kept even when a later export omits it, because absence
    from one download proves nothing about withdrawal, correction or resolution.
    That makes per-record observation time part of the contract: "this record was
    last observed on 1 August" is a different sentence from "we last checked the
    register on 4 August", and the screen must be able to say the first one.
    """
    rows = (await session.execute(select(OfficialRecord).where(
        OfficialRecord.authority == AUTHORITY_FSSAI_FOSCOS,
        OfficialRecord.record_type == RECORD_TYPE_FOOD_RECALL,
    ).order_by(desc(OfficialRecord.recall_start_date).nulls_last(), OfficialRecord.external_record_id.asc()))).scalars().all()
    from .matching import resolve_matches
    material = [
        {"recall_id": row.external_record_id, "fbo_name": row.fbo_name, "brand_name": row.brand_name,
            "product_name": row.product_name, "batch_lot": row.batch_lot, "licence": row.licence,
            "reason": row.reason, "recall_status": row.recall_status,
            "recall_start_date": row.recall_start_date.isoformat() if row.recall_start_date else None,
            "recall_termination_date": row.recall_termination_date.isoformat() if row.recall_termination_date else None,
            "nature_of_recall": row.nature_of_recall, "source_url": row.source_url,
            # Observation provenance for this record alone. Never a conclusion:
            # "not seen in the latest export" is not "withdrawn" or "resolved".
            "source_last_seen_at": row.last_seen_at.isoformat(),
            "seen_in_latest_successful_check": bool(
                latest_fetch is not None and row.last_seen_fetch_id == latest_fetch.id
            )}
        for row in rows
    ]
    return [{**record, "match_state": "matched"} for record in resolve_matches(facts, material)]


async def official_records_envelope(session: AsyncSession, facts: dict[str, Any] | None) -> dict[str, Any]:
    latest_fetch = await latest_successful_source_check(session)
    return {"authority": "FSSAI / FoSCoS", "record_type": RECORD_TYPE_FOOD_RECALL,
        "source_url": SOURCE_URL,
        "last_successful_check_at": latest_fetch.source_checked_at.isoformat() if latest_fetch else None,
        "records": await recalls_for_pack(session, facts, latest_fetch) if facts else []}
