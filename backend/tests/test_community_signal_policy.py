from __future__ import annotations

from pathlib import Path

import pytest
from app.domains.product.community_signals import (
    COMMUNITY_DISCLOSURE_KEYS,
    OBSERVATIONS,
    DeduplicationOutcome,
    EvidenceWindowSummary,
    GeographyEvidenceSummary,
    SignalEvidenceSummary,
    SignalKind,
    SignalScope,
    SignalStage,
    evaluate_signal,
)


def active(*, reporters: int, batches: dict[str, int] | None = None, photos: int | None = None, contexts: int = 0):
    return EvidenceWindowSummary(
        independent_reporters=reporters,
        photo_reporters=reporters if photos is None else photos,
        reporters_by_batch=batches or {},
        condition_context_reporters=contexts,
    )


def evidence(*, current: EvidenceWindowSummary, historical: EvidenceWindowSummary | None = None) -> SignalEvidenceSummary:
    return SignalEvidenceSummary(active=current, historical=historical or EvidenceWindowSummary())


def test_ingredients_changed_is_product_data_verification_without_batch_information() -> None:
    decision = evaluate_signal("ingredients_changed", evidence(current=active(reporters=20, photos=20)))

    assert decision.public is True
    assert decision.scope is SignalScope.PRODUCT
    assert decision.signal_kind is SignalKind.DATA_VERIFICATION
    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_barcode_mismatch_is_explicit_data_verification_not_condition_warning() -> None:
    decision = evaluate_signal("barcode_mismatch", evidence(current=active(reporters=5, photos=0)))

    assert decision.signal_kind is SignalKind.DATA_VERIFICATION
    assert decision.scope is SignalScope.PRODUCT


def test_physical_condition_without_batch_information_is_insufficient_context() -> None:
    decision = evaluate_signal("pack_leaking", evidence(current=active(reporters=20, photos=20)))

    assert decision.public is True
    assert decision.scope is SignalScope.INSUFFICIENT_CONTEXT


def test_distributed_current_physical_reports_can_be_product_scoped() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        evidence(current=active(reporters=8, batches={"batch-a": 2, "batch-b": 2, "batch-c": 2, "batch-d": 2})),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.scope is SignalScope.PRODUCT
    assert decision.evidence_summary.active.dominant_batch_share == 0.25


def test_concentrated_current_seven_plus_one_physical_pattern_remains_batch_scoped() -> None:
    decision = evaluate_signal("pack_leaking", evidence(current=active(reporters=8, batches={"batch-a": 7, "batch-b": 1})))

    assert decision.public is True
    assert decision.scope is SignalScope.BATCH
    assert "community.reason.active_batch_pattern_concentrated" in decision.reason_keys


def test_one_physical_batch_with_sufficient_current_reports_can_be_public() -> None:
    decision = evaluate_signal("pack_leaking", evidence(current=active(reporters=8, batches={"batch-a": 8})))

    assert decision.public is True
    assert decision.scope is SignalScope.BATCH


def test_condition_sensitive_signal_requires_current_structured_context() -> None:
    decision = evaluate_signal(
        "did_not_solidify_as_expected",
        evidence(current=active(reporters=10, batches={"batch-a": 5, "batch-b": 5}, contexts=0)),
    )

    assert decision.public is False
    assert "community.reason.active_condition_context_below_public_floor" in decision.reason_keys


def test_condition_sensitive_signal_without_batch_information_never_becomes_product_wide() -> None:
    decision = evaluate_signal(
        "did_not_solidify_as_expected",
        evidence(current=active(reporters=10, contexts=10)),
    )

    assert decision.public is True
    assert decision.scope is SignalScope.INSUFFICIENT_CONTEXT
    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_historical_reports_alone_do_not_establish_current_maturity() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        evidence(current=active(reporters=0), historical=active(reporters=20, batches={"batch-a": 20})),
    )

    assert decision.stage is SignalStage.COLLECTING
    assert decision.public is False
    assert decision.evidence_summary.total_independent_reporters == 20


def test_one_current_report_does_not_resurrect_twenty_historical_reports() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        evidence(
            current=active(reporters=1, batches={"batch-current": 1}),
            historical=active(reporters=20, batches={"batch-old": 20}),
        ),
    )

    assert decision.stage is SignalStage.COLLECTING
    assert decision.public is False
    assert decision.evidence_summary.active.independent_reporters == 1
    assert decision.evidence_summary.total_independent_reporters == 21


def test_current_threshold_and_scope_use_eight_active_reports_not_thirty_historical_reports() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        evidence(
            current=active(reporters=8, batches={"batch-b": 2, "batch-c": 2, "batch-d": 2, "batch-e": 2}),
            historical=active(reporters=30, batches={"batch-a": 30}),
        ),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.scope is SignalScope.PRODUCT
    assert decision.evidence_summary.total_independent_reporters == 38


def test_historical_batch_concentration_does_not_override_distributed_active_batches() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        evidence(
            current=active(reporters=8, batches={"batch-b": 2, "batch-c": 2, "batch-d": 2, "batch-e": 2}),
            historical=active(reporters=20, batches={"batch-a": 20}),
        ),
    )

    assert decision.scope is SignalScope.PRODUCT
    assert decision.evidence_summary.historical.dominant_batch_share == 1.0
    assert decision.evidence_summary.active.dominant_batch_share == 0.25


def test_high_severity_active_observation_is_internal_review_even_before_public_threshold() -> None:
    decision = evaluate_signal("visible_foreign_material", evidence(current=active(reporters=1, batches={"batch-a": 1})))

    assert decision.internal_review is True
    assert decision.public is False


@pytest.mark.parametrize("code", list(OBSERVATIONS))
def test_no_possible_community_signal_can_change_scientific_or_official_truth(code: str) -> None:
    decision = evaluate_signal(code, evidence(current=active(reporters=100, batches={f"batch-{index}": 10 for index in range(10)}, contexts=100)))

    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_unknown_observation_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown community observation code"):
        evaluate_signal("invented_observation", evidence(current=active(reporters=1)))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"independent_reporters": 2, "photo_reporters": 3},
        {"independent_reporters": 1, "photo_reporters": 1, "reporters_by_batch": {"batch-a": 2}},
        {"independent_reporters": 2, "photo_reporters": 1, "reporters_by_batch": {"": 1}},
        {"independent_reporters": 2, "photo_reporters": 1, "condition_context_reporters": 3},
        {"independent_reporters": -1},
    ],
)
def test_impossible_active_evidence_distributions_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        EvidenceWindowSummary(**kwargs)


def test_impossible_summary_metadata_is_rejected() -> None:
    with pytest.raises(ValueError):
        SignalEvidenceSummary(active=active(reporters=1), duplicate_replay_rejections=1)
    with pytest.raises(ValueError):
        SignalEvidenceSummary(
            active=active(reporters=1),
            active_geography=GeographyEvidenceSummary(aggregated_reporters=2, region_count=1, dominant_region_reporters=2),
        )


def test_active_geography_and_deduplication_metadata_are_retained_without_precise_location() -> None:
    summary = SignalEvidenceSummary(
        active=active(reporters=8, batches={"batch-a": 4, "batch-b": 4}),
        duplicate_replay_rejections=2,
        deduplication_outcome=DeduplicationOutcome.DUPLICATES_AND_REPLAYS_REJECTED,
        active_geography=GeographyEvidenceSummary(aggregated_reporters=8, region_count=3, dominant_region_reporters=4),
    )

    assert summary.active_geography is not None
    assert summary.active_geography.is_privacy_safe is True
    assert summary.active_geography.dominant_region_share == 0.5
    assert summary.duplicate_replay_rejections == 2


def test_policy_contains_semantic_disclosure_keys_not_final_customer_copy() -> None:
    source = Path("app/domains/product/community_signals.py").read_text(encoding="utf-8")

    assert COMMUNITY_DISCLOSURE_KEYS == (
        "community.disclosure.heading",
        "community.disclosure.reported_by_shoppers",
        "community.disclosure.not_laboratory_testing",
        "community.disclosure.not_official_finding",
    )
    for final_copy in ("Consumer observations", "Reported by shoppers", "These reports are not laboratory testing."):
        assert final_copy not in source
