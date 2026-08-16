from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.domains.care.decisions import decision_fingerprint, evaluate_care_context
from app.domains.care.routine_plan import plan_care_routine, routine_plan_fingerprint
from app.domains.evidence.applicability import (
    EVIDENCE_APPLICABILITY_VERSION,
    EvidenceApplicability,
    parse_behavior_applicability,
)
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import RuleEvidenceAssessment, assess_rule_evidence, evidence_state_for_rule
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

RULE = dict(
    domain="skin_care",
    rule_kind="ingredient_compatibility",
    rule_id="rule.retinoid_bha",
    rule_version="phase6-v1",
)
NOW = datetime(2026, 8, 16, tzinfo=UTC)
VALID_APPLICABILITY = {
    "behavior_applicability": {
        "schema_version": EVIDENCE_APPLICABILITY_VERSION,
        "jurisdictions": ["global"],
        "populations": ["adults"],
        "formulations": ["topical"],
        "usage_contexts": ["leave_on"],
    }
}


def _source(**overrides):
    values = dict(
        source_key=f"test.source.{uuid.uuid4().hex}", source_series_key="test.source",
        source_type="official_guideline", title="Test source", publisher="Test body",
        jurisdiction="US", accessed_at=NOW, status="active",
    )
    values.update(overrides)
    return EvidenceSource(**values)


def _claim(**overrides):
    values = dict(
        claim_key=f"test.claim.{uuid.uuid4().hex}", claim_version=1, domain="skin_care",
        subject_type="ingredient", subject_key="retinoid", claim_type="usage_context",
        summary="Summary", scope="Free-text scope is not structured applicability.",
        review_status="approved", reviewed_by="reviewer", reviewed_at=NOW,
        claim_status="supported", evidence_strength="moderate",
        strength_rationale="Reviewed rationale.", regulatory_context="unknown", ai_generated=False,
    )
    values.update(overrides)
    return EvidenceClaim(**values)


async def _bundle(
    session,
    *,
    structured_value=None,
    relationship="supports",
    claim_status="supported",
    source_relationship="supports",
    source_reviewed=True,
    source_status="active",
    rule_reviewed=True,
    rule_id=RULE["rule_id"],
):
    source = _source(status=source_status)
    claim = _claim(structured_value=structured_value, claim_status=claim_status)
    session.add_all([source, claim])
    await session.flush()
    session.add(EvidenceClaimSource(
        claim_id=claim.id,
        source_id=source.id,
        relationship=source_relationship,
        reviewed_at=NOW if source_reviewed else None,
        reviewed_by="reviewer" if source_reviewed else None,
    ))
    rule_values = {**RULE, "rule_id": rule_id}
    session.add(RuleEvidenceLink(
        **rule_values,
        rule_id=rule_id,
        claim_id=claim.id,
        relationship=relationship,
        reviewed_at=NOW if rule_reviewed else None,
        reviewed_by="reviewer" if rule_reviewed else None,
    ))
    await session.commit()
    return source, claim


def test_applicability_parser_returns_immutable_normalized_contract():
    result = parse_behavior_applicability({
        "behavior_applicability": {
            "schema_version": EVIDENCE_APPLICABILITY_VERSION,
            "jurisdictions": [" US ", "US"],
            "populations": ["adults"],
            "formulations": ["topical"],
            "usage_contexts": ["leave_on"],
        }
    })
    assert result.valid is True
    assert result.applicability == EvidenceApplicability(
        schema_version=EVIDENCE_APPLICABILITY_VERSION,
        jurisdictions=("US",), populations=("adults",), formulations=("topical",), usage_contexts=("leave_on",),
    )
    assert isinstance(result.applicability.jurisdictions, tuple)
    assert result.invalid_reason_codes == ()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "structured_value_missing"),
        ({}, "applicability_missing"),
        ({"scope": "US adults"}, "applicability_missing"),
        ({"behavior_applicability": "not an object"}, "malformed_applicability"),
        ({"behavior_applicability": {"schema_version": "v3-99", "jurisdictions": ["US"], "populations": ["adults"], "formulations": ["topical"], "usage_contexts": ["leave_on"]}}, "unsupported_schema_version"),
    ],
)
def test_applicability_parser_fails_closed_for_missing_or_malformed_contract(value, reason):
    result = parse_behavior_applicability(value)
    assert result.valid is False
    assert reason in result.invalid_reason_codes
    assert result.applicability is None


@pytest.mark.parametrize("missing", ["jurisdictions", "populations", "formulations", "usage_contexts"])
def test_applicability_parser_requires_every_non_empty_dimension(missing):
    value = {"behavior_applicability": dict(VALID_APPLICABILITY["behavior_applicability"])}
    value["behavior_applicability"].pop(missing)
    result = parse_behavior_applicability(value)
    assert result.valid is False
    assert f"{missing[:-1]}_missing" in result.invalid_reason_codes


@pytest.mark.parametrize("bad_value", [[""], [1], "US", {"value": "US"}])
def test_applicability_parser_rejects_malformed_dimension_values(bad_value):
    value = {"behavior_applicability": dict(VALID_APPLICABILITY["behavior_applicability"])}
    value["behavior_applicability"]["jurisdictions"] = bad_value
    result = parse_behavior_applicability(value)
    assert result.valid is False
    assert result.invalid_reason_codes == ("malformed_applicability",)


async def test_no_link_has_no_provenance_substance_or_behavior_eligibility(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment == RuleEvidenceAssessment(False, False, False)


@pytest.mark.parametrize(
    "structured_value",
    [None, {"scope": "US adults"}, {"behavior_applicability": {"schema_version": EVIDENCE_APPLICABILITY_VERSION, "jurisdictions": ["US"], "populations": ["adults"], "formulations": ["topical"]}}, {"behavior_applicability": {"schema_version": "v3-99", "jurisdictions": ["US"], "populations": ["adults"], "formulations": ["topical"], "usage_contexts": ["leave_on"]}}],
)
async def test_reviewed_supported_path_without_valid_applicability_stays_ineligible(db_clean, structured_value):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=structured_value)
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is True
    assert assessment.behavior_evidence_eligible is False


async def test_valid_reviewed_supported_path_is_behavior_eligible(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY)
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is True
    assert assessment.behavior_evidence_eligible is True


@pytest.mark.parametrize("relationship", ["background", "qualifies", "limits"])
async def test_non_support_rule_relationships_never_become_behavior_eligible(db_clean, relationship):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY, relationship=relationship)
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is False
    assert assessment.behavior_evidence_eligible is False


async def test_qualified_claim_never_becomes_behavior_eligible(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY, claim_status="qualified")
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is False
    assert assessment.behavior_evidence_eligible is False


@pytest.mark.parametrize("source_status, source_reviewed", [("retired", True), ("active", False)])
async def test_inactive_or_unreviewed_source_path_fails_closed(db_clean, source_status, source_reviewed):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY, source_status=source_status, source_reviewed=source_reviewed)
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.behavior_evidence_eligible is False


async def test_background_only_claim_source_path_fails_closed(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY, source_relationship="background")
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.behavior_evidence_eligible is False


async def test_unrelated_draft_rule_link_does_not_poison_valid_support_path(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        _, claim = await _bundle(session, structured_value=VALID_APPLICABILITY)
        # A foreign-key-valid draft link is created against a second claim.
        draft = _claim(review_status="draft", claim_status=None, evidence_strength=None, strength_rationale=None)
        session.add(draft)
        await session.flush()
        session.add(RuleEvidenceLink(
            **RULE, claim_id=draft.id, relationship="qualifies",
        ))
        await session.commit()
        assessment = await assess_rule_evidence(session, **RULE)
    assert claim is not None
    assert assessment.provenance_present is True
    assert assessment.substantive_support_present is True
    assert assessment.behavior_evidence_eligible is True


async def test_exact_rule_identity_is_required(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=VALID_APPLICABILITY, rule_id="rule.unknown")
        assessment = await assess_rule_evidence(session, **{**RULE, "rule_id": "rule.unknown"})
    assert assessment == RuleEvidenceAssessment(False, False, False)


async def test_evidence_linkage_state_remains_separate_from_behavior_eligibility(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await _bundle(session, structured_value=None)
        assert await evidence_state_for_rule(session, **RULE) == "evidence_linked"
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.provenance_present is True
    assert assessment.behavior_evidence_eligible is False


async def test_pilot_seed_remains_draft_and_ineligible(db_clean):
    from app.bootstrap import run as run_reference_seed
    from app.domains.evidence.models import EvidenceClaim

    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        claims = (await session.execute(select(EvidenceClaim))).scalars().all()
        assessments = [
            await assess_rule_evidence(session, domain="skin_care", rule_kind=link.rule_kind, rule_id=link.rule_id, rule_version=link.rule_version)
            for link in (await session.execute(select(RuleEvidenceLink))).scalars().all()
        ]
    assert all(claim.review_status == "draft" for claim in claims)
    assert all(not assessment.behavior_evidence_eligible for assessment in assessments)


def test_care_decision_and_routine_plan_fingerprints_remain_deterministic():
    from tests.test_care_decisions import _context

    context = _context()
    first_decisions = evaluate_care_context(context)
    second_decisions = evaluate_care_context(context)
    first_plan = plan_care_routine(context, first_decisions)
    second_plan = plan_care_routine(context, second_decisions)
    assert decision_fingerprint(first_decisions) == decision_fingerprint(second_decisions)
    assert routine_plan_fingerprint(first_plan) == routine_plan_fingerprint(second_plan)
