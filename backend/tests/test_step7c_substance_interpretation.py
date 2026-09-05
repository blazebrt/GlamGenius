"""Step 7C — category-specific public evidence over resolved formula rows."""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.domains.evidence.enums import (
    ClaimSourceRelationship,
    ClaimStatus,
    ClaimType,
    EvidenceDomain,
    EvidenceStrength,
    EvidenceTier,
    ReviewStatus,
    SourceStatus,
    SourceType,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.formulas.parser import ParseStatus
from app.domains.formulas.service import FormulaIngredientResolution, FormulaResolution
from app.domains.product.formula_projection import FormulaProjectionProvenance, LabelSnapshotFormulaProjection
from app.domains.substance_interpretation import service
from app.domains.substance_interpretation.enums import (
    InterpretationCategory,
    InterpretationStatus,
    ProjectedIdentityStatus,
)
from app.domains.substance_interpretation.schema import (
    INTERPRETATION_SCHEMA_VERSION,
    REFERENCE_ROLE_KIND,
    parse_interpretation_payload,
)
from app.domains.substance_interpretation.service import interpret_formula_projection
from app.domains.substances.service import ResolutionStatus
from app.shared.database import sql
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, func, select, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "app" / "domains" / "substance_interpretation"
BASE_REVISION = "z6a7b8c9d0"


def _provenance() -> FormulaProjectionProvenance:
    return FormulaProjectionProvenance(
        label_snapshot_id=uuid.uuid4(),
        barcode="8901234567890",
        version_number=3,
        content_fingerprint="a" * 64,
        scan_event_id=uuid.uuid4(),
    )


def _ingredient(
    raw_name: str = "Glycerin",
    *,
    position: int = 1,
    status: ResolutionStatus = ResolutionStatus.RESOLVED,
    substance_key: str | None = "glycerin",
    entity_kind: str | None = "defined_substance",
    candidates: tuple[str, ...] = ("glycerin",),
) -> FormulaIngredientResolution:
    return FormulaIngredientResolution(
        position=position,
        raw_name=raw_name,
        normalized_name=raw_name.casefold(),
        status=status,
        substance_key=substance_key,
        entity_kind=entity_kind,
        candidate_substance_keys=candidates,
    )


def _projection(
    *ingredients: FormulaIngredientResolution,
    status: ParseStatus = ParseStatus.PARSED,
) -> LabelSnapshotFormulaProjection:
    return LabelSnapshotFormulaProjection(
        provenance=_provenance(),
        formula=FormulaResolution(status=status, ingredients=tuple(ingredients)),
    )


def _payload(category: InterpretationCategory) -> dict[str, object]:
    return {
        "substance_category_interpretation": {
            "schema_version": INTERPRETATION_SCHEMA_VERSION,
            "category": category.value,
            "kind": REFERENCE_ROLE_KIND,
        },
        "publication_verification": {
            "source_opened": True,
            "founder_verified_fact": True,
            "claude_review_completed": True,
            "codex_review_completed": True,
            "independent_reviews_agree": True,
            "adversarial_review_passed": True,
            "unresolved_doubt": False,
        },
    }


async def _add_claim(
    session,
    *,
    category: InterpretationCategory = InterpretationCategory.SKIN_CARE,
    subject_key: str = "glycerin",
    claim_key: str | None = None,
    domain: str | None = None,
    subject_type: str = "substance",
    claim_type: str = ClaimType.SUBSTANCE_CATEGORY_INTERPRETATION.value,
    structured_value: object | None = None,
    review_status: str = ReviewStatus.PUBLISHED.value,
    claim_status: str = ClaimStatus.SUPPORTED.value,
    evidence_strength: str = EvidenceStrength.STRONG.value,
    evidence_tier: str = EvidenceTier.REFERENCE_DATA.value,
    ai_generated: bool = False,
    source_type: str = SourceType.INGREDIENT_REFERENCE_DATABASE.value,
    source_status: str = SourceStatus.ACTIVE.value,
    relationship: str = ClaimSourceRelationship.SUPPORTS.value,
    reviewed_link: bool = True,
    source_url: str | None = "https://example.org/reference/glycerin",
    source_title: str = "Ingredient reference entry",
    source_publisher: str = "Example Reference",
    license_note: str | None = "Used under the publisher's stated terms.",
) -> EvidenceClaim:
    now = utcnow()
    domain_by_category = {
        InterpretationCategory.PACKAGED_FOOD: EvidenceDomain.NUTRITION.value,
        InterpretationCategory.SKIN_CARE: EvidenceDomain.SKIN_CARE.value,
        InterpretationCategory.HAIR_CARE: EvidenceDomain.HAIR_CARE.value,
        InterpretationCategory.COSMETICS: EvidenceDomain.COSMETICS.value,
    }
    suffix = uuid.uuid4().hex
    source = EvidenceSource(
        source_key=f"source.{suffix}",
        source_series_key=f"series.{suffix}",
        source_type=source_type,
        title=source_title,
        publisher=source_publisher,
        publication_date=date(2025, 1, 2),
        version_or_revision="2025",
        jurisdiction="global",
        canonical_url=source_url,
        accessed_at=now,
        status=source_status,
        license_or_use_note=license_note,
    )
    claim = EvidenceClaim(
        claim_key=claim_key or f"interpretation.{suffix}",
        claim_version=1,
        domain=domain or domain_by_category[category],
        subject_type=subject_type,
        subject_key=subject_key,
        claim_type=claim_type,
        summary=f"Reviewed reference role for {subject_key}.",
        scope="Category-specific reference description only.",
        evidence_strength=evidence_strength,
        strength_rationale="The named reference records this role directly.",
        claim_status=claim_status,
        review_status=review_status,
        regulatory_context="unknown",
        structured_value=_payload(category) if structured_value is None else structured_value,
        ai_generated=ai_generated,
        evidence_tier=evidence_tier,
        reviewed_at=now if review_status != ReviewStatus.DRAFT.value else None,
        reviewed_by="reviewer" if review_status != ReviewStatus.DRAFT.value else None,
        published_at=now if review_status == ReviewStatus.PUBLISHED.value else None,
        published_by="publisher" if review_status == ReviewStatus.PUBLISHED.value else None,
        rejection_reason="Not accepted." if review_status == ReviewStatus.REJECTED.value else None,
    )
    session.add_all([source, claim])
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=claim.id,
        source_id=source.id,
        relationship=relationship,
        locator="section 4",
        reviewed_at=now if reviewed_link else None,
        reviewed_by="reviewer" if reviewed_link else None,
    ))
    await session.flush()
    return claim


async def _interpret(category: InterpretationCategory = InterpretationCategory.SKIN_CARE):
    factory = get_sessionmaker()
    async with factory() as session:
        return await interpret_formula_projection(session, _projection(_ingredient()), category=category)


class TestPayloadSchema:
    def test_valid_payload_and_category_enum_are_exact(self):
        parsed = parse_interpretation_payload(_payload(InterpretationCategory.SKIN_CARE))
        assert parsed is not None
        assert parsed.schema_version == "1"
        assert parsed.category is InterpretationCategory.SKIN_CARE
        assert parsed.kind == "reference_role"
        assert {row.value for row in InterpretationCategory} == {
            "packaged_food", "skin_care", "hair_care", "cosmetics",
        }

    @pytest.mark.parametrize("bad", [None, [], "", 7, {}, {"other": {}}, {"substance_category_interpretation": None}])
    def test_non_payloads_fail_closed(self, bad):
        assert parse_interpretation_payload(bad) is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("schema_version", None),
            ("schema_version", "2"),
            ("schema_version", 1),
            ("category", "skin"),
            ("category", "SKIN_CARE"),
            ("category", None),
            ("kind", "benefit"),
            ("kind", None),
        ],
    )
    def test_unknown_missing_and_untyped_fields_fail_closed(self, field, value):
        payload = _payload(InterpretationCategory.SKIN_CARE)
        payload["substance_category_interpretation"][field] = value
        assert parse_interpretation_payload(payload) is None

    def test_unknown_nested_key_fails_closed_but_governance_sibling_is_allowed(self):
        payload = _payload(InterpretationCategory.SKIN_CARE)
        assert parse_interpretation_payload(payload) is not None
        payload["substance_category_interpretation"]["risk"] = "low"
        assert parse_interpretation_payload(payload) is None


class TestFormulaAndIdentityStates:
    async def test_category_must_be_an_explicit_controlled_value(self):
        with pytest.raises(ValueError, match="InterpretationCategory"):
            await interpret_formula_projection(
                object(),
                _projection(status=ParseStatus.EMPTY),
                category="skin_care",
            )

    @pytest.mark.parametrize(
        "status",
        [
            ParseStatus.EMPTY,
            ParseStatus.MALFORMED,
            ParseStatus.AMBIGUOUS_BOUNDARY,
            ParseStatus.TOO_LONG,
            ParseStatus.TOO_MANY_ITEMS,
        ],
    )
    async def test_every_formula_failure_preserves_status_and_makes_zero_queries(self, db_clean, status):
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await interpret_formula_projection(
                    session,
                    _projection(status=status),
                    category=InterpretationCategory.SKIN_CARE,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert result.formula_status == status.value
        assert result.ingredients == ()
        assert statements == []

    async def test_unresolved_and_ambiguous_are_terminal_without_queries(self, db_clean):
        ambiguous_candidates = ("ceramide.np", "ceramide.ap")
        projection = _projection(
            _ingredient(
                "Unknown",
                status=ResolutionStatus.UNRESOLVED,
                substance_key=None,
                entity_kind=None,
                candidates=(),
            ),
            _ingredient(
                "Ceramide",
                position=2,
                status=ResolutionStatus.AMBIGUOUS,
                substance_key=None,
                entity_kind=None,
                candidates=ambiguous_candidates,
            ),
        )
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await interpret_formula_projection(
                    session, projection, category=InterpretationCategory.SKIN_CARE,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert [row.interpretation_status for row in result.ingredients] == [
            InterpretationStatus.IDENTITY_UNRESOLVED,
            InterpretationStatus.IDENTITY_AMBIGUOUS,
        ]
        assert result.ingredients[1].candidate_substance_keys == ambiguous_candidates
        assert all(row.claims == () for row in result.ingredients)
        assert statements == []

    async def test_resolved_without_a_canonical_key_fails_closed_without_queries(self, db_clean):
        projection = _projection(_ingredient(substance_key=None, candidates=()))
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await interpret_formula_projection(
                    session, projection, category=InterpretationCategory.SKIN_CARE,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert result.ingredients[0].interpretation_status is InterpretationStatus.NOT_ENOUGH_INFORMATION
        assert result.ingredients[0].claims == ()
        assert statements == []

    async def test_ambiguity_never_looks_up_or_selects_a_candidate(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, subject_key="ceramide.np")
            await _add_claim(session, subject_key="ceramide.ap")
            await session.commit()
        projection = _projection(_ingredient(
            "Ceramide",
            status=ResolutionStatus.AMBIGUOUS,
            substance_key=None,
            entity_kind=None,
            candidates=("ceramide.ap", "ceramide.np"),
        ))
        async with factory() as session:
            result = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
        row = result.ingredients[0]
        assert row.identity_status is ProjectedIdentityStatus.AMBIGUOUS
        assert row.candidate_substance_keys == ("ceramide.ap", "ceramide.np")
        assert row.claims == ()

    async def test_group_and_mixture_remain_exact_entities(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, subject_key="ceramides.group", claim_key="group.role")
            await _add_claim(session, subject_key="ceramide.np", claim_key="member.role")
            await _add_claim(session, subject_key="fragrance.mixture", claim_key="mixture.role")
            await _add_claim(session, subject_key="limonene", claim_key="component.role")
            await session.commit()
        projection = _projection(
            _ingredient("Ceramides", substance_key="ceramides.group", entity_kind="group", candidates=("ceramides.group",)),
            _ingredient(
                "Fragrance Blend", position=2, substance_key="fragrance.mixture",
                entity_kind="mixture", candidates=("fragrance.mixture",),
            ),
        )
        async with factory() as session:
            result = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
        assert [(row.entity_kind, [claim.claim_key for claim in row.claims]) for row in result.ingredients] == [
            ("group", ["group.role"]),
            ("mixture", ["mixture.role"]),
        ]


class TestEvidenceEligibility:
    async def test_valid_claims_sources_provenance_order_and_duplicates(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            later = await _add_claim(session, claim_key="z.role")
            earlier = await _add_claim(session, claim_key="a.role")
            second_source = EvidenceSource(
                source_key="a.second.source",
                source_series_key="a.second.series",
                source_type=SourceType.OFFICIAL_GUIDELINE.value,
                title="Official reference",
                publisher="Public Authority",
                canonical_url="https://example.org/official/glycerin",
                accessed_at=utcnow(),
                status=SourceStatus.ACTIVE.value,
                license_or_use_note="Public terms recorded.",
            )
            session.add(second_source)
            await session.flush()
            session.add(EvidenceClaimSource(
                claim_id=earlier.id,
                source_id=second_source.id,
                relationship=ClaimSourceRelationship.SUPPORTS.value,
                locator="page 2",
                reviewed_at=utcnow(),
                reviewed_by="reviewer",
            ))
            await session.commit()
            assert later.id != earlier.id
        projection = _projection(
            _ingredient(position=1),
            _ingredient(position=2),
        )
        async with factory() as session:
            result = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
        assert result.provenance == projection.provenance
        assert [row.position for row in result.ingredients] == [1, 2]
        assert all(row.interpretation_status is InterpretationStatus.EVIDENCE_AVAILABLE for row in result.ingredients)
        assert [[claim.claim_key for claim in row.claims] for row in result.ingredients] == [
            ["a.role", "z.role"], ["a.role", "z.role"],
        ]
        first = result.ingredients[0].claims[0]
        assert first.summary == "Reviewed reference role for glycerin."
        assert first.scope == "Category-specific reference description only."
        assert first.evidence_strength == EvidenceStrength.STRONG.value
        assert first.evidence_tier == EvidenceTier.REFERENCE_DATA.value
        assert [source.source_key for source in first.sources] == sorted(source.source_key for source in first.sources)
        assert all(source.canonical_url.startswith("https://") for source in first.sources)

    @pytest.mark.parametrize(
        ("category", "domain"),
        [
            (InterpretationCategory.PACKAGED_FOOD, EvidenceDomain.NUTRITION.value),
            (InterpretationCategory.SKIN_CARE, EvidenceDomain.SKIN_CARE.value),
            (InterpretationCategory.HAIR_CARE, EvidenceDomain.HAIR_CARE.value),
            (InterpretationCategory.COSMETICS, EvidenceDomain.COSMETICS.value),
        ],
    )
    async def test_explicit_category_maps_to_one_exact_evidence_domain(self, db_clean, category, domain):
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _add_claim(session, category=category)
            await session.commit()
            assert claim.domain == domain
        result = await _interpret(category)
        assert result.category is category
        assert result.ingredients[0].interpretation_status is InterpretationStatus.EVIDENCE_AVAILABLE

    async def test_skincare_and_cosmetics_are_isolated(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, category=InterpretationCategory.COSMETICS, claim_key="cosmetics.only")
            await session.commit()
        skin = await _interpret(InterpretationCategory.SKIN_CARE)
        cosmetics = await _interpret(InterpretationCategory.COSMETICS)
        assert skin.ingredients[0].interpretation_status is InterpretationStatus.NOT_ENOUGH_INFORMATION
        assert [claim.claim_key for claim in cosmetics.ingredients[0].claims] == ["cosmetics.only"]

    @pytest.mark.parametrize(
        "overrides",
        [
            {"review_status": ReviewStatus.DRAFT.value},
            {"review_status": ReviewStatus.APPROVED.value},
            {"review_status": ReviewStatus.REJECTED.value},
            {"review_status": ReviewStatus.SUPERSEDED.value},
            {"review_status": ReviewStatus.RETIRED.value},
            {"claim_status": ClaimStatus.UNSUPPORTED.value},
            {"claim_status": ClaimStatus.CONFLICTING.value},
            {"ai_generated": True},
            {"evidence_tier": EvidenceTier.CLINICALLY_STUDIED.value},
            {"evidence_tier": EvidenceTier.NOT_ENOUGH_INFORMATION.value},
            {"evidence_strength": EvidenceStrength.INSUFFICIENT.value},
            {"evidence_strength": EvidenceStrength.TRADITIONAL.value},
            {"claim_type": ClaimType.USAGE_CONTEXT.value},
            {"subject_type": "ingredient"},
            {"subject_key": "not.glycerin"},
            {"domain": EvidenceDomain.HAIR_CARE.value},
        ],
    )
    async def test_each_ineligible_claim_state_is_inert(self, db_clean, overrides):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, **overrides)
            await session.commit()
        result = await _interpret()
        assert result.ingredients[0].interpretation_status is InterpretationStatus.NOT_ENOUGH_INFORMATION
        assert result.ingredients[0].claims == ()

    @pytest.mark.parametrize(
        "mutator",
        [
            lambda value: value.pop("publication_verification"),
            lambda value: value["publication_verification"].update({"source_opened": False}),
            lambda value: value["publication_verification"].update({"unresolved_doubt": True}),
            lambda value: value["substance_category_interpretation"].update({"schema_version": "2"}),
            lambda value: value["substance_category_interpretation"].update({"kind": "benefit"}),
            lambda value: value["substance_category_interpretation"].update({"category": "hair_care"}),
            lambda value: value["substance_category_interpretation"].update({"unknown": True}),
        ],
    )
    async def test_malformed_unverified_and_disagreeing_payloads_are_inert(self, db_clean, mutator):
        payload = _payload(InterpretationCategory.SKIN_CARE)
        mutator(payload)
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, structured_value=payload)
            await session.commit()
        result = await _interpret()
        assert result.ingredients[0].claims == ()

    @pytest.mark.parametrize(
        "source_type",
        [
            SourceType.OFFICIAL_REGULATION.value,
            SourceType.OFFICIAL_GUIDELINE.value,
            SourceType.GOVERNMENT_REFERENCE.value,
            SourceType.INGREDIENT_REFERENCE_DATABASE.value,
            SourceType.MANUFACTURER_TECHNICAL_DOCUMENT.value,
        ],
    )
    async def test_every_allowed_source_type_can_support_reference_role(self, db_clean, source_type):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, source_type=source_type)
            await session.commit()
        result = await _interpret()
        assert result.ingredients[0].interpretation_status is InterpretationStatus.EVIDENCE_AVAILABLE

    @pytest.mark.parametrize(
        "overrides",
        [
            {"source_type": SourceType.MANUFACTURER_CLAIM.value},
            {"source_type": SourceType.MANUFACTURER_LABEL.value},
            {"source_type": SourceType.OTHER.value},
            {"source_type": SourceType.TRADITIONAL_REFERENCE.value},
            {"source_status": SourceStatus.RETIRED.value},
            {"source_status": SourceStatus.SUPERSEDED.value},
            {"source_status": SourceStatus.UNAVAILABLE.value},
            {"relationship": ClaimSourceRelationship.BACKGROUND.value},
            {"relationship": ClaimSourceRelationship.QUALIFIES.value},
            {"relationship": ClaimSourceRelationship.LIMITS.value},
            {"relationship": ClaimSourceRelationship.CONTRADICTS.value},
            {"reviewed_link": False},
            {"source_url": None},
            {"source_url": "not-a-url"},
            {"source_title": ""},
            {"source_publisher": ""},
            {"license_note": None},
        ],
    )
    async def test_each_ineligible_source_path_is_inert(self, db_clean, overrides):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, **overrides)
            await session.commit()
        result = await _interpret()
        assert result.ingredients[0].claims == ()

    async def test_invalid_claim_cannot_hide_a_valid_claim(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, claim_key="a.invalid", evidence_tier=EvidenceTier.AVOID.value)
            await _add_claim(session, claim_key="z.valid")
            await session.commit()
        result = await _interpret()
        assert [claim.claim_key for claim in result.ingredients[0].claims] == ["z.valid"]


class TestLiveReadOnlyProjection:
    async def test_live_knowledge_changes_without_snapshot_or_projection_mutation(self, db_clean):
        projection = _projection(_ingredient())
        before = await _interpret()
        assert before.ingredients[0].claims == ()

        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _add_claim(session, claim_key="live.role")
            claim_id = claim.id
            await session.commit()
        async with factory() as session:
            after_publish = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
        assert [row.claim_key for row in after_publish.ingredients[0].claims] == ["live.role"]
        assert after_publish.provenance == projection.provenance

        async with factory() as session:
            claim = await session.get(EvidenceClaim, claim_id)
            claim.review_status = ReviewStatus.RETIRED.value
            await session.commit()
        async with factory() as session:
            after_retire = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
        assert after_retire.ingredients[0].claims == ()
        assert after_retire.provenance == projection.provenance

    async def test_runtime_performs_no_writes(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session)
            await session.commit()
            before = (
                await session.scalar(select(func.count(EvidenceClaim.id))),
                await session.scalar(select(func.count(EvidenceClaimSource.id))),
                await session.scalar(select(func.count(EvidenceSource.id))),
            )
            await interpret_formula_projection(
                session, _projection(_ingredient()), category=InterpretationCategory.SKIN_CARE,
            )
            assert not session.new and not session.dirty and not session.deleted
            after = (
                await session.scalar(select(func.count(EvidenceClaim.id))),
                await session.scalar(select(func.count(EvidenceClaimSource.id))),
                await session.scalar(select(func.count(EvidenceSource.id))),
            )
        assert after == before

    async def test_convenience_function_projects_the_exact_supplied_snapshot(self, monkeypatch):
        snapshot = object()
        projection = _projection(status=ParseStatus.EMPTY)
        calls: list[tuple[object, object]] = []

        async def project(session, supplied):
            calls.append((session, supplied))
            return projection

        monkeypatch.setattr(service, "project_formula_from_label_snapshot", project)
        session = object()
        result = await service.interpret_label_snapshot(
            session, snapshot, category=InterpretationCategory.HAIR_CARE,
        )
        assert calls == [(session, snapshot)]
        assert result.provenance == projection.provenance


class TestQueryBudget:
    async def _query_count(self, projection):
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await interpret_formula_projection(
                    session, projection, category=InterpretationCategory.SKIN_CARE,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        return statements

    async def test_resolved_formula_without_candidates_uses_one_query(self, db_clean):
        statements = await self._query_count(_projection(*[
            _ingredient(f"Item {index}", position=index + 1, substance_key=f"entity.{index}", candidates=(f"entity.{index}",))
            for index in range(25)
        ]))
        assert len(statements) == 1

    async def test_any_formula_length_with_candidates_uses_two_queries(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            for index in range(12):
                await _add_claim(session, subject_key=f"entity.{index}")
            await session.commit()
        statements = await self._query_count(_projection(*[
            _ingredient(f"Item {index}", position=index + 1, substance_key=f"entity.{index}", candidates=(f"entity.{index}",))
            for index in range(12)
        ]))
        assert len(statements) == 2


def _package_of(path: Path) -> str:
    return ".".join(path.relative_to(BACKEND_ROOT).parts[:-1])


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _package_of(path).split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if prefix:
                found.add(prefix)
                found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


class TestArchitecture:
    def test_domain_owns_only_the_deliberate_modules(self):
        assert {path.name for path in DOMAIN_DIR.glob("*.py")} == {
            "__init__.py", "enums.py", "schema.py", "service.py",
        }

    @pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_step7c_imports_only_its_intentional_dependencies(self, path):
        allowed = (
            "app.domains.substance_interpretation",
            "app.domains.product.formula_projection",
            "app.domains.product.models",
            "app.domains.evidence",
            "sqlalchemy",
            "dataclasses",
            "collections",
            "enum",
            "typing",
            "uuid",
            "datetime",
            "__future__",
        )
        for module in _imported_modules(path):
            assert module.startswith(allowed), f"{path.name} imports {module}"

    @pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_step7c_never_reopens_identity_formula_or_other_decision_layers(self, path):
        imported = _imported_modules(path)
        forbidden = (
            "app.domains.substances", "app.domains.formulas", "app.domains.off",
            "app.domains.ai_gateway", "app.domains.routines", "app.domains.care",
            "app.domains.supplements", "app.domains.alternatives",
            "app.domains.recommendation", "app.domains.purchase", "httpx",
            "requests", "aiohttp", "urllib", "socket", "google.genai",
        )
        for prefix in forbidden:
            assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported)

    def test_runtime_has_no_write_or_snapshot_selection_api(self):
        body = inspect.getsource(service)
        for forbidden in (
            "session.add", "session.flush", "session.commit", "session.delete",
            "latest_label_snapshot", "latest_label_snapshots",
        ):
            assert forbidden not in body

    def test_output_has_no_decision_fields(self):
        fields = set(service.FormulaIngredientInterpretation.__dataclass_fields__)
        for forbidden in (
            "risk", "score", "grade", "verdict", "benefit", "safety",
            "concentration", "recommendation", "confidence",
        ):
            assert forbidden not in fields


async def _alembic(command: str, revision: str) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        command,
        revision,
        cwd=BACKEND_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    assert process.returncode == 0, output.decode(errors="replace")


async def test_populated_migration_downgrade_removes_only_new_vocabulary_and_reupgrades(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _add_claim(
            session,
            category=InterpretationCategory.COSMETICS,
            claim_key="cosmetics.step7c",
        )
        await _add_claim(
            session,
            category=InterpretationCategory.COSMETICS,
            claim_key="cosmetics.old-claim-type",
            claim_type=ClaimType.USAGE_CONTEXT.value,
        )
        skincare = await _add_claim(
            session,
            category=InterpretationCategory.SKIN_CARE,
            claim_key="skincare.step7c",
        )
        legacy = EvidenceClaim(
            claim_key="legacy.nutrition",
            claim_version=1,
            domain=EvidenceDomain.NUTRITION.value,
            subject_type="food",
            subject_key="example",
            claim_type=ClaimType.NUTRITION_REFERENCE.value,
            summary="Legacy row.",
            scope="Migration control.",
            review_status=ReviewStatus.DRAFT.value,
            regulatory_context="unknown",
            ai_generated=False,
            supersedes_claim_id=skincare.id,
        )
        session.add(legacy)
        await session.flush()
        session.add(RuleEvidenceLink(
            domain=EvidenceDomain.COSMETICS.value,
            rule_kind="routine_guidance",
            rule_id="migration-control",
            rule_version="1",
            claim_id=legacy.id,
            relationship="background",
        ))
        await session.commit()

    await sql.dispose_engine()
    upgraded = False
    try:
        await _alembic("downgrade", BASE_REVISION)
        async with sql.get_engine().connect() as connection:
            remaining = (await connection.execute(text(
                "SELECT claim_key, supersedes_claim_id FROM evidence_claims ORDER BY claim_key"
            ))).all()
            assert remaining == [("legacy.nutrition", None)]
            assert await connection.scalar(select(func.count()).select_from(RuleEvidenceLink.__table__)) == 0
        await sql.dispose_engine()
        await _alembic("upgrade", "head")
        upgraded = True
        async with sql.get_engine().connect() as connection:
            domain_check = await connection.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_evidence_claims_domain'"
            ))
            claim_check = await connection.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_evidence_claims_claim_type'"
            ))
        assert "cosmetics" in domain_check
        assert "substance_category_interpretation" in claim_check
    finally:
        if not upgraded:
            await sql.dispose_engine()
            await _alembic("upgrade", "head")
        await sql.dispose_engine()
