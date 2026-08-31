from __future__ import annotations

import pytest

from app.domains.product.community_signals import (
    OBSERVATIONS,
    SignalEvidenceSummary,
    SignalScope,
    SignalStage,
    evaluate_signal,
)


def test_one_high_severity_report_is_internal_only() -> None:
    decision = evaluate_signal(
        "visible_foreign_material",
        SignalEvidenceSummary(independent_reporters=1, photo_reports=1, distinct_batches=1, dominant_batch_reporters=1),
    )

    assert decision.internal_review is True
    assert decision.public is False
    assert decision.stage is SignalStage.COLLECTING


def test_community_signal_can_never_become_scientific_or_official_truth() -> None:
    decision = evaluate_signal(
        "seal_broken",
        SignalEvidenceSummary(independent_reporters=100, photo_reports=100, distinct_batches=5),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.public is True
    assert decision.analysis_score_eligible is False
    assert decision.official_finding is False


def test_photo_required_observation_does_not_surface_without_supporting_photos() -> None:
    decision = evaluate_signal(
        "insect_observed",
        SignalEvidenceSummary(independent_reporters=10, photo_reports=0, distinct_batches=2),
    )

    assert decision.stage is SignalStage.COLLECTING
    assert decision.public is False
    assert "supporting_photos_below_public_floor" in decision.reason_codes


def test_repeated_single_batch_reports_stay_batch_scoped() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        SignalEvidenceSummary(
            independent_reporters=5,
            photo_reports=5,
            distinct_batches=1,
            dominant_batch_reporters=5,
        ),
    )

    assert decision.public is True
    assert decision.scope is SignalScope.BATCH
    assert "signal_scoped_to_reported_batch" in decision.reason_codes


def test_multi_batch_pattern_can_be_product_scoped() -> None:
    decision = evaluate_signal(
        "pack_leaking",
        SignalEvidenceSummary(independent_reporters=8, photo_reports=6, distinct_batches=4),
    )

    assert decision.stage is SignalStage.ESTABLISHED
    assert decision.scope is SignalScope.PRODUCT


def test_traditional_observation_retains_condition_context_caveat() -> None:
    decision = evaluate_signal(
        "did_not_solidify_as_expected",
        SignalEvidenceSummary(independent_reporters=4, photo_reports=1, distinct_batches=2),
    )

    assert decision.public is True
    assert "observation_requires_condition_context" in decision.reason_codes
    assert decision.analysis_score_eligible is False


def test_catalog_contains_observations_not_accusatory_conclusions() -> None:
    banned = {"fake", "adulterated", "toxic", "unsafe", "poison", "fraud"}
    haystack = " ".join(
        [definition.code for definition in OBSERVATIONS.values()]
        + [definition.label_key for definition in OBSERVATIONS.values()]
    ).lower()

    assert not any(word in haystack for word in banned)


def test_evidence_summary_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError):
        SignalEvidenceSummary(independent_reporters=2, photo_reports=3)

    with pytest.raises(ValueError):
        SignalEvidenceSummary(independent_reporters=1, photo_reports=1, dominant_batch_reporters=2)
