"""Step 8G governed authoring of the exact evidence Step 8B consumes."""

from __future__ import annotations

import ast
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.api.v2.personal_applicability_admin import ExistingSourceBody, NewSourceBody
from app.domains.evidence import authoring as evidence_authoring
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
from app.domains.evidence.service import claim_is_public_knowledge_path, source_path_is_public_knowledge
from app.domains.personal_applicability import authoring
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.personal_applicability.schema import parse_personal_applicability_payload
from app.domains.personal_applicability.service import (
    PERSONAL_APPLICABILITY_SOURCE_TYPES,
    apply_personal_evidence,
)
from app.domains.personal_decision_explanation.rules import PERSONAL_DECISION_EXPLANATION_RULES
from app.domains.personal_decision_policy.rules import PERSONAL_DECISION_POLICY_RULES
from app.domains.personal_decision_semantics.rules import PERSONAL_DECISION_SEMANTIC_RULES
from app.domains.personal_lens.enums import PersonalLensCategory, PersonalLensStatus
from app.domains.personal_lens.service import PersonalLensContext, PersonalLensFact
from app.domains.product.formula_projection import FormulaProjectionProvenance
from app.domains.substance_interpretation.enums import (
    InterpretationCategory,
    InterpretationStatus,
    ProjectedIdentityStatus,
)
from app.domains.substance_interpretation.service import (
    FormulaIngredientInterpretation,
    LabelSnapshotFormulaInterpretation,
)
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConflictError, ValidationFailedError
from pydantic import ValidationError
from sqlalchemy import select

from tests.conftest import auth

ADMIN = "/api/v2/admin/personal-applicability"
GENERIC = "/api/v2/admin/knowledge"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
AUTHORING_PATH = BACKEND_ROOT / "app" / "domains" / "personal_applicability" / "authoring.py"


def _condition(
    fact_key: str = "care_skin_sensitivity",
    values: tuple[str, ...] = ("sometimes_reactive",),
) -> authoring.AuthoringConditionInput:
    return authoring.AuthoringConditionInput(fact_key=fact_key, values=values)


def _new_source(**overrides) -> authoring.NewSourceInput:
    values = {
        "source_type": SourceType.PEER_REVIEWED_RESEARCH.value,
        "title": "Synthetic reviewed research",
        "publisher": "Synthetic Journal",
        "canonical_url": "https://example.invalid/step8g/source-a",
        "license_or_use_note": "Synthetic source used only for automated verification.",
        "locator": "section synthetic-a",
        "publication_date": date(2025, 1, 2),
        "version_or_revision": "v1",
        "jurisdiction": "synthetic",
    }
    values.update(overrides)
    return authoring.NewSourceInput(**values)


def _entry(**overrides) -> authoring.PersonalApplicabilityDraftInput:
    values = {
        "category": PersonalApplicabilityCategory.SKIN_CARE,
        "substance_key": "substance.synthetic.a",
        "summary": "The synthetic source reports an exact scoped relationship.",
        "scope": "Synthetic controlled body facts and one synthetic substance only.",
        "evidence_strength": EvidenceStrength.MODERATE.value,
        "strength_rationale": "The synthetic source directly supports the synthetic scope.",
        "conditions": (_condition(),),
        "sources": (_new_source(),),
    }
    values.update(overrides)
    return authoring.PersonalApplicabilityDraftInput(**values)


def _body(**overrides) -> dict:
    body = {
        "category": "skin_care",
        "substance_key": "substance.synthetic.api",
        "summary": "A synthetic admin-authored claim.",
        "scope": "Synthetic API coverage only.",
        "evidence_strength": "moderate",
        "strength_rationale": "The synthetic source directly supports this synthetic scope.",
        "conditions": [{"fact_key": "care_skin_sensitivity", "values": ["sometimes_reactive"]}],
        "sources": [{
            "mode": "new",
            "source_type": "peer_reviewed_research",
            "title": "Synthetic API source",
            "publisher": "Synthetic API publisher",
            "canonical_url": "https://example.invalid/step8g/api-source",
            "license_or_use_note": "Synthetic test use only.",
            "locator": "section api",
        }],
    }
    body.update(overrides)
    return body


def _verification(**overrides) -> evidence_authoring.VerificationInput:
    values = {
        "source_opened": True,
        "founder_verified_fact": True,
        "claude_review_completed": True,
        "codex_review_completed": True,
        "independent_reviews_agree": True,
        "adversarial_review_passed": True,
        "unresolved_doubt": False,
    }
    values.update(overrides)
    return evidence_authoring.VerificationInput(**values)


async def _create(session, entry=None) -> dict:
    return await authoring.create_personal_applicability_draft(
        session,
        entry or _entry(),
        author="admin.synthetic",
    )


async def _approve(session, entry_id: str) -> dict:
    return await authoring.approve_personal_applicability_entry(
        session,
        uuid.UUID(entry_id),
        reviewer="reviewer.synthetic",
    )


async def _verify(session, entry_id: str, verification=None) -> dict:
    return await authoring.record_personal_applicability_publication_verification(
        session,
        uuid.UUID(entry_id),
        verification=verification or _verification(),
        actor="verifier.synthetic",
    )


async def _publish(session, entry_id: str) -> dict:
    return await authoring.publish_personal_applicability_entry(
        session,
        uuid.UUID(entry_id),
        publisher="publisher.synthetic",
    )


async def _existing_source(session, **overrides) -> EvidenceSource:
    values = {
        "source_key": f"source.synthetic.{uuid.uuid4().hex}",
        "source_series_key": f"series.synthetic.{uuid.uuid4().hex}",
        "source_type": SourceType.OFFICIAL_GUIDELINE.value,
        "title": "Synthetic existing guideline",
        "publisher": "Synthetic authority",
        "canonical_url": f"https://example.invalid/step8g/{uuid.uuid4().hex}",
        "accessed_at": utcnow(),
        "status": SourceStatus.ACTIVE.value,
        "license_or_use_note": "Synthetic review use only.",
    }
    values.update(overrides)
    source = EvidenceSource(**values)
    session.add(source)
    await session.flush()
    return source


def _step8b_inputs() -> tuple[LabelSnapshotFormulaInterpretation, PersonalLensContext]:
    context = PersonalLensContext(
        category=PersonalLensCategory.SKIN_CARE,
        status=PersonalLensStatus.PARTIAL_CONTEXT,
        profile_id=uuid.uuid4(),
        profile_version=1,
        body_facts=(PersonalLensFact(
            key="care_skin_sensitivity",
            value="sometimes_reactive",
            source="user_declared",
            verification_state="confirmed",
            profile_attribute_id=uuid.uuid4(),
            explicit_unknown=False,
            last_reviewed_at=None,
        ),),
        preference_facts=(),
        missing_information=(),
        handoff=None,
    )
    interpretation = LabelSnapshotFormulaInterpretation(
        provenance=FormulaProjectionProvenance(
            label_snapshot_id=uuid.uuid4(),
            barcode="synthetic-step8g",
            version_number=1,
            content_fingerprint="a" * 64,
            scan_event_id=uuid.uuid4(),
        ),
        category=InterpretationCategory.SKIN_CARE,
        formula_status="parsed",
        ingredients=(FormulaIngredientInterpretation(
            position=1,
            raw_name="Synthetic A",
            normalized_name="synthetic a",
            identity_status=ProjectedIdentityStatus.RESOLVED,
            substance_key="substance.synthetic.a",
            entity_kind="defined_substance",
            candidate_substance_keys=("substance.synthetic.a",),
            interpretation_status=InterpretationStatus.NOT_ENOUGH_INFORMATION,
            claims=(),
        ),),
    )
    return interpretation, context


async def _step8b_claims(session):
    interpretation, context = _step8b_inputs()
    result = await apply_personal_evidence(
        session,
        interpretation,
        context,
        category=PersonalApplicabilityCategory.SKIN_CARE,
    )
    return result[0].claims


def _replacement_entry(
    source_key: str,
    *,
    locator: str | None = "section synthetic-a",
    summary: str = "A replacement synthetic version.",
) -> authoring.PersonalApplicabilityDraftInput:
    return _entry(
        summary=summary,
        sources=(authoring.ExistingSourceInput(source_key=source_key, locator=locator),),
    )


@pytest.mark.parametrize(
    ("category", "expected_domain", "fact_key", "value", "operator"),
    [
        (PersonalApplicabilityCategory.SKIN_CARE, "skin_care", "care_skin_sensitivity", "sometimes_reactive", "equals_any"),
        (PersonalApplicabilityCategory.HAIR_CARE, "hair_care", "care_hair_processing", "coloured", "contains_any"),
        (PersonalApplicabilityCategory.COSMETICS, "cosmetics", "care_skin_usual_feel", "comfortable", "equals_any"),
    ],
)
async def test_creation_derives_exact_governed_shape(db_clean, category, expected_domain, fact_key, value, operator):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session, _entry(
            category=category,
            conditions=(_condition(fact_key, (value,)),),
        ))
        claim = await session.get(EvidenceClaim, uuid.UUID(created["id"]))
    assert created["category"] == category.value
    assert created["domain"] == expected_domain
    assert created["claim_type"] == ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value
    assert created["subject_type"] == "substance"
    assert created["evidence_tier"] == EvidenceTier.CLINICALLY_STUDIED.value
    assert created["ai_generated"] is False
    assert created["review_status"] == ReviewStatus.DRAFT.value
    assert created["claim_version"] == 1
    assert created["conditions"][0]["operator"] == operator
    assert parse_personal_applicability_payload(claim.structured_value) is not None


async def test_packaged_food_fails_closed_and_vocabulary_exposes_no_body_facts():
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError, match="CATEGORY_HAS_NO_PERSONAL_BODY_FACTS"):
            await _create(session, _entry(category=PersonalApplicabilityCategory.PACKAGED_FOOD))
    packaged = next(
        row for row in authoring.personal_applicability_vocabulary()["categories"]
        if row["category"] == "packaged_food"
    )
    assert packaged == {
        "category": "packaged_food",
        "supported_for_personal_applicability": False,
        "facts": [],
    }


@pytest.mark.parametrize(
    "conditions",
    [
        (_condition("unknown.fact"),),
        (_condition("care_hair_pattern", ("curly",)),),
        (_condition(values=("not_sure",)),),
        (_condition(values=("invented",)),),
        (_condition(values=("sometimes_reactive", "sometimes_reactive")),),
        (_condition(values=()),),
        (_condition(), _condition()),
    ],
)
async def test_invalid_condition_shapes_are_rejected(db_clean, conditions):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await _create(session, _entry(conditions=conditions))


async def test_more_than_four_conditions_is_rejected(db_clean):
    conditions = tuple(
        _condition(key, (value,))
        for key, value in (
            ("care_hair_pattern", "curly"),
            ("care_hair_strand_characteristic", "fine"),
            ("care_hair_density", "high"),
            ("care_hair_wash_frequency", "weekly"),
            ("care_heat_styling_frequency", "never"),
        )
    )
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError, match="1 to 4"):
            await _create(session, _entry(category=PersonalApplicabilityCategory.HAIR_CARE, conditions=conditions))


@pytest.mark.parametrize("strength", ["strong", "moderate", "limited"])
async def test_only_step8b_strengths_are_accepted(db_clean, strength):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session, _entry(evidence_strength=strength))
    assert created["evidence_strength"] == strength


@pytest.mark.parametrize("strength", ["traditional_uncertain", "insufficient", "invented"])
async def test_ineligible_strengths_are_rejected(db_clean, strength):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await _create(session, _entry(evidence_strength=strength))


async def test_blank_strength_rationale_is_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await _create(session, _entry(strength_rationale="  "))


@pytest.mark.parametrize("source_type", sorted(PERSONAL_APPLICABILITY_SOURCE_TYPES))
async def test_every_step8b_source_type_is_authorable(db_clean, source_type):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session, _entry(sources=(_new_source(source_type=source_type),)))
    assert created["sources"][0]["source_type"] == source_type


async def test_eligible_existing_source_and_locator_are_preserved(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = await _existing_source(session)
        created = await _create(session, _entry(sources=(authoring.ExistingSourceInput(
            source_key=source.source_key,
            locator="table synthetic-7",
        ),)))
    assert created["sources"][0]["source_key"] == source.source_key
    assert created["sources"][0]["locator"] == "table synthetic-7"


@pytest.mark.parametrize("source_mode", ["existing", "new"])
@pytest.mark.parametrize("locator", [None, "  synthetic section 3  "])
async def test_null_or_padded_nonblank_locator_is_accepted_without_normalization(
    db_clean, source_mode, locator,
):
    factory = get_sessionmaker()
    async with factory() as session:
        if source_mode == "existing":
            source = await _existing_source(session)
            source_input = authoring.ExistingSourceInput(source.source_key, locator)
        else:
            source_input = _new_source(locator=locator)
        created = await _create(session, _entry(sources=(source_input,)))
    assert created["sources"][0]["locator"] == locator


@pytest.mark.parametrize("source_mode", ["existing", "new"])
@pytest.mark.parametrize("locator", ["", "   "])
async def test_blank_locator_is_rejected_by_the_service(db_clean, source_mode, locator):
    factory = get_sessionmaker()
    async with factory() as session:
        if source_mode == "existing":
            source = await _existing_source(session)
            source_input = authoring.ExistingSourceInput(source.source_key, locator)
        else:
            source_input = _new_source(locator=locator)
        with pytest.raises(ValidationFailedError) as exc_info:
            await _create(session, _entry(sources=(source_input,)))
    assert exc_info.value.extra == {"field": "locator"}


@pytest.mark.parametrize("model", [ExistingSourceBody, NewSourceBody])
@pytest.mark.parametrize("locator", ["", "   "])
def test_blank_locator_is_rejected_by_the_api_schema(model, locator):
    values = (
        {"mode": "existing", "source_key": "source.synthetic"}
        if model is ExistingSourceBody
        else {
            "mode": "new",
            "source_type": SourceType.PEER_REVIEWED_RESEARCH.value,
            "title": "Synthetic source",
            "publisher": "Synthetic publisher",
            "canonical_url": "https://example.invalid/step8g/schema-locator",
            "license_or_use_note": "Synthetic test use only.",
        }
    )
    with pytest.raises(ValidationError):
        model(**values, locator=locator)


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_type": SourceType.MANUFACTURER_CLAIM.value},
        {"title": " "},
        {"publisher": " "},
        {"canonical_url": "not-openable"},
        {"license_or_use_note": " "},
    ],
)
async def test_invalid_new_source_metadata_is_rejected(db_clean, overrides):
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError):
            await _create(session, _entry(sources=(_new_source(**overrides),)))


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_type": SourceType.MANUFACTURER_CLAIM.value},
        {"status": SourceStatus.RETIRED.value},
        {"title": " "},
        {"publisher": " "},
        {"canonical_url": None},
        {"license_or_use_note": None},
    ],
)
async def test_invalid_existing_source_metadata_is_rejected(db_clean, overrides):
    factory = get_sessionmaker()
    async with factory() as session:
        source = await _existing_source(session, **overrides)
        with pytest.raises(ValidationFailedError):
            await _create(session, _entry(sources=(authoring.ExistingSourceInput(source.source_key),)))


async def test_duplicate_canonical_url_conflict_names_existing_source(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = await _existing_source(session, canonical_url="https://example.invalid/step8g/duplicate")
        with pytest.raises(ConflictError, match=source.source_key):
            await _create(session, _entry(sources=(_new_source(
                canonical_url="https://example.invalid/step8g/duplicate",
            ),)))


@pytest.mark.parametrize("count", [0, 6])
async def test_source_count_bounds_are_enforced(db_clean, count):
    sources = tuple(
        _new_source(canonical_url=f"https://example.invalid/step8g/bound-{index}")
        for index in range(count)
    )
    factory = get_sessionmaker()
    async with factory() as session:
        with pytest.raises(ValidationFailedError, match="1 to 5"):
            await _create(session, _entry(sources=sources))


async def test_duplicate_source_path_is_rejected(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        source = await _existing_source(session)
        duplicate = authoring.ExistingSourceInput(source.source_key, "same locator")
        with pytest.raises(ValidationFailedError, match="Duplicate source path"):
            await _create(session, _entry(sources=(duplicate, duplicate)))


async def test_approval_reviews_every_supporting_link_and_sets_supported(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        first = await _existing_source(session)
        second = await _existing_source(session)
        created = await _create(session, _entry(sources=(
            authoring.ExistingSourceInput(first.source_key, "first"),
            authoring.ExistingSourceInput(second.source_key, "second"),
        )))
        approved = await _approve(session, created["id"])
    assert approved["review_status"] == ReviewStatus.APPROVED.value
    assert approved["claim_status"] == ClaimStatus.SUPPORTED.value
    assert all(source["reviewed_by"] and source["reviewed_at"] for source in approved["sources"])


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("claim_type", ClaimType.USAGE_CONTEXT.value),
        ("domain", EvidenceDomain.HAIR_CARE.value),
        ("subject_type", "ingredient"),
        ("subject_key", " "),
        ("evidence_tier", EvidenceTier.REFERENCE_DATA.value),
        ("ai_generated", True),
        ("structured_value", {"substance_personal_applicability": {"schema_version": "2"}}),
    ],
)
async def test_approval_revalidates_corrupted_claim_shape(db_clean, attribute, value):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        claim = await session.get(EvidenceClaim, uuid.UUID(created["id"]))
        setattr(claim, attribute, value)
        with pytest.raises(ValidationFailedError):
            await _approve(session, created["id"])


@pytest.mark.parametrize("locator", ["", "   "])
async def test_approval_revalidates_corrupted_persisted_locator(db_clean, locator):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        claim = await session.get(EvidenceClaim, uuid.UUID(created["id"]))
        link = (await session.execute(
            select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id)
        )).scalar_one()
        link.locator = locator
        with pytest.raises(ValidationFailedError) as exc_info:
            await _approve(session, created["id"])

    assert exc_info.value.extra == {"field": "locator"}


async def test_publication_requires_complete_explicit_verification(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        await _approve(session, created["id"])
        with pytest.raises(ValidationFailedError, match="verification"):
            await _publish(session, created["id"])
        await _verify(session, created["id"], _verification(source_opened=False))
        with pytest.raises(ValidationFailedError, match="verification"):
            await _publish(session, created["id"])
        await _verify(session, created["id"], _verification(unresolved_doubt=True))
        with pytest.raises(ValidationFailedError, match="verification"):
            await _publish(session, created["id"])


async def test_successful_publication_is_exact_step8b_public_knowledge(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        await _approve(session, created["id"])
        await _verify(session, created["id"])
        published = await _publish(session, created["id"])
        claim = await session.get(EvidenceClaim, uuid.UUID(created["id"]))
        link, source = (await session.execute(
            select(EvidenceClaimSource, EvidenceSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceClaimSource.claim_id == claim.id)
        )).one()
    assert published["review_status"] == ReviewStatus.PUBLISHED.value
    assert published["published_by"] and published["published_at"]
    assert claim_is_public_knowledge_path(claim) is True
    assert source_path_is_public_knowledge(
        link,
        source,
        allowed_source_types=PERSONAL_APPLICABILITY_SOURCE_TYPES,
    ) is True


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("status", SourceStatus.RETIRED.value),
        ("canonical_url", None),
        ("license_or_use_note", None),
        ("source_type", SourceType.MANUFACTURER_CLAIM.value),
    ],
)
async def test_source_corruption_after_approval_blocks_publication(db_clean, attribute, value):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        await _approve(session, created["id"])
        await _verify(session, created["id"])
        source = (await session.execute(
            select(EvidenceSource)
            .join(EvidenceClaimSource, EvidenceClaimSource.source_id == EvidenceSource.id)
            .where(EvidenceClaimSource.claim_id == uuid.UUID(created["id"]))
        )).scalar_one()
        setattr(source, attribute, value)
        with pytest.raises(ValidationFailedError):
            await _publish(session, created["id"])


@pytest.mark.parametrize("locator", ["", "   "])
async def test_publication_revalidates_corrupted_persisted_locator(db_clean, locator):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        await _approve(session, created["id"])
        await _verify(session, created["id"])
        claim = await session.get(EvidenceClaim, uuid.UUID(created["id"]))
        link = (await session.execute(
            select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == claim.id)
        )).scalar_one()
        link.locator = locator
        with pytest.raises(ValidationFailedError) as exc_info:
            await _publish(session, created["id"])

    assert exc_info.value.extra == {"field": "locator"}
    assert claim.review_status == ReviewStatus.APPROVED.value


async def test_non_supporting_link_corruption_blocks_publication(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        await _approve(session, created["id"])
        await _verify(session, created["id"])
        link = (await session.execute(
            select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id == uuid.UUID(created["id"]))
        )).scalar_one()
        link.relationship = ClaimSourceRelationship.QUALIFIES.value
        with pytest.raises(ValidationFailedError):
            await _publish(session, created["id"])


async def test_draft_and_rejected_edits_stay_version_one_and_clear_lifecycle(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        source_key = created["sources"][0]["source_key"]
        edit_input = _entry(
            summary="Edited synthetic draft.",
            sources=(authoring.ExistingSourceInput(source_key, "edited locator"),),
        )
        edited = await authoring.edit_personal_applicability_entry(
            session, uuid.UUID(created["id"]), edit_input, author="admin.synthetic",
        )
        rejected = await authoring.reject_personal_applicability_entry(
            session, uuid.UUID(created["id"]), reviewer="reviewer.synthetic", reason="Synthetic rejection.",
        )
        reedited = await authoring.edit_personal_applicability_entry(
            session, uuid.UUID(rejected["id"]), edit_input, author="admin.synthetic",
        )
    assert edited["claim_version"] == reedited["claim_version"] == 1
    assert reedited["review_status"] == ReviewStatus.DRAFT.value
    assert reedited["rejection_reason"] is None
    assert reedited["verification"] is None
    assert reedited["sources"][0]["reviewed_at"] is None


@pytest.mark.parametrize("publish_first", [False, True])
async def test_reviewed_edit_creates_clean_version_two_without_overwriting_old(db_clean, publish_first):
    factory = get_sessionmaker()
    async with factory() as session:
        created = await _create(session)
        old_id = uuid.UUID(created["id"])
        old_summary = created["summary"]
        source_key = created["sources"][0]["source_key"]
        await _approve(session, created["id"])
        if publish_first:
            await _verify(session, created["id"])
            await _publish(session, created["id"])
        replacement = await authoring.edit_personal_applicability_entry(
            session,
            old_id,
            _entry(
                summary="A new synthetic version.",
                sources=(authoring.ExistingSourceInput(source_key, "replacement locator"),),
            ),
            author="admin.synthetic",
        )
        old = await session.get(EvidenceClaim, old_id)
    assert replacement["claim_version"] == 2
    assert replacement["claim_key"] == created["claim_key"]
    assert replacement["supersedes_claim_id"] == created["id"]
    assert replacement["review_status"] == ReviewStatus.DRAFT.value
    assert replacement["claim_status"] is None
    assert replacement["verification"] is None
    assert replacement["reviewed_by"] is None and replacement["published_by"] is None
    assert replacement["sources"][0]["reviewed_at"] is None
    assert old.summary == old_summary
    assert old.review_status == (
        ReviewStatus.PUBLISHED.value if publish_first else ReviewStatus.SUPERSEDED.value
    )


async def test_published_replacement_handoff_preserves_runtime_until_atomic_publish(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        v1 = await _create(session)
        await _approve(session, v1["id"])
        await _verify(session, v1["id"])
        await _publish(session, v1["id"])
        await session.commit()

        before_edit = await _step8b_claims(session)
        v2 = await authoring.edit_personal_applicability_entry(
            session,
            uuid.UUID(v1["id"]),
            _replacement_entry(v1["sources"][0]["source_key"]),
            author="admin.synthetic",
        )
        await session.commit()
        during_draft = await _step8b_claims(session)
        persisted_v1 = await session.get(EvidenceClaim, uuid.UUID(v1["id"]))
        persisted_v2 = await session.get(EvidenceClaim, uuid.UUID(v2["id"]))

        assert [(claim.claim_id, claim.claim_version) for claim in before_edit] == [
            (uuid.UUID(v1["id"]), 1),
        ]
        assert persisted_v1.review_status == ReviewStatus.PUBLISHED.value
        assert persisted_v2.review_status == ReviewStatus.DRAFT.value
        assert [(claim.claim_id, claim.claim_version) for claim in during_draft] == [
            (uuid.UUID(v1["id"]), 1),
        ]

        await _approve(session, v2["id"])
        await _verify(session, v2["id"])
        await _publish(session, v2["id"])
        await session.commit()
        after_publish = await _step8b_claims(session)
        await session.refresh(persisted_v1)
        await session.refresh(persisted_v2)

    assert persisted_v1.review_status == ReviewStatus.SUPERSEDED.value
    assert persisted_v2.review_status == ReviewStatus.PUBLISHED.value
    assert [(claim.claim_id, claim.claim_version) for claim in after_publish] == [
        (uuid.UUID(v2["id"]), 2),
    ]


async def test_failed_replacement_publication_rolls_back_and_preserves_live_v1(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        v1 = await _create(session)
        await _approve(session, v1["id"])
        await _verify(session, v1["id"])
        await _publish(session, v1["id"])
        await session.commit()

        v2 = await authoring.edit_personal_applicability_entry(
            session,
            uuid.UUID(v1["id"]),
            _entry(
                summary="A replacement with its own synthetic source.",
                sources=(_new_source(
                    canonical_url="https://example.invalid/step8g/replacement-source",
                    locator="replacement locator",
                ),),
            ),
            author="admin.synthetic",
        )
        await _approve(session, v2["id"])
        await _verify(session, v2["id"])
        await session.commit()

        v2_source = (await session.execute(
            select(EvidenceSource)
            .join(EvidenceClaimSource, EvidenceClaimSource.source_id == EvidenceSource.id)
            .where(EvidenceClaimSource.claim_id == uuid.UUID(v2["id"]))
        )).scalar_one()
        v2_source.status = SourceStatus.RETIRED.value
        with pytest.raises(ValidationFailedError):
            await _publish(session, v2["id"])
        await session.rollback()

        persisted_v1 = await session.get(EvidenceClaim, uuid.UUID(v1["id"]))
        persisted_v2 = await session.get(EvidenceClaim, uuid.UUID(v2["id"]))
        projected = await _step8b_claims(session)

    assert persisted_v1.review_status == ReviewStatus.PUBLISHED.value
    assert persisted_v2.review_status == ReviewStatus.APPROVED.value
    assert [(claim.claim_id, claim.claim_version) for claim in projected] == [
        (uuid.UUID(v1["id"]), 1),
    ]


async def test_corrupt_replacement_locator_rolls_back_and_preserves_live_v1(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        v1 = await _create(session)
        await _approve(session, v1["id"])
        await _verify(session, v1["id"])
        await _publish(session, v1["id"])
        await session.commit()

        v2 = await authoring.edit_personal_applicability_entry(
            session,
            uuid.UUID(v1["id"]),
            _replacement_entry(v1["sources"][0]["source_key"]),
            author="admin.synthetic",
        )
        await _approve(session, v2["id"])
        await _verify(session, v2["id"])
        await session.commit()

        v2_link = (await session.execute(
            select(EvidenceClaimSource).where(
                EvidenceClaimSource.claim_id == uuid.UUID(v2["id"]),
            )
        )).scalar_one()
        v2_link.locator = ""
        with pytest.raises(ValidationFailedError) as exc_info:
            await _publish(session, v2["id"])
        await session.rollback()

        persisted_v1 = await session.get(EvidenceClaim, uuid.UUID(v1["id"]))
        persisted_v2 = await session.get(EvidenceClaim, uuid.UUID(v2["id"]))
        projected = await _step8b_claims(session)

    assert exc_info.value.extra == {"field": "locator"}
    assert persisted_v1.review_status == ReviewStatus.PUBLISHED.value
    assert persisted_v2.review_status == ReviewStatus.APPROVED.value
    assert [(claim.claim_id, claim.claim_version) for claim in projected] == [
        (uuid.UUID(v1["id"]), 1),
    ]


async def test_multiple_other_published_versions_fail_closed_without_selecting_one(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        v1 = await _create(session)
        await _approve(session, v1["id"])
        await _verify(session, v1["id"])
        await _publish(session, v1["id"])
        await session.commit()

        v2 = await authoring.edit_personal_applicability_entry(
            session,
            uuid.UUID(v1["id"]),
            _replacement_entry(v1["sources"][0]["source_key"], summary="Synthetic v2."),
            author="admin.synthetic",
        )
        await _approve(session, v2["id"])
        await _verify(session, v2["id"])
        corrupted_v2 = await session.get(EvidenceClaim, uuid.UUID(v2["id"]))
        corrupted_v2.review_status = ReviewStatus.PUBLISHED.value
        corrupted_v2.published_by = "corruptor.synthetic"
        corrupted_v2.published_at = utcnow()
        await session.commit()

        v3 = await authoring.edit_personal_applicability_entry(
            session,
            uuid.UUID(v2["id"]),
            _replacement_entry(v1["sources"][0]["source_key"], summary="Synthetic v3."),
            author="admin.synthetic",
        )
        await _approve(session, v3["id"])
        await _verify(session, v3["id"])
        await session.commit()

        with pytest.raises(ConflictError, match="MULTIPLE_ACTIVE_PUBLISHED_VERSIONS"):
            await _publish(session, v3["id"])
        await session.rollback()
        statuses = {
            claim.claim_version: claim.review_status
            for claim in (await session.execute(
                select(EvidenceClaim).where(EvidenceClaim.claim_key == v1["claim_key"])
            )).scalars().all()
        }

    assert statuses == {
        1: ReviewStatus.PUBLISHED.value,
        2: ReviewStatus.PUBLISHED.value,
        3: ReviewStatus.APPROVED.value,
    }


@pytest.fixture
async def admin_token(registered_supabase_user):
    token, _ = await registered_supabase_user(admin=True)
    return token


async def test_routes_are_registered_and_enforce_canonical_admin_authority(
    db_clean, app_client, registered_supabase_user, admin_token,
):
    unauthenticated = await app_client.get(ADMIN + "/vocabulary")
    assert unauthenticated.status_code in {401, 403}
    non_admin_token, _ = await registered_supabase_user()
    forbidden = await app_client.get(ADMIN + "/vocabulary", headers=auth(non_admin_token))
    assert forbidden.status_code == 403
    allowed = await app_client.get(ADMIN + "/vocabulary", headers=auth(admin_token))
    assert allowed.status_code == 200
    created = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=_body())
    assert created.status_code == 201, created.text


async def test_api_never_accepts_governed_fields_or_condition_operator(db_clean, app_client, admin_token):
    for key, value in (
        ("domain", "hair_care"),
        ("claim_type", "usage_context"),
        ("review_status", "published"),
        ("ai_generated", True),
    ):
        response = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=_body(**{key: value}))
        assert response.status_code == 422
    with_operator = _body(conditions=[{
        "fact_key": "care_skin_sensitivity",
        "values": ["sometimes_reactive"],
        "operator": "contains_any",
    }])
    response = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=with_operator)
    assert response.status_code == 422


async def test_generic_reads_work_but_generic_mutations_cannot_bypass_specialized_route(
    db_clean, app_client, admin_token,
):
    created_response = await app_client.post(ADMIN + "/entries", headers=auth(admin_token), json=_body())
    entry_id = created_response.json()["id"]
    generic_read = await app_client.get(f"{GENERIC}/entries/{entry_id}", headers=auth(admin_token))
    assert generic_read.status_code == 200
    generic_body = {
        "subject_type": "substance",
        "subject": "substance.synthetic.api",
        "claim": "A weaker generic edit.",
        "source_name": "Synthetic",
        "source_url": "https://example.invalid/generic",
        "evidence_tier": "clinically_studied",
        "domain": "skin_care",
    }
    for method, path, payload in (
        ("put", f"{GENERIC}/entries/{entry_id}", generic_body),
        ("post", f"{GENERIC}/entries/{entry_id}/approve", None),
        ("post", f"{GENERIC}/entries/{entry_id}/publish", None),
    ):
        response = await getattr(app_client, method)(
            path,
            headers=auth(admin_token),
            **({"json": payload} if payload is not None else {}),
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SPECIALIZED_AUTHORING_REQUIRED"


async def test_real_step8b_projects_exact_specialized_authoring_provenance(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        locator = "  synthetic section 3  "
        created = await _create(session, _entry(sources=(_new_source(locator=locator),)))
        approved = await _approve(session, created["id"])
        assert approved["sources"][0]["locator"] == locator
        await _verify(session, created["id"])
        published = await _publish(session, created["id"])
        await session.commit()
        projected = (await _step8b_claims(session))[0]

    assert projected.claim_id == uuid.UUID(published["id"])
    assert projected.claim_key == published["claim_key"]
    assert projected.claim_version == published["claim_version"]
    assert projected.sources[0].source_key == published["sources"][0]["source_key"]
    assert projected.sources[0].locator == published["sources"][0]["locator"] == locator


def test_vocabulary_is_derived_from_controlled_authorities():
    vocabulary = authoring.personal_applicability_vocabulary()
    skin = next(row for row in vocabulary["categories"] if row["category"] == "skin_care")
    hair = next(row for row in vocabulary["categories"] if row["category"] == "hair_care")
    assert {fact["fact_key"] for fact in skin["facts"]} == {
        "care_skin_usual_feel", "care_skin_sensitivity",
    }
    assert next(fact for fact in hair["facts"] if fact["fact_key"] == "care_hair_processing")[
        "expected_operator"
    ] == "contains_any"
    assert vocabulary["max_conditions"] == 4
    assert vocabulary["max_sources"] == 5
    assert vocabulary["schema_version"] == "1"


def test_production_decision_registries_remain_empty():
    assert PERSONAL_DECISION_SEMANTIC_RULES == ()
    assert PERSONAL_DECISION_POLICY_RULES == ()
    assert PERSONAL_DECISION_EXPLANATION_RULES == ()


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_authoring_import_boundary_and_inert_decision_vocabulary():
    imports = _imports(AUTHORING_PATH)
    forbidden_imports = (
        "personal_decision_semantics",
        "personal_decision_aggregation",
        "personal_decision_policy",
        "personal_decision_explanation",
        "ai_gateway",
        "off",
        "payments",
        "recommendation",
        "alternatives",
        "family",
    )
    assert not any(any(part in module for part in forbidden_imports) for module in imports)
    source = AUTHORING_PATH.read_text(encoding="utf-8").lower()
    for term in ("buy", "wait", "skip", "score", "ranking", "winner", "weight"):
        assert re.search(rf"\b{term}\b", source) is None


def test_step8c_through_step8f_do_not_import_step8g_authoring():
    for directory in (
        "personal_decision_semantics",
        "personal_decision_aggregation",
        "personal_decision_policy",
        "personal_decision_explanation",
    ):
        for path in (BACKEND_ROOT / "app" / "domains" / directory).glob("*.py"):
            assert "personal_applicability.authoring" not in path.read_text(encoding="utf-8")
