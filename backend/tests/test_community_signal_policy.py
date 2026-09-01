from __future__ import annotations

import pytest
from app.domains.product.community_signals import (
    OBSERVATIONS,
    PUBLIC_LANGUAGE,
    DeduplicationOutcome,
    GeographyEvidenceSummary,
    SignalEvidenceSummary,
    SignalScope,
    SignalStage,
    evaluate_signal,
)


def high_evidence(*, reporters_by_batch: dict[str, int], reporters: int = 8) -> SignalEvidenceSummary:
    return SignalEvidenceSummary(
        independent_reporters=reporters,
        photo_reporters=reporters,
        reporters_by_batch=reporters_by_batch,
        observation_window_days=14,
        freshest_report_age_days=1,
    )


def test_one_high_severity_report_is_internal_only() -> None:
    decision = evaluate_signal("visible_foreign_material", high_evidence(reporters_by_batch={"batch-a": 1}, reporters=1))

    assert decision.internal_review is True
    assert decision.public is False
    assert decision.stage is SignalStage.COLLECTING


def test_distributed_reports_across_four_batches_can_be_product_scoped() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        high_evidence(reporters_by_batch={"batch-a": 2, "batch-b": 2, "batch-c": 2, "batch-d": 2}),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.public is True
    assert decision.scope is SignalScope.PRODUCT
    assert decision.evidence_summary.distinct_batches == 4
    assert decision.evidence_summary.dominant_batch_reporters == 2
    assert decision.evidence_summary.dominant_batch_share == 0.25


def test_concentrated_seven_plus_one_pattern_remains_batch_scoped() -> None:
    decision = evaluate_signal("pack_leaking", high_evidence(reporters_by_batch={"batch-a": 7, "batch-b": 1}))

    assert decision.public is True
    assert decision.scope is SignalScope.BATCH
    assert decision.evidence_summary.dominant_batch_share == 0.875
    assert "batch_pattern_is_concentrated" in decision.reason_codes


def test_missing_batch_information_never_becomes_product_wide() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        SignalEvidenceSummary(independent_reporters=20, photo_reporters=20, freshest_report_age_days=1),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.public is True
    assert decision.scope is SignalScope.INSUFFICIENT_CONTEXT
    assert "batch_information_incomplete" in decision.reason_codes


def test_one_batch_with_many_independent_reporters_can_be_a_public_batch_signal() -> None:
    decision = evaluate_signal("pack_leaking", high_evidence(reporters_by_batch={"batch-a": 8}))

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.public is True
    assert decision.scope is SignalScope.BATCH


def test_community_signal_can_never_become_scientific_or_official_truth() -> None:
    decision = evaluate_signal(
        "seal_broken",
        high_evidence(reporters=100, reporters_by_batch={f"batch-{index}": 10 for index in range(10)}),
    )

    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_unknown_observation_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown community observation code"):
        evaluate_signal("adulterated", high_evidence(reporters_by_batch={"batch-a": 8}))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"independent_reporters": 2, "photo_reporters": 3},
        {"independent_reporters": 1, "photo_reporters": 1, "reporters_by_batch": {"batch-a": 2}},
        {"independent_reporters": 2, "photo_reporters": 1, "reporters_by_batch": {"": 1}},
        {"independent_reporters": 2, "photo_reporters": 1, "condition_context_reporters": 3},
    ],
)
def test_impossible_reporter_photo_or_batch_distributions_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SignalEvidenceSummary(**kwargs)


def test_photo_required_observation_does_not_surface_without_supporting_photos() -> None:
    decision = evaluate_signal(
        "insect_observed",
        SignalEvidenceSummary(independent_reporters=10, photo_reporters=0, reporters_by_batch={"batch-a": 5, "batch-b": 5}),
    )

    assert decision.stage is SignalStage.COLLECTING
    assert decision.public is False
    assert "supporting_photos_below_public_floor" in decision.reason_codes


def test_condition_observation_requires_normalized_context_evidence() -> None:
    decision = evaluate_signal(
        "did_not_solidify_as_expected",
        SignalEvidenceSummary(
            independent_reporters=4,
            photo_reporters=1,
            reporters_by_batch={"batch-a": 2, "batch-b": 2},
            condition_context_reporters=4,
        ),
    )

    assert decision.public is True
    assert "observation_requires_condition_context" in decision.reason_codes


def test_stale_reports_do_not_remain_public() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        SignalEvidenceSummary(
            independent_reporters=8,
            photo_reporters=8,
            reporters_by_batch={"batch-a": 4, "batch-b": 4},
            freshest_report_age_days=91,
        ),
    )

    assert decision.stage is SignalStage.COLLECTING
    assert decision.public is False
    assert "reports_are_stale" in decision.reason_codes


def test_evidence_retains_privacy_safe_geography_and_deduplication_outcome() -> None:
    geography = GeographyEvidenceSummary(aggregated_reporters=8, region_count=3, dominant_region_reporters=4)
    evidence = SignalEvidenceSummary(
        independent_reporters=8,
        photo_reporters=8,
        reporters_by_batch={"batch-a": 4, "batch-b": 4},
        duplicate_replay_rejections=2,
        deduplication_outcome=DeduplicationOutcome.DUPLICATES_AND_REPLAYS_REJECTED,
        geography=geography,
    )

    assert evidence.geography is not None
    assert evidence.geography.is_privacy_safe is True
    assert evidence.geography.dominant_region_share == 0.5
    assert evidence.duplicate_replay_rejections == 2


def test_catalog_and_public_language_stay_observational() -> None:
    banned = {"fake", "adulterated", "toxic", "unsafe", "poison", "fraud"}
    text = " ".join(
        [definition.code for definition in OBSERVATIONS.values()]
        + [definition.label_key for definition in OBSERVATIONS.values()]
        + list(PUBLIC_LANGUAGE.values())
    ).lower()

    assert not any(word in text for word in banned)
    assert PUBLIC_LANGUAGE["not_laboratory_testing"] == "These reports are not laboratory testing."
    assert PUBLIC_LANGUAGE["not_official_finding"] == "Not an official finding."
