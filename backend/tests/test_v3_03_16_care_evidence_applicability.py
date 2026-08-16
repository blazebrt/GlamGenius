from __future__ import annotations

import uuid

import pytest
from app.domains.care.evidence_applicability import (
    CARE_EVIDENCE_APPLICABILITY_VERSION,
    CareApplicabilitySignals,
    resolve_care_evidence_applicability,
)
from app.domains.evidence.applicability import EVIDENCE_APPLICABILITY_VERSION, EvidenceApplicability
from app.domains.evidence.service import BehaviorEligibleEvidencePath, RuleEvidenceAssessment, assess_rule_evidence
from app.shared.database.sql import get_sessionmaker


def _scope(**overrides) -> EvidenceApplicability:
    values = dict(
        schema_version=EVIDENCE_APPLICABILITY_VERSION,
        jurisdictions=("US",),
        populations=("adults",),
        formulations=("topical",),
        usage_contexts=("leave_on",),
    )
    values.update(overrides)
    return EvidenceApplicability(**values)


def _signals(**overrides) -> CareApplicabilitySignals:
    values = dict(
        jurisdictions=("US",),
        populations=("adults",),
        formulations=("topical",),
        usage_contexts=("leave_on",),
    )
    values.update(overrides)
    return CareApplicabilitySignals(**values)


def _assessment(*scopes: EvidenceApplicability) -> RuleEvidenceAssessment:
    paths = tuple(
        BehaviorEligibleEvidencePath(claim_id=uuid.UUID(int=index + 1), applicability=scope)
        for index, scope in enumerate(scopes)
    )
    return RuleEvidenceAssessment(
        provenance_present=True,
        substantive_support_present=True,
        behavior_evidence_eligible=bool(paths),
        behavior_eligible_paths=paths,
    )


def test_no_behavior_eligible_evidence_fails_closed():
    result = resolve_care_evidence_applicability(
        RuleEvidenceAssessment(False, False, False),
        _signals(),
    )
    assert result.applicability_version == CARE_EVIDENCE_APPLICABILITY_VERSION
    assert result.applicable is False
    assert result.behavior_evidence_eligible is False
    assert result.reason_codes == ("evidence_not_behavior_eligible",)


def test_exact_match_returns_only_matching_claim_id():
    assessment = _assessment(_scope())
    result = resolve_care_evidence_applicability(assessment, _signals())
    assert result.applicable is True
    assert result.matching_claim_ids == (uuid.UUID(int=1),)
    assert result.reason_codes == ()


@pytest.mark.parametrize(
    ("dimension", "reason"),
    [
        ("populations", "population_signal_missing"),
        ("formulations", "formulation_signal_missing"),
        ("usage_contexts", "usage_context_signal_missing"),
    ],
)
def test_missing_non_jurisdiction_signal_fails_closed(dimension, reason):
    result = resolve_care_evidence_applicability(_assessment(_scope()), _signals(**{dimension: ()}))
    assert result.applicable is False
    assert reason in result.reason_codes


def test_specific_jurisdiction_requires_signal():
    result = resolve_care_evidence_applicability(_assessment(_scope()), _signals(jurisdictions=()))
    assert result.applicable is False
    assert "jurisdiction_signal_missing" in result.reason_codes


def test_global_jurisdiction_matches_with_or_without_signal():
    scope = _scope(jurisdictions=("global",))
    for signals in (_signals(), _signals(jurisdictions=())):
        result = resolve_care_evidence_applicability(_assessment(scope), signals)
        assert result.applicable is True


@pytest.mark.parametrize(
    ("dimension", "value", "reason"),
    [
        ("jurisdictions", ("EU",), "jurisdiction_mismatch"),
        ("populations", ("teens",), "population_mismatch"),
        ("formulations", ("rinse_off",), "formulation_mismatch"),
        ("usage_contexts", ("wash_off",), "usage_context_mismatch"),
    ],
)
def test_exact_dimension_mismatches_fail_closed(dimension, value, reason):
    result = resolve_care_evidence_applicability(_assessment(_scope()), _signals(**{dimension: value}))
    assert result.applicable is False
    assert reason in result.reason_codes
    assert "no_applicable_support_path" in result.reason_codes


def test_exact_tokens_do_not_use_synonyms_or_substrings():
    result = resolve_care_evidence_applicability(
        _assessment(_scope(populations=("adults",), usage_contexts=("leave_on",))),
        _signals(populations=("adult",), usage_contexts=("leave-on",)),
    )
    assert result.applicable is False
    assert "population_mismatch" in result.reason_codes
    assert "usage_context_mismatch" in result.reason_codes


def test_multiple_paths_return_only_matching_claim_ids():
    result = resolve_care_evidence_applicability(
        _assessment(_scope(jurisdictions=("EU",)), _scope()),
        _signals(),
    )
    assert result.applicable is True
    assert result.matching_claim_ids == (uuid.UUID(int=2),)


def test_all_paths_mismatch_deterministically_order_reasons():
    result = resolve_care_evidence_applicability(
        _assessment(_scope(jurisdictions=("EU",)), _scope(populations=("teens",))),
        _signals(),
    )
    assert result.applicable is False
    assert result.reason_codes == (
        "jurisdiction_mismatch",
        "population_mismatch",
        "no_applicable_support_path",
    )


def test_signal_whitespace_and_duplicates_are_normalized():
    signals = CareApplicabilitySignals(
        jurisdictions=(" US ", "US"),
        populations=(" adults ", "adults"),
        formulations=(" topical ", "topical"),
        usage_contexts=(" leave_on ", "leave_on"),
    )
    assert signals == _signals()
    assert resolve_care_evidence_applicability(_assessment(_scope()), signals).applicable is True


def test_malformed_signal_fails_closed():
    signals = _signals(populations=("adults", 1))
    result = resolve_care_evidence_applicability(_assessment(_scope()), signals)
    assert result.applicable is False
    assert result.reason_codes == ("malformed_signal",)


async def test_database_assessment_exposes_only_behavior_eligible_paths(db_clean):
    from tests.test_v3_03_15_evidence_applicability import RULE, VALID_APPLICABILITY, _bundle

    factory = get_sessionmaker()
    async with factory() as session:
        _, claim = await _bundle(session, structured_value=VALID_APPLICABILITY)
        assessment = await assess_rule_evidence(session, **RULE)
    assert assessment.behavior_evidence_eligible is True
    assert assessment.behavior_eligible_paths
    assert assessment.behavior_eligible_paths[0].claim_id == claim.id
    assert assessment.behavior_eligible_paths[0].applicability.schema_version == EVIDENCE_APPLICABILITY_VERSION
