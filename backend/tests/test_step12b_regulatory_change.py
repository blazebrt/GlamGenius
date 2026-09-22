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
from app.domains.official_records.source import SOURCE_URL, stable_content_hash
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
async def test_the_predecessor_is_selected_from_this_record_alone(db_clean, tmp_path):
    """Pair selection is by record and revision number, not by "a revision 1".

    Two records each hold a revision 1 and a revision 2. A selection that asked
    only for ``revision_number == current - 1`` would be free to return the
    other record's row, and the integrity authority would then refuse a
    comparison that was never wrong in the first place — the customer sees the
    same withheld envelope either way, so only this test can tell them apart.
    """
    await _ingest_rows(
        tmp_path, checked_at=SOURCE_CHECKED_AT,
        rows=[
            data_row(recall_id=901, brand=BRAND, product=PRODUCT, batch=BATCH, status="Ongoing"),
            data_row(recall_id=902, brand="Other", product="Other cereal", batch="B-777",
                     status="Ongoing"),
        ],
    )
    await _ingest_rows(
        tmp_path, checked_at=LATER,
        rows=[
            data_row(recall_id=901, brand=BRAND, product=PRODUCT, batch=BATCH, status="Completed"),
            data_row(recall_id=902, brand="Other", product="Other cereal", batch="B-777",
                     status="Completed"),
        ],
    )

    for external_id in ("901", "902"):
        record = await _record(external_id)
        async with get_sessionmaker()() as session:
            _fresh, current, previous = await official_records._revision_pair(session, record)
        assert current is not None and previous is not None
        assert current.record_id == record.id
        assert previous.record_id == record.id
        assert (current.revision_number, previous.revision_number) == (2, 1)
        # And the pair projects cleanly, which it could not do if either row
        # had come from the other record.
        projection = project_regulatory_change(
            record=record, current=current, previous=previous,
            current_fetch=await _fetch_for(current), previous_fetch=await _fetch_for(previous),
        )
        assert projection.status is RegulatoryChangeStatus.CHANGED
        assert projection.changed_fields == ("recall_status",)


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
# The ledger proves its own head. The canonical pointer is a claim it checks.
# ---------------------------------------------------------------------------
async def _service_change(caplog, external_record_id: str = RECALL_ID):
    """The real ``regulatory_change_for_record`` path, with the reasons it logged."""
    record = await _record(external_record_id)
    caplog.clear()
    async with get_sessionmaker()() as session:
        with caplog.at_level(logging.WARNING, logger="app.domains.official_records.service"):
            envelope = await official_records.regulatory_change_for_record(session, record)
    reasons = [
        row.getMessage().rsplit("reason=", 1)[-1]
        for row in caplog.records
        if "regulatory_history_invariant_failed" in row.getMessage()
    ]
    return envelope, reasons


async def _ingest_chain(tmp_path, statuses):
    """One legitimate import per status, each at a later source time."""
    for step, status in enumerate(statuses):
        await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT + timedelta(days=3 * step), status=status)


async def _set_record(**values):
    async with get_sessionmaker()() as session:
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == RECALL_ID
        ))).scalars().one()
        for name, value in values.items():
            setattr(record, name, value)
        await session.commit()


async def _delete_revision(number: int):
    async with get_sessionmaker()() as session:
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == RECALL_ID
        ))).scalars().one()
        await session.execute(delete(OfficialRecordRevision).where(
            OfficialRecordRevision.record_id == record.id,
            OfficialRecordRevision.revision_number == number,
        ))
        await session.commit()


def _force_publication(monkeypatch):
    """Lift the evidence gate, so only the integrity authority stands in the way.

    With the gate closed every answer is withheld anyway, which would hide
    whether the integrity authority accepted a history it should have refused.
    """
    monkeypatch.setattr(
        official_records.change_evidence, "regulatory_change_is_publishable",
        lambda **_kwargs: True,
    )


@pytest.mark.asyncio
async def test_d2_a_regressed_pointer_is_refused_rather_than_trusted(db_clean, tmp_path, caplog):
    """Ledger 1, 2; pointer 1. The newest observation may not be hidden by the pointer."""
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    await _set_record(latest_revision=1)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == ["canonical_latest_revision_pointer_mismatch"]


@pytest.mark.asyncio
async def test_d2_revision_one_is_not_silently_accepted_as_the_whole_history(
    db_clean, off_clean, app_client, device, registered_supabase_user, tmp_path, caplog, monkeypatch,  # noqa: F811
):
    """The sharp case: pointer *and* canonical row rolled back together.

    A record row restored from an old backup agrees perfectly with revision 1,
    so every content check passes — and revision 2 still sits in the ledger.
    Trusting the pointer would present revision 1 as a first observation.
    """
    token, account_id = await registered_supabase_user()
    await confirm_label(app_client, device, token, account_id, BARCODE, label_facts())
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    await _set_record(latest_revision=1, recall_status="Ongoing")
    _force_publication(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="app.domains.official_records.service"):
        change, body = await _matched_change(app_client, device)

    assert change == UNAVAILABLE_PROJECTION.as_payload()
    assert change["status"] != "first_observed_record"
    assert "canonical_latest_revision_pointer_mismatch" in caplog.text
    assert "canonical_latest_revision_pointer_mismatch" not in repr(body)


@pytest.mark.asyncio
async def test_d2_a_pointer_ahead_of_the_ledger_is_refused(db_clean, tmp_path, caplog):
    """Ledger 1, 2; pointer 3. The pointer names an observation that does not exist."""
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    await _set_record(latest_revision=3)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == ["canonical_latest_revision_pointer_mismatch"]


@pytest.mark.parametrize("removed", [1, 2])
@pytest.mark.asyncio
async def test_d2_a_hidden_sequence_gap_is_refused_not_bridged(
    db_clean, tmp_path, caplog, monkeypatch, removed,
):
    """Ledger 1..3 with one revision gone. No plausible chain is built from survivors.

    Removing revision 1 leaves ``2, 3`` — a pair that compares cleanly on its
    own. Removing revision 2 leaves ``1, 3``. Both are histories no supported
    import writes, and both are refused whatever the evidence gate would say.
    """
    await _ingest_chain(tmp_path, ["Initiated", "Ongoing", "Completed"])
    await _delete_revision(removed)
    _force_publication(monkeypatch)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == ["revision_ledger_sequence_invalid"]


@pytest.mark.asyncio
async def test_d2_a_valid_three_revision_ledger_still_compares_its_head(
    db_clean, tmp_path, caplog, monkeypatch,
):
    """The checks refuse corruption, not length: 1..3 compares revision 3 with 2."""
    await _ingest_chain(tmp_path, ["Initiated", "Ongoing", "Completed"])
    _force_publication(monkeypatch)

    envelope, reasons = await _service_change(caplog)

    assert reasons == []
    assert envelope["status"] == "changed"
    assert (envelope["current_revision"], envelope["previous_revision"]) == (3, 2)
    assert envelope["changes"] == [
        {"field": "recall_status", "previous_value": "Ongoing", "current_value": "Completed"},
    ]


@pytest.mark.asyncio
async def test_d2_a_concurrent_import_is_not_mistaken_for_a_corrupt_pointer(
    db_clean, tmp_path, caplog, monkeypatch,
):
    """An import landing mid-request must not raise a false integrity alarm.

    The record row is loaded first, as ``recalls_for_pack`` loads it; an import
    then commits revision 3. Checking the stale in-memory pointer against the
    live ledger would call that corruption. The pointer and the aggregate are
    read in one statement, so they always come from the same snapshot.
    """
    await _ingest_chain(tmp_path, ["Initiated", "Ongoing"])
    _force_publication(monkeypatch)
    async with get_sessionmaker()() as session:
        stale = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == RECALL_ID
        ))).scalars().one()
        assert stale.latest_revision == 2
        await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT + timedelta(days=6), status="Completed")

        with caplog.at_level(logging.WARNING, logger="app.domains.official_records.service"):
            envelope = await official_records.regulatory_change_for_record(session, stale)

    assert "regulatory_history_invariant_failed" not in caplog.text
    assert envelope["status"] == "changed"
    assert (envelope["current_revision"], envelope["previous_revision"]) == (3, 2)


# ---------------------------------------------------------------------------
# Revision time is bound to its own source check, and moves strictly forward
# ---------------------------------------------------------------------------
async def _revision_and_fetch(number: int):
    async with get_sessionmaker()() as session:
        record = (await session.execute(select(OfficialRecord).where(
            OfficialRecord.external_record_id == RECALL_ID
        ))).scalars().one()
        revision = (await session.execute(select(OfficialRecordRevision).where(
            OfficialRecordRevision.record_id == record.id,
            OfficialRecordRevision.revision_number == number,
        ))).scalars().one()
        return revision.id, revision.source_fetch_id


async def _stamp(number: int, *, observed_at=None, source_checked_at=None):
    """Corrupt stored time after a legitimate import, as a hand edit would."""
    revision_id, fetch_id = await _revision_and_fetch(number)
    async with get_sessionmaker()() as session:
        if observed_at is not None:
            revision = await session.get(OfficialRecordRevision, revision_id)
            revision.observed_at = observed_at
        if source_checked_at is not None:
            fetch = await session.get(OfficialSourceFetch, fetch_id)
            fetch.source_checked_at = source_checked_at
        await session.commit()


@pytest.mark.parametrize(("number", "reason"), [
    (2, "current_revision_observation_time_mismatch"),
    (1, "previous_revision_observation_time_mismatch"),
])
@pytest.mark.asyncio
async def test_d3_a_revision_detached_from_its_own_source_check_fails_closed(
    db_clean, tmp_path, caplog, monkeypatch, number, reason,
):
    """``observed_at`` is the source time of the check that produced the revision.

    The drift is kept small and keeps the pair in order, so no chronology
    check could catch it: only binding each revision to its own fetch does.
    """
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    drifted = (SOURCE_CHECKED_AT if number == 1 else LATER) + timedelta(minutes=1)
    await _stamp(number, observed_at=drifted)
    _force_publication(monkeypatch)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == [reason]


@pytest.mark.asyncio
async def test_d3_two_revisions_at_the_same_source_time_fail_closed(
    db_clean, tmp_path, caplog, monkeypatch,
):
    """The importer refuses a second artifact at the same instant; so does this.

    Revision 1 *and* its fetch are moved to revision 2's time, so each revision
    still agrees with its own fetch. What remains is two semantic revisions of
    one record observed at one moment, which no accepted import produces.
    """
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    await _stamp(1, observed_at=LATER, source_checked_at=LATER)
    _force_publication(monkeypatch)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == ["revision_observation_order_invalid"]


@pytest.mark.asyncio
async def test_d3_a_predecessor_observed_after_its_successor_fails_closed(
    db_clean, tmp_path, caplog, monkeypatch,
):
    """Source time only moves forward. A predecessor from the future was not imported."""
    await _ingest_chain(tmp_path, ["Ongoing", "Completed"])
    await _stamp(1, observed_at=LATER_STILL, source_checked_at=LATER_STILL)
    _force_publication(monkeypatch)

    envelope, reasons = await _service_change(caplog)

    assert envelope == UNAVAILABLE_PROJECTION.as_payload()
    assert reasons == ["revision_observation_order_invalid"]


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


#: Never revision evidence, whatever field it sits in. The first block is the
#: one generic register page in every spelling; the rest are not the regulator.
NOT_REVISION_EVIDENCE = [
    "https://foscos.fssai.gov.in/food-recall",
    "https://foscos.fssai.gov.in/food-recall/",
    "https://foscos.fssai.gov.in/food-recall//",
    "https://foscos.fssai.gov.in/food-recall?revision=2",
    "https://foscos.fssai.gov.in/food-recall#revision-2",
    "https://FOSCOS.FSSAI.GOV.IN/food-recall",
    "http://foscos.fssai.gov.in/food-recall/archive/revision-2",
    "https://foscos.fssai.gov.in:443/food-recall/archive/revision-2",
    "https://example.com/revision/2",
    "https://example.com/archive/123",
    "https://evil.example/food-recall/archive/revision-2",
    "https://glamgenius.app/food-recall/archive/revision-2",
    "https://foscos.fssai.gov.in.evil.example/food-recall/archive/revision-2",
    "https://evil.foscos.fssai.gov.in/food-recall/archive/revision-2",
    "https://foscos.fssai.gov.in./food-recall/archive/revision-2",
    "https://foscos.fssai.gov.in@evil.example/food-recall/archive/revision-2",
    "https://user:secret@foscos.fssai.gov.in/food-recall/archive/revision-2",
    "https://foscos.fssai.gov.in/food-recall/../admin",
    "https://foscos.fssai.gov.in/food-recall/%2e%2e/admin",
    "https://foscos.fssai.gov.in/food-recallx/archive",
    "https://foscos.fssai.gov.in/other/archive",
    " https://foscos.fssai.gov.in/food-recall/archive/revision-2",
    "/api/v2/official-records/901",
    "/food-recall/archive/revision-2",
    "file:///tmp/foscos.xlsx",
    "foscos-food-recall.xlsx",
    "d" * 64,
    "",
    None,
    12345,
]

#: Test-only. Shaped like a page beneath the official register that shows one
#: revision, and **not claimed to exist**: no locator of any kind is stored in
#: production, and nothing here says FSSAI publishes one at this path.
STAND_IN_REVISION_LOCATOR = "https://foscos.fssai.gov.in/food-recall/archive/revision-2"


@pytest.mark.parametrize("candidate", NOT_REVISION_EVIDENCE)
def test_o2_no_candidate_outside_the_official_policy_is_revision_evidence(candidate):
    """Openable is not enough. The regulator's own host, beneath its register, or nothing."""
    assert change_evidence.is_official_revision_locator(candidate) is False


def test_o2_the_policy_is_one_exact_official_host():
    """Explicit and narrow: not a suffix match, not "any .gov.in", not "not ours"."""
    assert frozenset({"foscos.fssai.gov.in"}) == change_evidence.OFFICIAL_REVISION_HOSTS
    # The structural stand-in is the only shape that passes, and only as shape.
    assert change_evidence.is_official_revision_locator(STAND_IN_REVISION_LOCATOR) is True
    assert STAND_IN_REVISION_LOCATOR != SOURCE_URL


@pytest.mark.asyncio
async def test_o2_the_generic_register_page_never_becomes_revision_evidence(
    db_clean, tmp_path, monkeypatch,
):
    """Moving the landing page into a "revision locator" changes nothing it proves.

    Every record and every revision already carries this exact official, openable
    URL. It is refused as evidence for a historical value wherever it appears —
    as the record's own ``source_url``, as a fetch's, or handed back by
    ``revision_source`` as though a revision-level field had stored it.
    """
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()
    current, previous = revisions[-1], revisions[-2]

    assert record.source_url == SOURCE_URL
    assert (await _fetch_for(current)).source_url == SOURCE_URL
    assert change_evidence.is_official_revision_locator(SOURCE_URL) is False

    monkeypatch.setattr(change_evidence, "revision_source", lambda _revision: SOURCE_URL)
    assert change_evidence.regulatory_change_is_publishable(
        record=record, current=current, previous=previous,
    ) is False


@pytest.mark.asyncio
async def test_o2_production_revisions_have_no_revision_source_at_all(db_clean, tmp_path):
    """Fail closed by construction: no field name can open the gate.

    Even a revision object carrying an official, revision-shaped URL under any
    attribute name is given no source, because ``revision_source`` reads no
    field. Making it return anything is a reviewed provenance change.
    """
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()

    for revision in revisions:
        for name in ("archive_url", "source_url", "revision_url", "locator"):
            setattr(revision, name, STAND_IN_REVISION_LOCATOR)
        assert change_evidence.revision_source(revision) is None
    assert change_evidence.regulatory_change_is_publishable(
        record=record, current=revisions[-1], previous=revisions[-2],
    ) is False


@pytest.mark.parametrize("candidate", [*NOT_REVISION_EVIDENCE, STAND_IN_REVISION_LOCATOR])
@pytest.mark.asyncio
async def test_o2_the_future_branch_opens_only_for_an_official_revision_shape(
    db_clean, tmp_path, monkeypatch, candidate,
):
    """Structural only: if a locator were ever produced, which ones could open the gate.

    ``revision_source`` is replaced for the duration of the test; nothing is
    written, and no production field exists for it to read. The point is that
    the second refusal holds on its own: an arbitrary host or the generic page
    fails even when the first refusal has been lifted.
    """
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()
    monkeypatch.setattr(change_evidence, "revision_source", lambda _revision: candidate)

    assert change_evidence.regulatory_change_is_publishable(
        record=record, current=revisions[-1], previous=revisions[-2],
    ) is (candidate == STAND_IN_REVISION_LOCATOR)


@pytest.mark.asyncio
async def test_o2_one_sourced_side_is_not_half_publishable(db_clean, tmp_path, monkeypatch):
    """A before-and-after sentence needs evidence for the before and the after."""
    await _ingest(tmp_path, checked_at=SOURCE_CHECKED_AT, status="Initiated")
    await _ingest(tmp_path, checked_at=LATER, status="Completed")
    record = await _record()
    revisions = await _revisions()
    current, previous = revisions[-1], revisions[-2]
    monkeypatch.setattr(
        change_evidence, "revision_source",
        lambda revision: STAND_IN_REVISION_LOCATOR if revision is current else SOURCE_URL,
    )

    assert change_evidence.regulatory_change_is_publishable(
        record=record, current=current, previous=previous,
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
    """The withheld envelope states nothing about any field — including "none"."""
    payload = UNAVAILABLE_PROJECTION.as_payload()
    assert payload == {
        "scope": "official_record_history",
        "status": "unavailable",
        "current_revision": None,
        "previous_revision": None,
        "changed_fields": None,
        "changes": None,
        "exact_identity_changed": None,
    }


def test_a_consumer_cannot_read_unavailable_as_nothing_changed():
    """``[]`` says "no field changed"; ``False`` says "identity held". Neither is known.

    The checks below are the ones a careless client would write. Each of them
    must be unable to conclude "nothing changed" from a withheld comparison.
    """
    payload = UNAVAILABLE_PROJECTION.as_payload()

    assert payload["changed_fields"] != []
    assert payload["changes"] != []
    assert payload["exact_identity_changed"] is not False
    # Not an empty sequence dressed as an answer, and not a boolean at all.
    assert not isinstance(payload["changed_fields"], list | tuple)
    assert not isinstance(payload["changes"], list | tuple)
    assert not isinstance(payload["exact_identity_changed"], bool)
    # And the real non-change state stays distinct from it: a first observation
    # is an established answer, so it does carry the empty comparison.
    first = project_first_observation_payload()
    assert first["changed_fields"] == [] and first["changes"] == []
    assert first["exact_identity_changed"] is False
    assert first != payload


def project_first_observation_payload() -> dict:
    return change_projection.RegulatoryChangeProjection(
        status=RegulatoryChangeStatus.FIRST_OBSERVED_RECORD,
        current_revision=1, previous_revision=None,
        changed_fields=(), changes=(), exact_identity_changed=False,
    ).as_payload()


@pytest.mark.parametrize("leak", [
    {"changed_fields": ()},
    {"changes": ()},
    {"exact_identity_changed": False},
    {"exact_identity_changed": True},
    {"current_revision": 2},
    {"previous_revision": 1},
])
def test_an_unavailable_projection_that_states_anything_cannot_be_built(leak):
    """The ambiguous state is unrepresentable, not merely avoided."""
    fields = {
        "status": RegulatoryChangeStatus.UNAVAILABLE,
        "current_revision": None, "previous_revision": None,
        "changed_fields": None, "changes": None, "exact_identity_changed": None,
        **leak,
    }
    with pytest.raises(ValueError):
        change_projection.RegulatoryChangeProjection(**fields)


@pytest.mark.parametrize("status", [
    RegulatoryChangeStatus.FIRST_OBSERVED_RECORD, RegulatoryChangeStatus.CHANGED,
])
def test_an_established_projection_must_state_its_whole_comparison(status):
    """An answer we did establish may not quietly omit part of itself."""
    with pytest.raises(ValueError):
        change_projection.RegulatoryChangeProjection(
            status=status, current_revision=1, previous_revision=None,
            changed_fields=(), changes=None, exact_identity_changed=False,
        )


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


#: The four absence greps the CI "PR gate" job runs over ``backend/app/`` and
#: ``backend/server.py``. They are reproduced verbatim rather than referenced,
#: because the job is the only thing that ran them until a Step 12B docstring
#: used the word "subscription" to say Step 12B has none, and burned a CI cycle
#: proving it. The gates themselves are not weakened here, and must not be:
#: changing what they forbid is its own change, with its own review.
CI_ABSENCE_GREPS: tuple[tuple[str, ...], ...] = (
    ("mongodb", "MongoClient", "pymongo", "motor"),
    ("minio", "MinIO", "boto3", "s3_client", "S3_BUCKET"),
    ("stripe", "Stripe", "payment_intent", "billing", "subscription"),
)


@pytest.mark.parametrize("needles", CI_ABSENCE_GREPS, ids=lambda n: n[0])
def test_ci_absence_gates_pass_locally(needles):
    """Run the CI gate's own patterns here, so a docstring cannot fail CI alone.

    ``tests/test_no_legacy_terms.py`` polices the Supabase-cutover vocabulary,
    which is more specific: it forbids ``/api/subscription`` and
    ``app.domains.billing``, not the bare words the CI job greps for. This test
    closes that gap in the only direction that is safe — it matches CI exactly.
    """
    backend_root = Path(__file__).resolve().parents[1]
    roots = [backend_root / "app", backend_root / "server.py"]
    offenders = []
    for root in roots:
        paths = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for path in paths:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for needle in needles:
                    if needle in line:
                        offenders.append(f"{path.relative_to(backend_root)}:{lineno}: {needle}")
    assert not offenders, "the CI PR gate would fail on:\n" + "\n".join(offenders)


def test_utc_helpers_stay_unused_so_the_projection_has_no_clock():
    """A pure function of two rows: same pair, same answer, any day."""
    import inspect

    source = inspect.getsource(change_projection)
    for clock in ("datetime.now", "utcnow", "date.today", "time.time"):
        assert clock not in source

