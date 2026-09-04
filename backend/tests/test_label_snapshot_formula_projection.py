"""Step 7B.1 — projection is bound to one explicit label snapshot."""
from __future__ import annotations

import copy
import uuid

import pytest
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, SourceType
from app.domains.formulas import service as formula_service
from app.domains.formulas.parser import ParseStatus
from app.domains.formulas.service import FormulaResolution
from app.domains.product import formula_projection
from app.domains.product.formula_projection import (
    FormulaProjectionProvenance,
    project_formula_from_label_snapshot,
)
from app.domains.product.models import LabelSnapshot, ScanEvent
from app.domains.product.service import canonical_label_facts
from app.domains.substances import authoring as substance_authoring
from app.domains.substances.enums import EntityKind, NameNamespace
from app.domains.substances.service import ResolutionStatus
from app.shared.database import sql
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, select

VERIFIED = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)


def _snapshot(
    ingredients: object = "Water,Glycerin",
    *,
    facts: object | None = None,
    barcode: str = "8900000000001",
    version: int = 1,
    fingerprint: str = "a" * 64,
    previous_snapshot_id: uuid.UUID | None = None,
) -> LabelSnapshot:
    snapshot = LabelSnapshot(
        id=uuid.uuid4(),
        barcode=barcode,
        scan_event_id=uuid.uuid4(),
        facts={"ingredients_text": ingredients} if facts is None else facts,
        content_fingerprint=fingerprint,
        version_number=version,
        previous_snapshot_id=previous_snapshot_id,
        changed_fields=[] if version == 1 else ["ingredients_text"],
        completeness="complete_for_grading",
        confidence="user_confirmed",
    )
    return snapshot


def _name(text: str) -> dict[str, object]:
    return {
        "name": text,
        "namespace": NameNamespace.INCI.value,
        "language_tag": "und",
        "is_preferred": True,
    }


async def _publish_identity(session, key: str, printed: str) -> None:
    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=key,
        entity_kind=EntityKind.DEFINED_SUBSTANCE.value,
        names=[_name(printed)],
        summary=f"Names recorded for {key}.",
        scope="Nomenclature only.",
        evidence_strength=EvidenceStrength.STRONG.value,
        strength_rationale="A named reference records this nomenclature directly.",
        source_title="Reference entry",
        source_publisher="Example Reference",
        source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
        source_url=f"https://example.org/reference/{key}",
        license_or_use_note="Used under the publisher's stated terms.",
        author="tester",
    )
    claim_id = uuid.UUID(result["claim_id"])
    await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
    await evidence_authoring.record_publication_verification(
        session,
        claim_id,
        verification=VERIFIED,
        actor="founder",
    )
    await evidence_authoring.publish(session, claim_id, publisher="founder")
    await session.commit()


async def test_exact_snapshot_provenance_is_copied_without_recomputation(monkeypatch):
    snapshot = _snapshot()
    expected = FormulaResolution(ParseStatus.EMPTY)
    calls: list[tuple[object, object]] = []

    async def _resolve(session, value):
        calls.append((session, value))
        return expected

    monkeypatch.setattr(formula_projection, "resolve_formula", _resolve)
    session = object()
    result = await project_formula_from_label_snapshot(session, snapshot)

    assert result.formula is expected
    assert result.provenance == FormulaProjectionProvenance(
        label_snapshot_id=snapshot.id,
        barcode=snapshot.barcode,
        version_number=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
        scan_event_id=snapshot.scan_event_id,
    )
    assert calls == [(session, snapshot.facts["ingredients_text"])]


async def test_exact_raw_line_boundary_is_not_canonicalised_before_step_7b():
    raw = "Water\nGlycerin"
    snapshot = _snapshot(raw)
    assert canonical_label_facts(snapshot.facts)["ingredients_text"] == "Water Glycerin"

    result = await project_formula_from_label_snapshot(object(), snapshot)

    assert result.formula.status is ParseStatus.AMBIGUOUS_BOUNDARY
    assert result.formula.ingredients == ()


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        ({}, ParseStatus.EMPTY),
        ({"ingredients_text": None}, ParseStatus.EMPTY),
        ({"ingredients_text": " \t "}, ParseStatus.EMPTY),
        ({"ingredients_text": ["Water", "Glycerin"]}, ParseStatus.EMPTY),
        ("not-a-mapping", ParseStatus.EMPTY),
        ({"ingredients_text": "Water (Aqua"}, ParseStatus.MALFORMED),
    ],
)
async def test_missing_invalid_and_malformed_values_fail_closed_without_identity_lookup(
    monkeypatch,
    facts,
    status,
):
    async def _identity_must_not_run(*args, **kwargs):
        pytest.fail("Step 7A must not run when Step 7B cannot parse a formula")

    monkeypatch.setattr(formula_service, "resolve_names", _identity_must_not_run)
    result = await project_formula_from_label_snapshot(
        object(),
        _snapshot(facts=facts),
    )
    assert result.formula.status is status
    assert result.formula.ingredients == ()


async def test_valid_formula_and_duplicates_keep_printed_order_and_step_7a_identity(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _publish_identity(session, "water", "Water")
        await _publish_identity(session, "glycerin", "Glycerin")
        await _publish_identity(session, "niacinamide", "Niacinamide")

    normal = _snapshot("Water,Glycerin,Niacinamide")
    duplicate = _snapshot("Glycerin,Water,Glycerin", version=2, fingerprint="b" * 64)
    async with factory() as session:
        normal_result = await project_formula_from_label_snapshot(session, normal)
        duplicate_result = await project_formula_from_label_snapshot(session, duplicate)

    assert normal_result.formula.status is ParseStatus.PARSED
    assert [row.position for row in normal_result.formula.ingredients] == [1, 2, 3]
    assert [row.raw_name for row in normal_result.formula.ingredients] == [
        "Water",
        "Glycerin",
        "Niacinamide",
    ]
    assert [row.substance_key for row in normal_result.formula.ingredients] == [
        "water",
        "glycerin",
        "niacinamide",
    ]
    assert all(
        row.status is ResolutionStatus.RESOLVED
        for row in normal_result.formula.ingredients
    )
    assert normal_result.provenance.label_snapshot_id == normal.id
    assert [row.position for row in duplicate_result.formula.ingredients] == [1, 2, 3]
    assert [row.raw_name for row in duplicate_result.formula.ingredients] == [
        "Glycerin",
        "Water",
        "Glycerin",
    ]
    assert [row.substance_key for row in duplicate_result.formula.ingredients] == [
        "glycerin",
        "water",
        "glycerin",
    ]


async def test_explicit_versions_never_call_latest_snapshot_helpers(monkeypatch):
    first = _snapshot("Water,Glycerin", version=1, fingerprint="1" * 64)
    second = _snapshot(
        "Water,Niacinamide",
        version=2,
        fingerprint="2" * 64,
        previous_snapshot_id=first.id,
    )
    observed: list[object] = []

    async def _resolve(session, value):
        observed.append(value)
        return FormulaResolution(ParseStatus.PARSED)

    async def _latest_must_not_run(*args, **kwargs):
        pytest.fail("the projection must never choose a latest snapshot")

    from app.domains.product import service as product_service

    monkeypatch.setattr(formula_projection, "resolve_formula", _resolve)
    monkeypatch.setattr(product_service, "latest_label_snapshot", _latest_must_not_run)
    monkeypatch.setattr(product_service, "latest_label_snapshots", _latest_must_not_run)

    second_projection = await project_formula_from_label_snapshot(object(), second)
    first_projection = await project_formula_from_label_snapshot(object(), first)

    assert observed == ["Water,Niacinamide", "Water,Glycerin"]
    assert second_projection.provenance.label_snapshot_id == second.id
    assert second_projection.provenance.version_number == 2
    assert first_projection.provenance.label_snapshot_id == first.id
    assert first_projection.provenance.version_number == 1


async def test_a_to_b_to_a_history_preserves_each_snapshot_identity(monkeypatch):
    first = _snapshot("A", version=1, fingerprint="a" * 64)
    second = _snapshot(
        "B", version=2, fingerprint="b" * 64, previous_snapshot_id=first.id
    )
    third = _snapshot(
        "A", version=3, fingerprint="a" * 64, previous_snapshot_id=second.id
    )
    observed: list[object] = []

    async def _resolve(session, value):
        observed.append(value)
        return FormulaResolution(ParseStatus.PARSED)

    monkeypatch.setattr(formula_projection, "resolve_formula", _resolve)
    results = [
        await project_formula_from_label_snapshot(object(), snapshot)
        for snapshot in (first, second, third)
    ]

    assert observed == ["A", "B", "A"]
    assert [result.provenance.label_snapshot_id for result in results] == [
        first.id,
        second.id,
        third.id,
    ]
    assert [result.provenance.version_number for result in results] == [1, 2, 3]
    assert [result.provenance.content_fingerprint for result in results] == [
        "a" * 64,
        "b" * 64,
        "a" * 64,
    ]
    assert [result.provenance.scan_event_id for result in results] == [
        first.scan_event_id,
        second.scan_event_id,
        third.scan_event_id,
    ]


async def test_same_snapshot_tracks_live_unresolved_resolved_ambiguous_registry(db_clean):
    snapshot = _snapshot("Shared Printed Name")
    original = {
        field: copy.deepcopy(getattr(snapshot, field))
        for field in (
            "id",
            "facts",
            "version_number",
            "content_fingerprint",
            "previous_snapshot_id",
            "changed_fields",
            "scan_event_id",
        )
    }
    factory = get_sessionmaker()

    async with factory() as session:
        unresolved = await project_formula_from_label_snapshot(session, snapshot)
        await _publish_identity(session, "shared.alpha", "Shared Printed Name")
        resolved = await project_formula_from_label_snapshot(session, snapshot)
        await _publish_identity(session, "shared.beta", "Shared Printed Name")
        ambiguous = await project_formula_from_label_snapshot(session, snapshot)

    assert unresolved.formula.ingredients[0].status is ResolutionStatus.UNRESOLVED
    assert resolved.formula.ingredients[0].status is ResolutionStatus.RESOLVED
    assert resolved.formula.ingredients[0].substance_key == "shared.alpha"
    assert ambiguous.formula.ingredients[0].status is ResolutionStatus.AMBIGUOUS
    assert ambiguous.formula.ingredients[0].substance_key is None
    assert ambiguous.formula.ingredients[0].candidate_substance_keys == (
        "shared.alpha",
        "shared.beta",
    )
    assert {
        field: copy.deepcopy(getattr(snapshot, field)) for field in original
    } == original
    assert unresolved.provenance == resolved.provenance == ambiguous.provenance


async def test_projection_keeps_one_full_formula_step_7a_batch(monkeypatch, db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _publish_identity(session, "water", "Water")
        await _publish_identity(session, "glycerin", "Glycerin")
        await _publish_identity(session, "niacinamide", "Niacinamide")

    real_resolve_names = formula_service.resolve_names
    batches: list[list[str]] = []

    async def _spy(session, names):
        batches.append(list(names))
        return await real_resolve_names(session, names)

    monkeypatch.setattr(formula_service, "resolve_names", _spy)
    async with factory() as session:
        result = await project_formula_from_label_snapshot(
            session,
            _snapshot("Water,Glycerin,Niacinamide"),
        )

    assert result.formula.status is ParseStatus.PARSED
    assert batches == [["Water", "Glycerin", "Niacinamide"]]


async def test_adapter_itself_attempts_no_session_write(monkeypatch):
    class WriteRejectingSession:
        def add(self, *args, **kwargs):
            pytest.fail("projection attempted session.add")

        async def flush(self, *args, **kwargs):
            pytest.fail("projection attempted session.flush")

        async def commit(self, *args, **kwargs):
            pytest.fail("projection attempted session.commit")

        async def delete(self, *args, **kwargs):
            pytest.fail("projection attempted session.delete")

        async def execute(self, *args, **kwargs):
            pytest.fail("adapter attempted its own query or update")

    session = WriteRejectingSession()

    async def _resolve(received_session, value):
        assert received_session is session
        assert value == "Water"
        return FormulaResolution(ParseStatus.PARSED)

    monkeypatch.setattr(formula_projection, "resolve_formula", _resolve)
    result = await project_formula_from_label_snapshot(session, _snapshot("Water"))
    assert result.formula.status is ParseStatus.PARSED


async def test_persisted_snapshot_is_field_equivalent_and_projection_writes_nothing(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _publish_identity(session, "water", "Water")
        scan = ScanEvent(
            barcode="8900000000009",
            outcome="label_captured",
            client_scan_id=f"projection-{uuid.uuid4()}",
            queued_offline=False,
            label_facts={"ingredients_text": "Water"},
        )
        session.add(scan)
        await session.flush()
        snapshot = LabelSnapshot(
            barcode=scan.barcode,
            scan_event_id=scan.id,
            facts={"ingredients_text": "Water", "brand": "Observed Brand"},
            confidence="user_confirmed",
            content_fingerprint="f" * 64,
            version_number=1,
            changed_fields=[],
            completeness="complete_for_grading",
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        snapshot_id = snapshot.id
        before = {
            column.name: copy.deepcopy(getattr(snapshot, column.name))
            for column in LabelSnapshot.__table__.columns
        }

    writes: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        verb = statement.lstrip().split(maxsplit=1)[0].upper()
        if verb in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    sync_engine = sql.get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", _record)
    try:
        async with factory() as session:
            persisted = await session.get(LabelSnapshot, snapshot_id)
            assert persisted is not None
            result = await project_formula_from_label_snapshot(session, persisted)
            assert result.formula.ingredients[0].substance_key == "water"
            assert not session.is_modified(persisted, include_collections=True)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _record)

    async with factory() as session:
        after_snapshot = await session.scalar(
            select(LabelSnapshot).where(LabelSnapshot.id == snapshot_id)
        )
        assert after_snapshot is not None
        after = {
            column.name: copy.deepcopy(getattr(after_snapshot, column.name))
            for column in LabelSnapshot.__table__.columns
        }

    assert writes == []
    assert after == before
