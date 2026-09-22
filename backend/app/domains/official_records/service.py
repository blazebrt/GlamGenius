from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from . import change_evidence, change_projection
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
    by_external_id = {row.external_record_id: row for row in rows}
    matched = resolve_matches(facts, material)
    # Step 12B is attached only to records that deterministically resolved to
    # this exact pack. An unresolved or ambiguous candidate set publishes
    # nothing at all, so there is nothing for a change claim to be about.
    return [
        {
            **record,
            "match_state": "matched",
            "regulatory_change": await regulatory_change_for_record(
                session, by_external_id[record["recall_id"]],
            ),
        }
        for record in matched
    ]


class _LedgerPair(NamedTuple):
    """One record as its ledger proves it: head revision and explicit predecessor."""

    record: OfficialRecord
    current: OfficialRecordRevision
    previous: OfficialRecordRevision | None


def _ledger_aggregate(expression):
    """A per-record aggregate over the revision ledger, correlated to the record row."""
    return (
        select(expression)
        .where(OfficialRecordRevision.record_id == OfficialRecord.id)
        .correlate(OfficialRecord)
        .scalar_subquery()
    )


async def _revision_pair(session: AsyncSession, record: OfficialRecord) -> _LedgerPair:
    """The ledger's own head and its explicit predecessor, checked against the pointer.

    ``OfficialRecord.latest_revision`` is a *claim* about the immutable ledger,
    and Step 12B exists to check that claim, so it is never used to *find* the
    current revision. The ledger establishes its own head, and then:

    * the ledger must be exactly ``1, 2, … N`` — no missing first revision, no
      gap, no hidden extra. ``(record_id, revision_number)`` is unique, so
      ``count`` distinct numbers whose minimum is 1 and maximum is ``count``
      are exactly that sequence;
    * the canonical pointer must equal that head, in both directions: a
      pointer behind the ledger would hide the newest observation, and a
      pointer ahead of it names an observation that does not exist.

    Nothing is repaired. Each failure raises
    :class:`~change_projection.RegulatoryHistoryInvariantError` with a stable
    reason, and the caller fails closed.

    The record row and the ledger aggregate are read in **one statement**, so
    they come from one snapshot. Read separately, an import committing between
    the two reads would look exactly like a corrupt pointer and raise a false
    integrity alarm. ``populate_existing`` refreshes the record's canonical
    content from that same snapshot, because the projection compares it with
    the head revision. Existing revisions are immutable and a concurrent import
    only ever *adds* ``N + 1``, so loading ``N`` and ``N − 1`` afterwards by
    number stays consistent with the aggregate.
    """
    invariant = change_projection.RegulatoryHistoryInvariantError
    snapshot = (await session.execute(
        select(
            OfficialRecord,
            _ledger_aggregate(func.min(OfficialRecordRevision.revision_number)),
            _ledger_aggregate(func.max(OfficialRecordRevision.revision_number)),
            _ledger_aggregate(func.count(OfficialRecordRevision.id)),
        )
        .where(OfficialRecord.id == record.id)
        .execution_options(populate_existing=True)
    )).one_or_none()
    if snapshot is None:
        raise invariant("canonical_record_missing")
    fresh, low, high, count = snapshot
    if not count:
        raise invariant("record_has_no_revision")
    if low != 1 or high != count:
        raise invariant("revision_ledger_sequence_invalid")
    if high != fresh.latest_revision:
        raise invariant("canonical_latest_revision_pointer_mismatch")
    by_number = {
        row.revision_number: row
        for row in (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.record_id == fresh.id,
            OfficialRecordRevision.revision_number.in_({high, high - 1}),
        ))).scalars().all()
    }
    return _LedgerPair(record=fresh, current=by_number[high], previous=by_number.get(high - 1))


async def _validated_regulatory_history(
    session: AsyncSession, record: OfficialRecord,
) -> tuple[OfficialRecord, OfficialRecordRevision, OfficialRecordRevision | None, change_projection.RegulatoryChangeProjection]:
    """Run the one complete Step 12B integrity proof for an official history.

    A structurally consecutive ledger is not a trustworthy history on its own:
    the revisions must also be bound to successful authoritative source fetches,
    their canonical payloads and the current record. Callers may make different
    publication decisions from this proof, but cannot use weaker history facts.
    """
    pair = await _revision_pair(session, record)
    fetches = {
        row.id: row
        for row in (await session.execute(select(OfficialSourceFetch).where(
            OfficialSourceFetch.id.in_(
                {row.source_fetch_id for row in (pair.current, pair.previous) if row is not None}
            )
        ))).scalars().all()
    }
    current_fetch = fetches.get(pair.current.source_fetch_id)
    previous_fetch = fetches.get(pair.previous.source_fetch_id) if pair.previous is not None else None
    if current_fetch is None or (pair.previous is not None and previous_fetch is None):
        raise change_projection.RegulatoryHistoryInvariantError("revision_fetch_missing")
    projection = change_projection.project_regulatory_change(
        record=pair.record, current=pair.current, previous=pair.previous,
        current_fetch=current_fetch, previous_fetch=previous_fetch,
    )
    return pair.record, pair.current, pair.previous, projection


async def regulatory_change_for_record(
    session: AsyncSession, record: OfficialRecord,
) -> dict[str, Any]:
    """Step 12B: what this official record's own authority now says differently.

    Two authorities run here, in this order, and the order is the design:

    1. :func:`change_projection.project_regulatory_change` decides whether the
       stored revision history is valid and what the two observations say
       differently. It runs **first, and always** — a corrupt ledger must be
       detected and logged whether or not anybody was ever going to be told.
    2. :func:`change_evidence.regulatory_change_is_publishable` decides,
       separately, whether a valid answer may leave the server.

    A claim is published only when both permit it. Integrity alone is not
    permission to speak; evidence alone can never make a corrupt history
    publishable.

    The return is always the same governed envelope shape. Every way of being
    unable to state a comparison — corrupt history, missing history, or a
    correct comparison no openable source backs — looks identical from outside,
    so the envelope itself cannot be read as a diagnosis of our storage.
    """
    # First authority: integrity. Unconditional, and it begins with the ledger
    # proving its own head rather than trusting the canonical pointer.
    projection: change_projection.RegulatoryChangeProjection | None = None
    current: OfficialRecordRevision | None = None
    previous: OfficialRecordRevision | None = None
    try:
        record, current, previous, projection = await _validated_regulatory_history(session, record)
    except change_projection.RegulatoryHistoryInvariantError as broken:
        # An addition may not take the page down with it. The official record
        # itself was established without this envelope and is still shown; the
        # broken invariant is named to the log and to nobody else, and never
        # with a database identifier beside it.
        logger.warning(
            "regulatory_history_invariant_failed authority=%s record_type=%s reason=%s",
            record.authority, record.record_type, broken.reason,
        )
    # Second authority, asked separately: may a valid result be published?
    publishable = change_evidence.regulatory_change_is_publishable(
        record=record, current=current, previous=previous,
    )
    if projection is None or not publishable:
        return change_projection.UNAVAILABLE_PROJECTION.as_payload()
    return projection.as_payload()


async def validated_revision_heads(
    session: AsyncSession, external_record_ids: list[str],
) -> dict[str, int | None]:
    """Each official record's ledger head, as this module validates it — bookkeeping only.

    Step 12C's baseline needs to remember *which* revision of a record was
    current when a customer began watching, so that a later revision can be
    told apart from one that already existed. That answer belongs to this
    module: the head comes from :func:`_validated_regulatory_history`, which
    proves both the ``1..N`` ledger shape and every Step 12B provenance and
    payload invariant, and is ``None`` whenever either fails.

    Nothing here is publishable and nothing is published. The number is an
    opaque cursor for set membership; whether a change may be *stated* is still
    decided only by :func:`regulatory_change_for_record` and its evidence gate.
    The invariant reason is not logged again here, because every caller also
    builds the official-records envelope for the same records, and that path
    has already named it to the log.
    """
    if not external_record_ids:
        return {}
    rows = (await session.execute(select(OfficialRecord).where(
        OfficialRecord.authority == AUTHORITY_FSSAI_FOSCOS,
        OfficialRecord.record_type == RECORD_TYPE_FOOD_RECALL,
        OfficialRecord.external_record_id.in_(set(external_record_ids)),
    ))).scalars().all()
    heads: dict[str, int | None] = dict.fromkeys(external_record_ids)
    for row in rows:
        try:
            _record, current, _previous, _projection = await _validated_regulatory_history(session, row)
        except change_projection.RegulatoryHistoryInvariantError:
            continue
        heads[row.external_record_id] = current.revision_number
    return heads


async def official_records_envelope(session: AsyncSession, facts: dict[str, Any] | None) -> dict[str, Any]:
    latest_fetch = await latest_successful_source_check(session)
    return {"authority": "FSSAI / FoSCoS", "record_type": RECORD_TYPE_FOOD_RECALL,
        "source_url": SOURCE_URL,
        "last_successful_check_at": latest_fetch.source_checked_at.isoformat() if latest_fetch else None,
        "records": await recalls_for_pack(session, facts, latest_fetch) if facts else []}
