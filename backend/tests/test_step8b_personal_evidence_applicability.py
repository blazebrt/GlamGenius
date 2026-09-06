"""Step 8B — governed personal evidence applicability over Steps 8A and 7C."""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import inspect
import sys
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

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
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.formulas.parser import ParseStatus
from app.domains.formulas.service import FormulaIngredientResolution, FormulaResolution
from app.domains.identity import service as identity_service
from app.domains.personal_applicability import service
from app.domains.personal_applicability.enums import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityOperator,
    PersonalApplicabilityStatus,
)
from app.domains.personal_applicability.schema import (
    MAX_PERSONAL_APPLICABILITY_CONDITIONS,
    PERSONAL_APPLICABILITY_SCHEMA_VERSION,
    parse_personal_applicability_payload,
)
from app.domains.personal_applicability.service import (
    apply_personal_evidence,
    interpret_label_snapshot_for_account,
)
from app.domains.personal_lens.enums import PersonalLensCategory, PersonalLensStatus
from app.domains.personal_lens.service import (
    PersonalLensContext,
    PersonalLensFact,
    PersonalLensSafetyInput,
    build_personal_lens_context,
)
from app.domains.product.formula_projection import FormulaProjectionProvenance, LabelSnapshotFormulaProjection
from app.domains.profile.models import AppearanceProfile, ProfileAttribute
from app.domains.substance_interpretation.enums import (
    InterpretationCategory,
    InterpretationStatus,
    ProjectedIdentityStatus,
)
from app.domains.substance_interpretation.service import (
    FormulaIngredientInterpretation,
    LabelSnapshotFormulaInterpretation,
    interpret_formula_projection,
)
from app.domains.substances.service import ResolutionStatus
from app.shared.database import sql
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "app" / "domains" / "personal_applicability"
BASE_REVISION = "a7b8c9d0e1"


def _verification() -> dict[str, bool]:
    return {
        "source_opened": True,
        "founder_verified_fact": True,
        "claude_review_completed": True,
        "codex_review_completed": True,
        "independent_reviews_agree": True,
        "adversarial_review_passed": True,
        "unresolved_doubt": False,
    }


def _payload(
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    *conditions: dict[str, object],
) -> dict[str, object]:
    if not conditions:
        conditions = ({
            "fact_key": "care_skin_sensitivity",
            "operator": "equals_any",
            "values": ["sometimes_reactive", "often_reactive"],
        },)
    return {
        "substance_personal_applicability": {
            "schema_version": PERSONAL_APPLICABILITY_SCHEMA_VERSION,
            "category": category.value,
            "all_of": list(conditions),
        },
        "publication_verification": _verification(),
    }


def _fact(key: str, value: object, *, explicit_unknown: bool = False) -> PersonalLensFact:
    return PersonalLensFact(
        key=key,
        value=value,
        source="user_declared",
        verification_state="confirmed",
        profile_attribute_id=uuid.uuid4(),
        explicit_unknown=explicit_unknown,
        last_reviewed_at=None,
    )


def _context(
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    *facts: PersonalLensFact,
    status: PersonalLensStatus = PersonalLensStatus.PARTIAL_CONTEXT,
) -> PersonalLensContext:
    return PersonalLensContext(
        category=PersonalLensCategory(category.value),
        status=status,
        profile_id=uuid.uuid4(),
        profile_version=7,
        body_facts=tuple(facts) or (_fact("care_skin_sensitivity", "sometimes_reactive"),),
        preference_facts=(),
        missing_information=(),
        handoff=None,
    )


def _provenance() -> FormulaProjectionProvenance:
    return FormulaProjectionProvenance(
        label_snapshot_id=uuid.uuid4(),
        barcode="8901234567890",
        version_number=4,
        content_fingerprint="b" * 64,
        scan_event_id=uuid.uuid4(),
    )


def _ingredient(
    raw_name: str = "Glycerin",
    *,
    position: int = 1,
    status: ProjectedIdentityStatus = ProjectedIdentityStatus.RESOLVED,
    substance_key: str | None = "glycerin",
    candidates: tuple[str, ...] = ("glycerin",),
    reference_claims: tuple = (),
) -> FormulaIngredientInterpretation:
    return FormulaIngredientInterpretation(
        position=position,
        raw_name=raw_name,
        normalized_name=raw_name.casefold(),
        identity_status=status,
        substance_key=substance_key,
        entity_kind="defined_substance" if substance_key else None,
        candidate_substance_keys=candidates,
        interpretation_status=(
            InterpretationStatus.EVIDENCE_AVAILABLE
            if reference_claims
            else InterpretationStatus.NOT_ENOUGH_INFORMATION
        ),
        claims=reference_claims,
    )


def _interpretation(
    *ingredients: FormulaIngredientInterpretation,
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
) -> LabelSnapshotFormulaInterpretation:
    return LabelSnapshotFormulaInterpretation(
        provenance=_provenance(),
        category=InterpretationCategory(category.value),
        formula_status=ParseStatus.PARSED.value,
        ingredients=tuple(ingredients) or (_ingredient(),),
    )


async def _add_claim(
    session,
    *,
    category: PersonalApplicabilityCategory = PersonalApplicabilityCategory.SKIN_CARE,
    subject_key: str = "glycerin",
    claim_key: str | None = None,
    domain: str | None = None,
    subject_type: str = "substance",
    claim_type: str = ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value,
    structured_value: object | None = None,
    review_status: str = ReviewStatus.PUBLISHED.value,
    claim_status: str = ClaimStatus.SUPPORTED.value,
    evidence_strength: str = EvidenceStrength.STRONG.value,
    evidence_tier: str = EvidenceTier.CLINICALLY_STUDIED.value,
    ai_generated: bool = False,
    source_type: str = SourceType.PEER_REVIEWED_RESEARCH.value,
    source_status: str = SourceStatus.ACTIVE.value,
    relationship: str = ClaimSourceRelationship.SUPPORTS.value,
    reviewed_link: bool = True,
    source_url: str | None = "https://example.org/research/glycerin",
    source_title: str = "Reviewed research",
    source_publisher: str = "Example Journal",
    license_note: str | None = "Use recorded for evidence review.",
) -> EvidenceClaim:
    now = utcnow()
    suffix = uuid.uuid4().hex
    source = EvidenceSource(
        source_key=f"personal.source.{suffix}",
        source_series_key=f"personal.series.{suffix}",
        source_type=source_type,
        title=source_title,
        publisher=source_publisher,
        publication_date=date(2025, 2, 3),
        version_or_revision="2025",
        jurisdiction="global",
        canonical_url=source_url,
        accessed_at=now,
        status=source_status,
        license_or_use_note=license_note,
    )
    claim = EvidenceClaim(
        claim_key=claim_key or f"personal.claim.{suffix}",
        claim_version=1,
        domain=domain or {
            PersonalApplicabilityCategory.PACKAGED_FOOD: EvidenceDomain.NUTRITION.value,
            PersonalApplicabilityCategory.SKIN_CARE: EvidenceDomain.SKIN_CARE.value,
            PersonalApplicabilityCategory.HAIR_CARE: EvidenceDomain.HAIR_CARE.value,
            PersonalApplicabilityCategory.COSMETICS: EvidenceDomain.COSMETICS.value,
        }[category],
        subject_type=subject_type,
        subject_key=subject_key,
        claim_type=claim_type,
        summary="The reviewed source reports this scoped body-context relationship.",
        scope="Exact declared fact values and the named substance only.",
        evidence_strength=evidence_strength,
        strength_rationale="The named independent source directly supports the scoped claim.",
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
        locator="section 2",
        reviewed_at=now if reviewed_link else None,
        reviewed_by="reviewer" if reviewed_link else None,
    ))
    await session.flush()
    return claim


class TestPayload:
    def test_valid_scalar_equals_any(self):
        parsed = parse_personal_applicability_payload(_payload())
        assert parsed is not None
        assert parsed.category is PersonalApplicabilityCategory.SKIN_CARE
        assert parsed.all_of[0].operator is PersonalApplicabilityOperator.EQUALS_ANY

    def test_valid_list_contains_any(self):
        payload = _payload(
            PersonalApplicabilityCategory.HAIR_CARE,
            {
                "fact_key": "care_hair_processing",
                "operator": "contains_any",
                "values": ["coloured", "bleached"],
            },
        )
        parsed = parse_personal_applicability_payload(payload)
        assert parsed is not None
        assert parsed.all_of[0].values == ("coloured", "bleached")

    def test_multiple_all_of_conditions_preserve_exact_order(self):
        parsed = parse_personal_applicability_payload(_payload(
            PersonalApplicabilityCategory.SKIN_CARE,
            {"fact_key": "care_skin_usual_feel", "operator": "equals_any", "values": ["comfortable"]},
            {"fact_key": "care_skin_sensitivity", "operator": "equals_any", "values": ["rarely_reactive"]},
        ))
        assert parsed is not None
        assert tuple(row.fact_key for row in parsed.all_of) == (
            "care_skin_usual_feel", "care_skin_sensitivity",
        )

    @pytest.mark.parametrize(
        "mutator",
        [
            lambda value: value.update({"substance_personal_applicability": None}),
            lambda value: value["substance_personal_applicability"].update({"schema_version": "2"}),
            lambda value: value["substance_personal_applicability"].update({"category": "skin"}),
            lambda value: value["substance_personal_applicability"].update({"unknown": True}),
            lambda value: value["substance_personal_applicability"].update({"all_of": []}),
            lambda value: value["substance_personal_applicability"].update({"all_of": "condition"}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"unknown": True}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"fact_key": "unknown"}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update(
                {"fact_key": "care_fragrance_preference"}
            ),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update(
                {"operator": "contains_any"}
            ),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"values": []}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"values": "often_reactive"}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"values": [""]}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"values": [7]}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update({"values": ["not_sure"]}),
            lambda value: value["substance_personal_applicability"]["all_of"][0].update(
                {"values": ["often_reactive", "often_reactive"]}
            ),
            lambda value: value["substance_personal_applicability"]["all_of"].append(
                {"fact_key": "care_skin_sensitivity", "operator": "equals_any", "values": ["rarely_reactive"]}
            ),
        ],
    )
    def test_malformed_unknown_preference_and_duplicate_payloads_fail_closed(self, mutator):
        payload = _payload()
        mutator(payload)
        assert parse_personal_applicability_payload(payload) is None

    def test_excessive_condition_count_fails_closed(self):
        condition = {
            "fact_key": "care_skin_sensitivity",
            "operator": "equals_any",
            "values": ["rarely_reactive"],
        }
        payload = _payload()
        payload["substance_personal_applicability"]["all_of"] = [
            {**condition, "fact_key": f"fact.{index}"}
            for index in range(MAX_PERSONAL_APPLICABILITY_CONDITIONS + 1)
        ]
        assert parse_personal_applicability_payload(payload) is None

    def test_profile_values_are_exact_and_typos_are_not_normalized(self):
        canonical = _payload()
        typo = _payload()
        typo["substance_personal_applicability"]["all_of"][0]["values"] = ["often-reactive"]
        assert parse_personal_applicability_payload(canonical) is not None
        assert parse_personal_applicability_payload(typo) is None

    def test_category_fact_allowlist_is_exact(self):
        wrong = _payload(
            PersonalApplicabilityCategory.HAIR_CARE,
            {"fact_key": "care_skin_sensitivity", "operator": "equals_any", "values": ["rarely_reactive"]},
        )
        assert parse_personal_applicability_payload(wrong) is None


class TestMatching:
    async def test_scalar_list_and_all_of_only_match_when_every_condition_matches(self, db_clean):
        payload = _payload(
            PersonalApplicabilityCategory.HAIR_CARE,
            {"fact_key": "care_hair_pattern", "operator": "equals_any", "values": ["curly"]},
            {"fact_key": "care_hair_processing", "operator": "contains_any", "values": ["coloured"]},
        )
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(
                session,
                category=PersonalApplicabilityCategory.HAIR_CARE,
                structured_value=payload,
            )
            await session.commit()
            matching = await apply_personal_evidence(
                session,
                _interpretation(_ingredient(), category=PersonalApplicabilityCategory.HAIR_CARE),
                _context(
                    PersonalApplicabilityCategory.HAIR_CARE,
                    _fact("care_hair_pattern", "curly"),
                    _fact("care_hair_processing", ("coloured",)),
                ),
                category=PersonalApplicabilityCategory.HAIR_CARE,
            )
            missing_one = await apply_personal_evidence(
                session,
                _interpretation(_ingredient(), category=PersonalApplicabilityCategory.HAIR_CARE),
                _context(
                    PersonalApplicabilityCategory.HAIR_CARE,
                    _fact("care_hair_pattern", "straight"),
                    _fact("care_hair_processing", ("coloured",)),
                ),
                category=PersonalApplicabilityCategory.HAIR_CARE,
            )
        assert matching[0].personal_applicability_status is PersonalApplicabilityStatus.PERSONAL_EVIDENCE_AVAILABLE
        assert tuple(row.fact_key for row in matching[0].claims[0].matched_facts) == (
            "care_hair_pattern", "care_hair_processing",
        )
        assert missing_one[0].personal_applicability_status is PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION

    async def test_partial_context_matches_present_fact_and_missing_fact_blocks_only_its_claim(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, claim_key="a.present")
            await _add_claim(
                session,
                claim_key="b.missing",
                structured_value=_payload(
                    PersonalApplicabilityCategory.SKIN_CARE,
                    {"fact_key": "care_skin_usual_feel", "operator": "equals_any", "values": ["comfortable"]},
                ),
            )
            await session.commit()
            result = await apply_personal_evidence(
                session,
                _interpretation(),
                _context(),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert [claim.claim_key for claim in result[0].claims] == ["a.present"]

    async def test_explicit_unknown_never_matches(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session)
            await session.commit()
            result = await apply_personal_evidence(
                session,
                _interpretation(),
                _context(
                    PersonalApplicabilityCategory.SKIN_CARE,
                    _fact("care_skin_sensitivity", "sometimes_reactive", explicit_unknown=True),
                ),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims == ()

    async def test_preference_facts_cannot_activate_body_evidence(self, db_clean):
        context = _context()
        context = dataclasses.replace(
            context,
            body_facts=(),
            preference_facts=(_fact("care_fragrance_preference", "fragrance_free_preferred"),),
        )
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session)
            await session.commit()
            result = await apply_personal_evidence(
                session,
                _interpretation(),
                context,
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims == ()

    @pytest.mark.parametrize("category", list(PersonalApplicabilityCategory))
    async def test_category_to_evidence_domain_is_explicit(self, db_clean, category):
        if category is PersonalApplicabilityCategory.PACKAGED_FOOD:
            context = _context(category, status=PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT)
        elif category is PersonalApplicabilityCategory.HAIR_CARE:
            context = _context(category, _fact("care_hair_pattern", "curly"))
        else:
            context = _context(category)
        factory = get_sessionmaker()
        async with factory() as session:
            result = await apply_personal_evidence(
                session,
                _interpretation(_ingredient(), category=category),
                context,
                category=category,
            )
        assert result[0].personal_applicability_status is PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION

    async def test_category_mismatch_is_never_guessed(self):
        with pytest.raises(ValueError, match="formula interpretation category"):
            await apply_personal_evidence(
                object(),
                _interpretation(),
                _context(PersonalApplicabilityCategory.HAIR_CARE, _fact("care_hair_pattern", "curly")),
                category=PersonalApplicabilityCategory.HAIR_CARE,
            )
        with pytest.raises(ValueError, match="personal context category"):
            await apply_personal_evidence(
                object(),
                _interpretation(),
                _context(PersonalApplicabilityCategory.HAIR_CARE, _fact("care_hair_pattern", "curly")),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )


class TestIdentityBoundary:
    async def test_resolved_uses_exact_key_and_duplicates_preserve_positions(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, subject_key="glycerin")
            await _add_claim(session, subject_key="not.glycerin", claim_key="wrong.key")
            await session.commit()
            result = await apply_personal_evidence(
                session,
                _interpretation(_ingredient(position=1), _ingredient(position=2)),
                _context(),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert [row.position for row in result] == [1, 2]
        assert [[claim.claim_key for claim in row.claims] for row in result] == [
            [result[0].claims[0].claim_key], [result[0].claims[0].claim_key],
        ]
        assert result[0].claims[0].claim_key != "wrong.key"

    async def test_unresolved_and_ambiguous_are_terminal_and_preserve_candidates(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, subject_key="Glycerin", claim_key="raw.name")
            await _add_claim(session, subject_key="ceramide.ap", claim_key="candidate")
            await session.commit()
            result = await apply_personal_evidence(
                session,
                _interpretation(
                    _ingredient(
                        status=ProjectedIdentityStatus.UNRESOLVED,
                        substance_key=None,
                        candidates=(),
                    ),
                    _ingredient(
                        "Ceramide",
                        position=2,
                        status=ProjectedIdentityStatus.AMBIGUOUS,
                        substance_key=None,
                        candidates=("ceramide.ap", "ceramide.np"),
                    ),
                ),
                _context(),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].personal_applicability_status is PersonalApplicabilityStatus.IDENTITY_UNRESOLVED
        assert result[1].personal_applicability_status is PersonalApplicabilityStatus.IDENTITY_AMBIGUOUS
        assert result[1].candidate_substance_keys == ("ceramide.ap", "ceramide.np")
        assert all(row.claims == () for row in result)

    async def test_only_unresolved_and_ambiguous_rows_make_zero_queries(self, db_clean):
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await apply_personal_evidence(
                    session,
                    _interpretation(
                        _ingredient(status=ProjectedIdentityStatus.UNRESOLVED, substance_key=None, candidates=()),
                        _ingredient(
                            position=2,
                            status=ProjectedIdentityStatus.AMBIGUOUS,
                            substance_key=None,
                            candidates=("a", "b"),
                        ),
                    ),
                    _context(),
                    category=PersonalApplicabilityCategory.SKIN_CARE,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert statements == []


class TestReferenceRoleSeparation:
    async def test_step7c_reference_role_never_becomes_personal_evidence(self, db_clean):
        from app.domains.substance_interpretation.schema import (
            INTERPRETATION_SCHEMA_VERSION,
            REFERENCE_ROLE_KIND,
        )

        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(
                session,
                claim_key="reference.humectant",
                claim_type=ClaimType.SUBSTANCE_CATEGORY_INTERPRETATION.value,
                evidence_tier=EvidenceTier.REFERENCE_DATA.value,
                source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
                structured_value={
                    "substance_category_interpretation": {
                        "schema_version": INTERPRETATION_SCHEMA_VERSION,
                        "category": "skin_care",
                        "kind": REFERENCE_ROLE_KIND,
                    },
                    "publication_verification": _verification(),
                },
            )
            await session.commit()
            projection = LabelSnapshotFormulaProjection(
                provenance=_provenance(),
                formula=FormulaResolution(
                    status=ParseStatus.PARSED,
                    ingredients=(FormulaIngredientResolution(
                        position=1,
                        raw_name="Glycerin",
                        normalized_name="glycerin",
                        status=ResolutionStatus.RESOLVED,
                        substance_key="glycerin",
                        entity_kind="defined_substance",
                        candidate_substance_keys=("glycerin",),
                    ),),
                ),
            )
            step7c = await interpret_formula_projection(
                session, projection, category=InterpretationCategory.SKIN_CARE,
            )
            result = await apply_personal_evidence(
                session,
                step7c,
                _context(
                    PersonalApplicabilityCategory.SKIN_CARE,
                    _fact("care_skin_usual_feel", "often_dry_or_tight"),
                ),
                category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert [claim.claim_key for claim in step7c.ingredients[0].claims] == ["reference.humectant"]
        assert result[0].claims == ()
        assert result[0].personal_applicability_status is PersonalApplicabilityStatus.NOT_ENOUGH_INFORMATION


class TestClaimGovernance:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"review_status": ReviewStatus.DRAFT.value},
            {"review_status": ReviewStatus.APPROVED.value},
            {"review_status": ReviewStatus.SUPERSEDED.value},
            {"review_status": ReviewStatus.RETIRED.value},
            {"claim_status": ClaimStatus.UNSUPPORTED.value},
            {"claim_status": ClaimStatus.CONFLICTING.value},
            {"ai_generated": True},
            {"subject_type": "ingredient"},
            {"subject_key": "not.glycerin"},
            {"domain": EvidenceDomain.HAIR_CARE.value},
            {"claim_type": ClaimType.SUBSTANCE_CATEGORY_INTERPRETATION.value},
            {"evidence_tier": EvidenceTier.REFERENCE_DATA.value},
            {"evidence_tier": EvidenceTier.CLASSICAL_TEXT.value},
            {"evidence_tier": EvidenceTier.TRADITIONAL_USE.value},
            {"evidence_tier": EvidenceTier.NOT_ENOUGH_INFORMATION.value},
            {"evidence_tier": EvidenceTier.AVOID.value},
            {"evidence_strength": EvidenceStrength.INSUFFICIENT.value},
            {"evidence_strength": EvidenceStrength.TRADITIONAL.value},
        ],
    )
    async def test_each_ineligible_claim_is_inert(self, db_clean, overrides):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, **overrides)
            await session.commit()
            result = await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims == ()

    @pytest.mark.parametrize(
        "mutator",
        [
            lambda value: value.pop("publication_verification"),
            lambda value: value["publication_verification"].update({"source_opened": False}),
            lambda value: value["publication_verification"].update({"unresolved_doubt": True}),
            lambda value: value["substance_personal_applicability"].update({"category": "hair_care"}),
            lambda value: value["substance_personal_applicability"].update({"schema_version": "2"}),
        ],
    )
    async def test_unpublished_malformed_and_doubtful_payloads_are_inert(self, db_clean, mutator):
        payload = _payload()
        mutator(payload)
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, structured_value=payload)
            await session.commit()
            result = await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims == ()

    @pytest.mark.parametrize("source_type", sorted(service.PERSONAL_APPLICABILITY_SOURCE_TYPES))
    async def test_every_allowed_source_type_is_eligible(self, db_clean, source_type):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session, source_type=source_type)
            await session.commit()
            result = await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims

    @pytest.mark.parametrize(
        "overrides",
        [
            {"source_type": SourceType.MANUFACTURER_CLAIM.value},
            {"source_type": SourceType.MANUFACTURER_LABEL.value},
            {"source_type": SourceType.MANUFACTURER_TECHNICAL_DOCUMENT.value},
            {"source_type": SourceType.INGREDIENT_REFERENCE_DATABASE.value},
            {"source_type": SourceType.TRADITIONAL_REFERENCE.value},
            {"source_type": SourceType.OTHER.value},
            {"source_status": SourceStatus.RETIRED.value},
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
            result = await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert result[0].claims == ()

    async def test_claim_projection_retains_exact_reviewed_text_fact_and_source_provenance(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            claim = await _add_claim(session, claim_key="personal.exact")
            await session.commit()
            result = await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        projected = result[0].claims[0]
        assert projected.claim_id == claim.id
        assert projected.claim_key == "personal.exact"
        assert projected.summary == claim.summary
        assert projected.scope == claim.scope
        assert projected.evidence_tier == EvidenceTier.CLINICALLY_STUDIED.value
        assert projected.matched_facts[0].profile_attribute_id
        assert projected.sources[0].canonical_url == "https://example.org/research/glycerin"


class TestStep8AOrchestration:
    async def test_confirmed_user_declared_fact_reaches_matcher_but_untrusted_does_not(self, db_clean):
        factory = get_sessionmaker()
        owners: list[uuid.UUID] = []
        async with factory() as session:
            for source, verification in (("user_declared", "confirmed"), ("photo_observed", "confirmed")):
                owner = uuid.uuid4()
                owners.append(owner)
                await identity_service.register_account(session, owner)
                profile = AppearanceProfile(account_id=owner)
                session.add(profile)
                await session.flush()
                session.add(ProfileAttribute(
                    profile_id=profile.id,
                    key="care_skin_sensitivity",
                    value="sometimes_reactive",
                    source=source,
                    confidence=1.0,
                    verification_state=verification,
                ))
            await session.commit()
            trusted = await build_personal_lens_context(
                session, account_id=owners[0], category=PersonalLensCategory.SKIN_CARE,
            )
            untrusted = await build_personal_lens_context(
                session, account_id=owners[1], category=PersonalLensCategory.SKIN_CARE,
            )
            await _add_claim(session)
            await session.commit()
            trusted_result = await apply_personal_evidence(
                session, _interpretation(), trusted, category=PersonalApplicabilityCategory.SKIN_CARE,
            )
            untrusted_result = await apply_personal_evidence(
                session, _interpretation(), untrusted, category=PersonalApplicabilityCategory.SKIN_CARE,
            )
        assert trusted_result[0].claims
        assert untrusted.body_facts == ()
        assert untrusted_result[0].claims == ()

    async def test_handoff_stops_before_step7c_and_step8b_and_does_not_echo_text(self, db_clean, monkeypatch):
        async def forbidden(*args, **kwargs):
            raise AssertionError("product or evidence work ran after handoff")

        monkeypatch.setattr(service, "interpret_label_snapshot", forbidden)
        private_text = "I take novaformin-private-step8b"
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        factory = get_sessionmaker()
        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await interpret_label_snapshot_for_account(
                    session,
                    object(),
                    account_id=uuid.uuid4(),
                    category=PersonalApplicabilityCategory.SKIN_CARE,
                    safety=PersonalLensSafetyInput(text=private_text),
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        assert result.context_status is PersonalLensStatus.HANDOFF_REQUIRED
        assert result.provenance is None
        assert result.ingredients == ()
        assert private_text not in repr(result)
        assert "novaformin" not in repr(result)
        assert statements == []

    async def test_no_body_context_and_packaged_food_skip_step7c(self, db_clean, monkeypatch):
        async def forbidden(*args, **kwargs):
            raise AssertionError("Step 7C ran without usable personal body context")

        monkeypatch.setattr(service, "interpret_label_snapshot", forbidden)
        owner = uuid.uuid4()
        factory = get_sessionmaker()
        async with factory() as session:
            await identity_service.register_account(session, owner)
            await session.commit()
            snapshot = SimpleNamespace(
                id=uuid.uuid4(),
                barcode="8900000000001",
                version_number=1,
                content_fingerprint="c" * 64,
                scan_event_id=uuid.uuid4(),
            )
            result = await interpret_label_snapshot_for_account(
                session,
                snapshot,
                account_id=owner,
                category=PersonalApplicabilityCategory.PACKAGED_FOOD,
            )
        assert result.context_status is PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
        assert result.profile_id is None
        assert result.provenance.label_snapshot_id == snapshot.id
        assert result.formula_status is None
        assert result.ingredients == ()

    async def test_top_level_calls_step8a_then_exact_step7c_then_matcher(self, monkeypatch):
        calls: list[tuple[str, object]] = []
        context = _context()
        interpretation = _interpretation()

        async def lens(session, *, account_id, category, safety):
            calls.append(("8a", category))
            return context

        async def step7c(session, snapshot, *, category):
            calls.append(("7c", snapshot))
            return interpretation

        async def matcher(session, supplied_interpretation, supplied_context, *, category):
            calls.append(("8b", category))
            assert supplied_interpretation is interpretation
            assert supplied_context is context
            return ()

        monkeypatch.setattr(service, "build_personal_lens_context", lens)
        monkeypatch.setattr(service, "interpret_label_snapshot", step7c)
        monkeypatch.setattr(service, "apply_personal_evidence", matcher)
        snapshot = object()
        result = await interpret_label_snapshot_for_account(
            object(),
            snapshot,
            account_id=uuid.uuid4(),
            category=PersonalApplicabilityCategory.SKIN_CARE,
        )
        assert [call[0] for call in calls] == ["8a", "7c", "8b"]
        assert calls[1] == ("7c", snapshot)
        assert result.provenance == interpretation.provenance
        assert result.profile_version == 7


class TestQueryBudgetAndPersistence:
    async def _queries(self, projection, *, with_claims: bool = False):
        factory = get_sessionmaker()
        if with_claims:
            async with factory() as session:
                for key in {row.substance_key for row in projection.ingredients if row.substance_key}:
                    await _add_claim(session, subject_key=key)
                await session.commit()
        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        engine = sql.get_engine().sync_engine
        async with factory() as session:
            event.listen(engine, "before_cursor_execute", record)
            try:
                await apply_personal_evidence(
                    session, projection, _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
                )
                assert not session.new and not session.dirty and not session.deleted
            finally:
                event.remove(engine, "before_cursor_execute", record)
        return statements

    async def test_zero_resolved_keys_uses_zero_queries(self, db_clean):
        statements = await self._queries(_interpretation(
            _ingredient(status=ProjectedIdentityStatus.UNRESOLVED, substance_key=None, candidates=()),
        ))
        assert statements == []

    async def test_resolved_keys_without_candidates_use_one_claim_query_and_no_source_query(self, db_clean):
        statements = await self._queries(_interpretation(*[
            _ingredient(f"Item {index}", position=index, substance_key=f"entity.{index}", candidates=(f"entity.{index}",))
            for index in range(1, 8)
        ]))
        assert len(statements) == 1
        assert "evidence_claims" in statements[0]
        assert "evidence_claim_sources" not in statements[0]

    async def test_distinct_substances_with_claims_use_one_claim_and_one_source_query(self, db_clean):
        projection = _interpretation(*[
            _ingredient(f"Item {index}", position=index, substance_key=f"entity.{index}", candidates=(f"entity.{index}",))
            for index in range(1, 5)
        ])
        statements = await self._queries(projection, with_claims=True)
        assert len(statements) == 2
        assert sum("evidence_claims" in row and "evidence_claim_sources" not in row for row in statements) == 1
        assert sum("evidence_claim_sources" in row for row in statements) == 1

    async def test_duplicate_positions_do_not_add_queries(self, db_clean):
        projection = _interpretation(*[_ingredient(position=index) for index in range(1, 30)])
        statements = await self._queries(projection, with_claims=True)
        assert len(statements) == 2

    async def test_runtime_does_not_mutate_evidence_rows(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _add_claim(session)
            await session.commit()
            before = (
                await session.scalar(select(func.count(EvidenceClaim.id))),
                await session.scalar(select(func.count(EvidenceClaimSource.id))),
                await session.scalar(select(func.count(EvidenceSource.id))),
            )
            await apply_personal_evidence(
                session, _interpretation(), _context(), category=PersonalApplicabilityCategory.SKIN_CARE,
            )
            after = (
                await session.scalar(select(func.count(EvidenceClaim.id))),
                await session.scalar(select(func.count(EvidenceClaimSource.id))),
                await session.scalar(select(func.count(EvidenceSource.id))),
            )
        assert before == after


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
            "__init__.py", "authoring.py", "enums.py", "schema.py", "service.py",
        }

    @pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_production_imports_only_reviewed_authorities(self, path):
        allowed = (
            "app.domains.personal_applicability",
            "app.domains.personal_lens",
            "app.domains.substance_interpretation",
            "app.domains.evidence",
            "app.domains.product.formula_projection",
            "app.domains.product.models",
            "app.domains.profile.registry",
            "sqlalchemy",
            "collections",
            "dataclasses",
            "datetime",
            "enum",
            "typing",
            "uuid",
            "__future__",
        )
        for module in _imported_modules(path):
            assert module.startswith(allowed), f"{path.name} imports {module}"

    @pytest.mark.parametrize("path", sorted(DOMAIN_DIR.glob("*.py")), ids=lambda path: path.name)
    def test_domain_never_imports_forbidden_authorities(self, path):
        imported = _imported_modules(path)
        forbidden = (
            "app.domains.substances", "app.domains.formulas", "app.domains.off",
            "app.domains.ai_gateway", "app.domains.alternatives", "app.domains.purchase",
            "app.domains.recommendation", "app.domains.profile.service",
            "app.domains.profile.models", "app.domains.routines", "httpx", "requests",
            "aiohttp", "urllib", "socket", "google.genai",
        )
        for prefix in forbidden:
            assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported)

    def test_runtime_has_no_write_or_latest_snapshot_api(self):
        body = inspect.getsource(service)
        for forbidden in (
            "session.add", "session.flush", "session.commit", "session.delete",
            "latest_label_snapshot", "latest_label_snapshots",
        ):
            assert forbidden not in body

    def test_output_contract_is_immutable_and_has_no_decision_fields(self):
        output_types = (
            service.PersonalApplicabilitySource,
            service.MatchedPersonalFact,
            service.ApplicableSubstancePersonalClaim,
            service.IngredientPersonalApplicability,
            service.LabelSnapshotPersonalApplicability,
        )
        for output_type in output_types:
            assert output_type.__dataclass_params__.frozen is True
        fields = set(service.IngredientPersonalApplicability.__dataclass_fields__)
        assert fields.isdisjoint({
            "score", "grade", "verdict", "risk", "benefit", "safety",
            "concentration", "dose", "recommendation", "confidence", "rank",
        })

    def test_no_production_personal_applicability_seed_exists(self):
        production = BACKEND_ROOT / "app"
        hits = []
        for path in production.rglob("*.py"):
            if (
                DOMAIN_DIR in path.parents
                or "knowledge_packs" in path.parts
                or path.name == "enums.py" and "evidence" in path.parts
            ):
                continue
            if "substance_personal_applicability" in path.read_text(encoding="utf-8"):
                hits.append(path)
        assert hits == []


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


async def test_populated_migration_downgrade_preserves_old_claims_and_reupgrade_accepts_new_type(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        step8b = await _add_claim(session, claim_key="step8b.remove")
        old = await _add_claim(
            session,
            claim_key="step7c.keep",
            claim_type=ClaimType.SUBSTANCE_CATEGORY_INTERPRETATION.value,
            evidence_tier=EvidenceTier.REFERENCE_DATA.value,
            source_type=SourceType.INGREDIENT_REFERENCE_DATABASE.value,
        )
        legacy = EvidenceClaim(
            claim_key="legacy.keep",
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
            supersedes_claim_id=step8b.id,
        )
        session.add(legacy)
        await session.commit()
        old_id = old.id

    await sql.dispose_engine()
    upgraded = False
    try:
        await _alembic("downgrade", BASE_REVISION)
        async with sql.get_engine().connect() as connection:
            remaining = (await connection.execute(text(
                "SELECT claim_key, supersedes_claim_id FROM evidence_claims ORDER BY claim_key"
            ))).all()
            assert remaining == [("legacy.keep", None), ("step7c.keep", None)]
            assert await connection.scalar(text(
                "SELECT count(*) FROM evidence_claim_sources WHERE claim_id = :claim_id"
            ), {"claim_id": old_id}) == 1
            claim_check = await connection.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_evidence_claims_claim_type'"
            ))
            assert "substance_category_interpretation" in claim_check
            assert "substance_personal_applicability" not in claim_check
        await sql.dispose_engine()
        await _alembic("upgrade", "head")
        upgraded = True
        async with sql.get_engine().connect() as connection:
            claim_check = await connection.scalar(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_evidence_claims_claim_type'"
            ))
            assert "substance_personal_applicability" in claim_check
            with pytest.raises(IntegrityError):
                await connection.execute(text(
                    "INSERT INTO evidence_claims "
                    "(id, claim_key, claim_version, domain, subject_type, subject_key, claim_type, "
                    "summary, scope, review_status, regulatory_context, ai_generated, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'bad.type', 1, 'skin_care', 'substance', 'x', "
                    "'arbitrary_claim_type', 'x', 'x', 'draft', 'unknown', false, now(), now())"
                ))
    finally:
        if not upgraded:
            await sql.dispose_engine()
            await _alembic("upgrade", "head")
        await sql.dispose_engine()
