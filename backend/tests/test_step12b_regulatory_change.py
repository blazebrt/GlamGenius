"""Step 12B — the deterministic regulatory change authority.

Two authorities, asked in a fixed order, and the order is the whole design:

1. ``change_projection`` decides whether the stored official revision history is
   valid, and what two immutable observations say differently. It runs first,
   and unconditionally.
2. ``change_evidence`` decides, separately, whether a valid answer may leave
   the server.

A claim is published only when both permit it. Integrity alone is not
permission to speak; evidence alone can never make a corrupt ledger
publishable.

No watch, no subscription, no notification, no polling. Those are Step 12C and
are deliberately absent from this module and from everything it touches.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from app.domains.official_records import change_evidence, change_projection
from app.domains.official_records import service as official_records
from app.domains.official_records.change_projection import (
    MATERIAL_FIELDS,
    UNAVAILABLE_PROJECTION,
    RegulatoryChangeStatus,
    RegulatoryHistoryInvariantError,
    project_regulatory_change,
)
from app.domains.official_records.models import (
    OfficialRecord,
    OfficialRecordRevision,
    OfficialSourceFetch,
)
from app.domains.official_records.source import stable_content_hash
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import delete, select

from tests.test_official_records import HEADERS, data_row, make_export
from tests.test_official_records_api import (
    BARCODE,
    BATCH,
    BRAND,
    PRODUCT,
    RECALL_ID,
    SOURCE_CHECKED_AT,
    confirm_label,
    device,  # noqa: F401 - re-exported fixture
    label_facts,
    off_clean,  # noqa: F401 - re-exported fixture
    verdict,
)

LATER = SOURCE_CHECKED_AT + timedelta(days=3)
LATER_STILL = SOURCE_CHECKED_AT + timedelta(days=6)


# ---------------------------------------------------------------------------
# Helpers: every revision below is written through the real importer, so the
# ledger under test is the one production writes.
# ---------------------------------------------------------------------------
async def _ingest(tmp_path: Path, *, checked_at: datetime, **row) -> dict[str, int]:
    path = make_export(
        tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx",
        rows=[data_row(recall_id=int(RECALL_ID), brand=BRAND, product=PRODUCT,
                       batch=BATCH, **row)],
    )
    async with get_sessionmaker()() as session:
        _fetch, counts = await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=checked_at,
        )
        await session.commit()
        return counts


async def _ingest_rows(tmp_path: Path, *, checked_at: datetime, rows: list) -> dict[str, int]:
    path = make_export(tmp_path / f"foscos-{uuid.uuid4().hex}.xlsx", rows=rows, headers=HEADERS)
    async with get_sessionmaker()() as session:
        _fetch, counts = await official_records.ingest_recall_xlsx(
            session, path, source_checked_at=checked_at,
        )
        await session.commit()
        return counts


async def _record(external_record_id: str = RECALL_ID) -> OfficialRecord:
    """The canonical record under test, named explicitly.

    Never "the only record" and never "the newest": a later export may carry
    other recalls, and a helper that quietly picked one of those would be the
    vague latest-matching this authority exists to refuse.
    """
    async with get_sessionmaker()() as session:
        return (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == external_record_id
        ))).scalars().one()


async def _revisions(external_record_id: str = RECALL_ID) -> list[OfficialRecordRevision]:
    """This record's own revision ledger, oldest first."""
    record = await _record(external_record_id)
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(OfficialRecordRevision)
            .where(OfficialRecordRevision.record_id == record.id)
            .order_by(OfficialRecordRevision.revision_number)
        )).scalars().all())


async def _all_revisions() -> list[OfficialRecordRevision]:
    async with get_sessionmaker()() as session:
        return list((await session.execute(
            select(OfficialRecordRevision).order_by(OfficialRecordRevision.revision_number)
        )).scalars().all())


async def _fetch_for(revision: OfficialRecordRevision) -> OfficialSourceFetch:
    async with get_sessionmaker()() as session:
        return (await session.execute(select(OfficialSourceFetch).where(
            OfficialSourceFetch.id == revision.source_fetch_id
        ))).scalars().one()


async def _project():
    """The internal authority over the record's real current/predecessor pair."""
    record = await _record()
    revisions = await _revisions()
    current = revisions[-1]
    previous = revisions[-2] if len(revisions) > 1 else None
    return project_regulatory_change(
        record=record, current=current, previous=previous,
        current_fetch=await _fetch_for(current),
        previous_fetch=await _fetch_for(previous) if previous else None,
    )


async def _matched_change(app_client, device):  # noqa: F811
    body = await verdict(app_client, device)
    records = body["official_records"]["records"]
    assert [row["recall_id"] for row in records] == [RECALL_ID]
    return records[0]["regulatory_change"], body


# ---------------------------------------------------------------------------
# A. First observation is not a change
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_first_official_revision_is_not_a_change(db_clean, tmp_path):
    """Downloading a row for the first time says nothing about when FSSAI published it."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")

    projection = await _project()

    assert projection.status is RegulatoryChangeStatus.FIRST_OBSERVED_RECORD
    assert projection.current_revision == 1
    assert projection.previous_revision is None
    assert projection.changed_fields == ()
    assert projection.changes == ()
    assert projection.exact_identity_changed is False
    # Never the vocabulary of a new regulatory event. The schema's own key
    # names are the contract and are exempt; what is checked is every value
    # the envelope actually states.
    payload = projection.as_payload()
    assert payload["status"] == "first_observed_record"
    rendered = repr([value for key, value in payload.items() if key != "status"])
    for forbidden in ("new_recall", "newly", "changed", "regulator", "first_observed"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# B / C. Real content changes, and only the fields that actually moved
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_b_same_exact_pack_status_change_names_only_that_field(db_clean, tmp_path):
    """Same licence and batch; the register restates one field."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    counts = await _ingest(tmp_path, checked_at=LATER, status="Completed")
    assert counts["records_revised"] == 1

    projection = await _project()

    assert projection.status is RegulatoryChangeStatus.CHANGED
    assert projection.current_revision == 2
    assert projection.previous_revision == 1
    assert projection.changed_fields == ("recall_status",)
    assert projection.changes[0].previous_value == "Initiated"
    assert projection.changes[0].current_value == "Completed"
    # The pack the record is about did not change.
    assert projection.exact_identity_changed is False


@pytest.mark.asyncio
async def test_c_multiple_semantic_fields_change_with_no_metadata_noise(db_clean, tmp_path):
    """Several official fields move at once; provenance fields are not changes."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated", termination="NA")
    await _ingest(
        tmp_path, checked_at=LATER, status="Completed", termination="15-08-2026",
    )

    projection = await _project()

    assert projection.status is RegulatoryChangeStatus.CHANGED
    assert set(projection.changed_fields) == {"recall_status", "recall_termination_date"}
    # Observation bookkeeping never appears as a regulatory change.
    for provenance in (
        "created_at", "updated_at", "last_seen_at", "last_seen_fetch_id",
        "source_file_sha256", "adapter_version", "original_filename",
        "observed_at", "content_hash", "revision_number", "first_seen_at",
    ):
        assert provenance not in projection.changed_fields
        assert provenance not in MATERIAL_FIELDS


# ---------------------------------------------------------------------------
# D. Identical re-observation is not a regulatory change
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_d_identical_re_observation_creates_no_second_revision(db_clean, tmp_path):
    """Seeing the same row again is an observation, not a change of position."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    counts = await _ingest(tmp_path, checked_at=LATER, status="Initiated")

    assert counts["records_unchanged"] == 1
    assert counts["records_revised"] == 0
    assert len(await _revisions()) == 1
    # Still a first observation, not a change, however many times we looked.
    assert (await _project()).status is RegulatoryChangeStatus.FIRST_OBSERVED_RECORD


# ---------------------------------------------------------------------------
# E. Omission from a later export is not resolution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_e_source_omission_invents_no_revision_and_no_clearance(db_clean, tmp_path):
    """A row absent from the next download proves nothing about withdrawal."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    # A later, valid export that simply does not contain our record.
    await _ingest_rows(
        tmp_path, checked_at=LATER,
        rows=[data_row(recall_id=902, batch="B-777", brand="Other", product="Other cereal")],
    )

    record = await _record()
    by_record = await _revisions()

    assert len(by_record) == 1, "absence must not manufacture a revision"
    # The later export was genuinely ingested; it simply said nothing about us.
    assert len(await _all_revisions()) == 2
    assert (await _record("902")).latest_revision == 1
    assert record.latest_revision == 1
    assert record.recall_status == "Initiated"
    projection = await _project()
    assert projection.status is RegulatoryChangeStatus.FIRST_OBSERVED_RECORD
    rendered = repr(projection.as_payload()).lower()
    for forbidden in ("withdraw", "cleared", "resolved", "safe", "expired", "cancelled"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# F. The exact identity itself changes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_f_changed_exact_identity_is_stated_not_turned_into_a_clearance(db_clean, tmp_path):
    """Revision 2 names a different batch. That is a fact, never a clearance."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest_rows(
        tmp_path, checked_at=LATER,
        rows=[data_row(recall_id=int(RECALL_ID), brand=BRAND, product=PRODUCT,
                       batch="B-999", status="Initiated")],
    )

    projection = await _project()

    assert projection.status is RegulatoryChangeStatus.CHANGED
    assert "batch_lot" in projection.changed_fields
    # The flag exists precisely so a later surface cannot read "identity moved"
    # as "your pack is fine".
    assert projection.exact_identity_changed is True
    rendered = repr(projection.as_payload()).lower()
    for forbidden in ("cleared", "safe", "no longer applies", "resolved"):
        assert forbidden not in rendered


# ---------------------------------------------------------------------------
# G / H / I. Attribution stays deterministic
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g_latest_matches_but_history_is_not_called_newly_recalled(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Our observation history is not the regulator's publication history."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    # Revision 1 names somebody else's batch; revision 2 names this pack's.
    await _ingest_rows(
        tmp_path, checked_at=SOURCE_CHECKED_AT,
        rows=[data_row(recall_id=int(RECALL_ID), brand=BRAND, product=PRODUCT, batch="B-000")],
    )
    await _ingest(tmp_path, checked_at=LATER, status="Initiated")

    change, body = await _matched_change(app_client, device)
    rendered = repr(body).lower()

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    for forbidden in ("newly recalled", "newly_recalled", "new recall"):
        assert forbidden not in rendered


@pytest.mark.asyncio
async def test_h_ambiguous_records_publish_no_product_specific_change(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Two rows share this licence and lot; neither is provably this pack's."""
    token, account_id = await registered_supabase_user()
    await confirm_label(
        app_client, device, token, account_id, BARCODE,
        label_facts(brand=None, product_name=None),
    )
    await _ingest_rows(
        tmp_path, checked_at=SOURCE_CHECKED_AT,
        rows=[
            data_row(recall_id=901, brand="Northstar", product="Cereal A"),
            data_row(recall_id=902, brand="Southline", product="Cereal B"),
        ],
    )

    body = await verdict(app_client, device)

    assert body["official_records"]["records"] == []


@pytest.mark.asyncio
async def test_i_open_food_facts_alone_cannot_produce_a_regulatory_change(
    db_clean, off_clean, app_client, device, tmp_path,  # noqa: F811
):
    """No confirmed pack means no licence and no batch, so nothing attaches."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")

    body = await verdict(app_client, device)

    assert body["official_records"]["records"] == []
    assert "regulatory_change" not in repr(body["official_records"]["records"])


# ---------------------------------------------------------------------------
# J / K / L. Corrupt history fails closed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_j_corrupt_revision_hash_fails_closed(db_clean, tmp_path):
    """A payload edited outside the import path is refused, not reconciled."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Ongoing")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        revision = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.revision_number == 2
        ))).scalars().one()
        revision.content_hash = "d" * 64
        await session.commit()

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        await _project()

    assert caught.value.reason == "current_revision_content_hash_mismatch"
    # The reason is for the log. It is not in what a customer would receive.
    assert "content_hash" not in caught.value.message
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_k_broken_revision_sequence_fails_closed(db_clean, tmp_path):
    """The predecessor is the previous revision of this record, or nothing."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()
    current, previous = revisions[1], revisions[0]

    # A missing predecessor.
    with pytest.raises(RegulatoryHistoryInvariantError) as missing:
        project_regulatory_change(
            record=record, current=current, previous=None,
            current_fetch=await _fetch_for(current), previous_fetch=None,
        )
    assert missing.value.reason == "revision_predecessor_missing"

    # A predecessor that is not the immediately preceding revision. Contiguity
    # is settled before anything else about that row is considered, so a
    # predecessor two steps back is refused rather than compared.
    previous.revision_number = 0
    with pytest.raises(RegulatoryHistoryInvariantError) as gap:
        project_regulatory_change(
            record=record, current=current, previous=previous,
            current_fetch=await _fetch_for(current),
            previous_fetch=await _fetch_for(revisions[0]),
        )
    assert gap.value.reason == "revision_sequence_non_contiguous"

    # A revision numbered below the first one the importer can write.
    previous.revision_number = 1
    current.revision_number = 0
    with pytest.raises(RegulatoryHistoryInvariantError) as unnumbered:
        project_regulatory_change(
            record=record, current=current, previous=previous,
            current_fetch=await _fetch_for(current),
            previous_fetch=await _fetch_for(revisions[0]),
        )
    assert unnumbered.value.reason == "current_revision_number_invalid"


@pytest.mark.asyncio
async def test_l_canonical_row_disagreeing_with_latest_revision_fails_closed(db_clean, tmp_path):
    """The canonical row is a projection of the latest revision, or it is corrupt."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        record = (await session.execute(select(OfficialRecord))).scalars().one()
        record.recall_status = "Something Nobody Published"
        await session.commit()

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        await _project()

    assert caught.value.reason == "canonical_record_disagrees_with_latest_revision"


@pytest.mark.asyncio
async def test_l2_a_revision_from_a_failed_fetch_fails_closed(db_clean, tmp_path):
    """Content the register never successfully served is not authority."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()
    current = revisions[1]
    fetch = await _fetch_for(current)
    fetch.status = "failed"

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        project_regulatory_change(
            record=record, current=current, previous=revisions[0],
            current_fetch=fetch, previous_fetch=await _fetch_for(revisions[0]),
        )

    assert caught.value.reason == "current_revision_fetch_unsuccessful"


@pytest.mark.asyncio
async def test_l3_a_revision_of_another_record_cannot_be_the_predecessor(db_clean, tmp_path):
    """Pairing is by record and revision number, never by "looks close enough"."""
    await _ingest_rows(
        tmp_path, checked_at=SOURCE_CHECKED_AT,
        rows=[
            data_row(recall_id=901, brand=BRAND, product=PRODUCT, batch=BATCH),
            data_row(recall_id=902, brand="Other", product="Other cereal", batch="B-777"),
        ],
    )
    async with get_sessionmaker()() as session:
        records = (await session.execute(
            select(OfficialRecord).order_by(OfficialRecord.external_record_id)
        )).scalars().all()
        revisions = (await session.execute(select(OfficialRecordRevision))).scalars().all()
    ours, theirs = records[0], records[1]
    our_revision = next(r for r in revisions if r.record_id == ours.id)
    their_revision = next(r for r in revisions if r.record_id == theirs.id)

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        project_regulatory_change(
            record=ours, current=our_revision, previous=their_revision,
            current_fetch=await _fetch_for(our_revision),
            previous_fetch=await _fetch_for(their_revision),
        )

    assert caught.value.reason in {
        "first_revision_has_predecessor", "previous_revision_belongs_to_another_record",
    }


@pytest.mark.asyncio
async def test_l5_a_fetch_from_another_authority_fails_closed(db_clean, tmp_path):
    """A revision must be derived from a look at this record's own register."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    revisions = await _revisions()
    record = await _record()
    fetch = await _fetch_for(revisions[-1])
    fetch.authority = "some_other_regulator"

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        project_regulatory_change(
            record=record, current=revisions[-1], previous=revisions[-2],
            current_fetch=fetch, previous_fetch=await _fetch_for(revisions[-2]),
        )

    assert caught.value.reason == "current_revision_fetch_authority_mismatch"

    # The same for the record type: a cosmetics register is not a food recall.
    fetch.authority = record.authority
    fetch.record_type = "some_other_record_type"
    with pytest.raises(RegulatoryHistoryInvariantError) as typed:
        project_regulatory_change(
            record=record, current=revisions[-1], previous=revisions[-2],
            current_fetch=fetch, previous_fetch=await _fetch_for(revisions[-2]),
        )
    assert typed.value.reason == "current_revision_fetch_authority_mismatch"


@pytest.mark.asyncio
async def test_l6_a_payload_for_a_different_official_record_fails_closed(db_clean, tmp_path):
    """The payload must state the identity of the record it is filed under."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == RECALL_ID
        ))).scalars().one()
        revision = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.record_id == record.id,
            OfficialRecordRevision.revision_number == 1,
        ))).scalars().one()
        revision.payload = {**revision.payload, "external_record_id": "999"}
        await session.commit()
    revisions = await _revisions()
    record = await _record()

    with pytest.raises(RegulatoryHistoryInvariantError) as caught:
        project_regulatory_change(
            record=record, current=revisions[-1], previous=revisions[0],
            current_fetch=await _fetch_for(revisions[-1]),
            previous_fetch=await _fetch_for(revisions[0]),
        )

    # Identity is settled before the hash, so the reason names the real fault.
    assert caught.value.reason == "previous_revision_identity_mismatch"


@pytest.mark.asyncio
async def test_l4_a_record_with_no_revision_at_all_fails_closed(db_clean, tmp_path, caplog):
    """Missing history is history we cannot read, not history that says nothing."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    async with get_sessionmaker()() as session:
        await session.execute(delete(OfficialRecordRevision))
        await session.commit()
    record = await _record()

    async with get_sessionmaker()() as session:
        with caplog.at_level(logging.WARNING):
            envelope = await official_records.regulatory_change_for_record(session, record)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert "record_has_no_revision" in caplog.text
    # Never null, which a client could read as "there is no history here".
    assert envelope is not None


# ---------------------------------------------------------------------------
# M / N / O. The evidence boundary
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_m_valid_change_is_withheld_when_no_openable_source_exists(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,  # noqa: F811
):
    """The comparison is internally correct and is still not published."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    # "Ongoing" is the superseded value and is written nowhere else in the
    # response, so finding it anywhere would be the withheld comparison leaking.
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Ongoing")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")

    # Internally, the engine knows exactly what moved.
    internal = await _project()
    assert internal.status is RegulatoryChangeStatus.CHANGED
    assert internal.changed_fields == ("recall_status",)
    assert internal.changes[0].previous_value == "Ongoing"

    change, body = await _matched_change(app_client, device)

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    # The official record itself is still shown, under the existing rules.
    assert body["official_records"]["records"][0]["recall_status"] == "Completed"
    assert body["official_records"]["source_url"].startswith("https://")
    # Neither value of the withheld comparison leaks, and no reason does either.
    rendered = repr(body)
    assert "Ongoing" not in rendered
    for leaked in ("invariant", "content_hash", "record_id", "revision_id", "source_fetch"):
        assert leaked not in rendered


@pytest.mark.asyncio
async def test_n_evidence_can_never_override_integrity(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """With publication forced on and the ledger corrupt, nothing is published.

    This is the milestone-after-next test: when a revision-specific official
    locator finally exists the gate starts returning True, and that must not
    turn a history the integrity authority rejects into a customer claim.
    """
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        revision = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.revision_number == 2
        ))).scalars().one()
        revision.content_hash = "d" * 64
        await session.commit()
    monkeypatch.setattr(
        official_records.change_evidence, "regulatory_change_is_publishable",
        lambda **_kwargs: True,
    )

    change, body = await _matched_change(app_client, device)

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    assert "Ongoing" not in repr(body)


@pytest.mark.asyncio
async def test_n2_integrity_runs_even_when_publication_is_withheld(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path, caplog,  # noqa: F811
):
    """Corruption is found and logged although nobody was going to be told.

    On a service that asked the evidence gate first, this row would never be
    examined at all: the page looks identical, and the warning that is the only
    trace of the corruption is never emitted.
    """
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    async with get_sessionmaker()() as session:
        revision = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.revision_number == 2
        ))).scalars().one()
        revision.content_hash = "d" * 64
        await session.commit()

    with caplog.at_level(logging.WARNING, logger="app.domains.official_records.service"):
        change, body = await _matched_change(app_client, device)

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    warnings = [
        row for row in caplog.records
        if "regulatory_history_invariant_failed" in row.getMessage()
    ]
    assert warnings, "the integrity authority never ran"
    logged = warnings[0].getMessage()
    assert "content_hash_mismatch" in logged
    # The reason reaches the log and nothing else.
    assert "content_hash_mismatch" not in repr(body)


@pytest.mark.asyncio
async def test_o_the_publishing_branch_works_when_both_authorities_permit_it(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path, monkeypatch,  # noqa: F811
):
    """Structural proof of the far side, with no fabricated production evidence.

    The real gate is stubbed for this one request and no locator is written, so
    nothing about the shipped evidence rule is weakened. It exists so the
    publishing branch is exercised at all, and so the published values are
    proven to come from the validated projection.
    """
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    monkeypatch.setattr(
        official_records.change_evidence, "regulatory_change_is_publishable",
        lambda **_kwargs: True,
    )

    change, _body = await _matched_change(app_client, device)
    expected = (await _project()).as_payload()

    assert change == expected
    assert change["status"] == "changed"
    assert change["changed_fields"] == ["recall_status"]
    assert change["changes"] == [
        {"field": "recall_status", "previous_value": "Initiated", "current_value": "Completed"},
    ]
    assert change["exact_identity_changed"] is False


@pytest.mark.parametrize("candidate", [
    "https://foscos.fssai.gov.in/food-recall",  # real, openable, not revision-specific
    "d" * 64,                                    # a SHA-256 is not a locator
    "foscos-food-recall.xlsx",                   # a filename is not a locator
    "/api/v2/official-records/901",              # our own API citing itself
    "file:///tmp/foscos.xlsx",
    "",
    None,
    12345,
])
@pytest.mark.asyncio
async def test_o2_the_gate_is_not_satisfied_by_a_manufactured_locator(db_clean, tmp_path, candidate):
    """No assembled string opens the publication gate.

    The register landing page is the tempting one: it is genuinely official and
    genuinely openable. It is still refused as *revision* evidence, because one
    constant page shared by every revision cannot say which revision it proves.
    """
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    record = await _record()
    revision = (await _revisions())[0]

    if candidate == "https://foscos.fssai.gov.in/food-recall":
        # Openable in the general sense, and still not a revision locator.
        assert change_evidence.is_openable_official_source(candidate) is True
        assert record.source_url == candidate
    assert change_evidence.revision_source(revision) is None
    assert change_evidence.regulatory_change_is_publishable(
        record=record, current=revision, previous=None,
    ) is False


# ---------------------------------------------------------------------------
# P / Q. Nothing else moves
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_p_a_regulatory_revision_does_not_touch_the_scientific_verdict(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,  # noqa: F811
):
    """The official record is additive. The science is decided without it."""
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    before = await verdict(app_client, device)

    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    after = await verdict(app_client, device)

    for key in ("grade", "band", "decision", "negatives", "positives", "nutrition",
                "alternative", "confidence", "facts_provenance"):
        assert before.get(key) == after.get(key), f"{key} moved with an official revision"
    # The only thing that moved is the official envelope itself.
    assert before["official_records"] != after["official_records"]


@pytest.mark.asyncio
async def test_q_a_later_revision_does_not_rewrite_historical_decision_memory(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path,  # noqa: F811
):
    """Decision Memory and the shelf hold history, not today's regulatory state.

    Neither table stores official-record state at all, which is why a revision
    cannot rewrite them — this proves the rows are byte-identical rather than
    trusting that they have no reason to change.
    """
    from sqlalchemy import text as sql_text

    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await verdict(app_client, device)

    async def _snapshot() -> list[tuple]:
        async with get_sessionmaker()() as session:
            rows = []
            for table in ("scan_decision_events", "inventory_product_links",
                          "product_label_snapshots"):
                rows.append(tuple((await session.execute(
                    sql_text(f"SELECT md5(CAST((t.*) AS text)) FROM {table} AS t ORDER BY 1")  # noqa: S608
                )).scalars().all()))
            return rows

    before = await _snapshot()
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    await _ingest(tmp_path, checked_at=LATER_STILL, status="Terminated")
    after = await _snapshot()

    assert before == after, "an official revision rewrote historical product truth"


# ---------------------------------------------------------------------------
# The material-field whitelist is the parser's own canonical row
# ---------------------------------------------------------------------------
def test_material_fields_are_exactly_the_parsed_official_content():
    """The whitelist is derived from the source contract, not hand-maintained."""
    from app.domains.official_records.source import canonical_row

    parsed = canonical_row({"Recall Id": 1})
    # Exactly the parsed official content, minus the key the record is filed
    # under. Adding a column to the source without deciding whether it is a
    # regulatory position, or adding a field of ours to the whitelist, fails
    # here rather than quietly becoming a customer-facing "change".
    assert set(MATERIAL_FIELDS) == set(parsed) - {"external_record_id"}
    assert len(set(MATERIAL_FIELDS)) == len(MATERIAL_FIELDS)
    # The whitelist's own order is this module's presentation order, not the
    # spreadsheet's; it decides the order of ``changed_fields``.
    assert MATERIAL_FIELDS.index("recall_status") < MATERIAL_FIELDS.index("recall_start_date")


def test_the_withheld_envelope_carries_no_values_to_leak():
    """The withheld envelope states nothing about any field."""
    payload = UNAVAILABLE_PROJECTION.as_payload()
    assert payload["changed_fields"] == []
    assert payload["changes"] == []
    assert payload["current_revision"] is None
    assert payload["previous_revision"] is None
    assert payload["status"] == "unavailable"


def test_the_hash_rule_is_the_importer_s_own():
    """Integrity is checked under the rule the importer actually used."""
    row = {"external_record_id": "1", **{field: None for field in MATERIAL_FIELDS}}
    assert stable_content_hash(row) == stable_content_hash(dict(row))
    assert stable_content_hash({**row, "recall_status": "x"}) != stable_content_hash(row)


def test_date_payloads_hash_identically_whether_stored_as_date_or_iso_text():
    """The stored payload is ISO text; the importer hashed date objects."""
    with_date = {"recall_start_date": date(2026, 8, 1)}
    with_text = {"recall_start_date": "2026-08-01"}
    assert stable_content_hash(with_date) == stable_content_hash(with_text)


#: Everything Step 12C owns and Step 12B must not reach for.
#: "fetch" is deliberately absent: the existing ingestion ledger is spelled
#: ``OfficialSourceFetch``, and this authority reads it. Reading a row that an
#: operator already imported is the whole of Step 12B; going and getting one is
#: what Step 12C would have to do, and that is what the rest of this list bans.
STEP_12C_VOCABULARY = (
    "notify", "notification", "subscribe", "subscription", "watch", "alert",
    "cron", "schedule", "poll", "httpx", "requests", "celery", "queue", "worker",
)


def _code_words(source: str) -> set[str]:
    """Every name and literal the code actually uses.

    Prose is excluded on purpose: both modules say in their docstrings that
    Step 12C is absent, and a plain text search would read that promise as a
    breach of it. Imports, calls, attributes and string literals are where a
    watcher, a poller or an HTTP client would have to appear.
    """
    import ast

    tree = ast.parse(source)
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    words: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.add(node.id)
        elif isinstance(node, ast.Attribute):
            words.add(node.attr)
        elif isinstance(node, ast.arg) or (isinstance(node, ast.keyword) and node.arg):
            words.add(node.arg)
        elif isinstance(node, ast.alias):
            words.update(part for part in (node.name, node.asname) if part)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            words.add(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            words.add(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings:
            words.add(node.value)
    return {word.lower() for word in words}


def test_no_step_12c_behaviour_is_reachable_from_this_authority():
    """Step 12B derives on read. It subscribes, schedules and notifies nothing."""
    import inspect

    for module in (change_projection, change_evidence):
        words = _code_words(inspect.getsource(module))
        for forbidden in STEP_12C_VOCABULARY:
            offenders = sorted(word for word in words if forbidden in word)
            assert not offenders, (
                f"{module.__name__} reaches into Step 12C territory: {offenders}"
            )


def test_the_step_12c_guard_would_actually_catch_step_12c_code():
    """The guard above is only worth having if it fails on a real breach."""
    breach = (
        '''"""A module that only promises not to poll."""\n'''
        "import httpx\n\n\n"
        "def schedule_watch():\n"
        "    return httpx.get('/notify')\n"
    )
    words = _code_words(breach)
    assert {"httpx", "schedule_watch", "/notify", "get"} <= words
    caught = {term for term in STEP_12C_VOCABULARY if any(term in word for word in words)}
    assert caught == {"httpx", "schedule", "watch", "notify"}

    # The docstring alone, with no such code, is not a breach.
    assert not {
        term for term in STEP_12C_VOCABULARY
        if any(term in word for word in _code_words('''"""No poll, no watch, no notification."""'''))
    }


def test_utc_helpers_stay_unused_so_the_projection_has_no_clock():
    """A pure function of two rows: same pair, same answer, any day."""
    import inspect

    source = inspect.getsource(change_projection)
    for clock in ("datetime.now", "utcnow", "date.today", "time.time"):
        assert clock not in source

