"""Structured community-observation policy for scanned products.

Community reports are useful because they can reveal fresh pack changes, batch
problems, and repeated real-world observations faster than a catalogue can.
They are also epistemically weaker than label facts, reviewed evidence, or an
official enforcement record.  This module keeps that boundary executable.

The rules here intentionally do *not* decide the scientific product grade.
They answer only whether independently submitted structured observations have
become strong enough to surface as a labelled community signal.

Constitutional boundary
-----------------------

Community observes. Regulators establish official findings. GlamGenius must
never turn a crowd observation into a laboratory result, an adulteration
finding, medical causation, or an official enforcement conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


COMMUNITY_SIGNAL_POLICY_VERSION: Final = "community-signals-v1"


class ObservationSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SignalStage(StrEnum):
    """Public maturity of a community observation.

    There is deliberately no ``officially_confirmed`` state.  Only the separate
    official-records domain may represent a regulator/laboratory finding.
    """

    COLLECTING = "collecting"
    EMERGING = "emerging"
    ESTABLISHED = "established"


class SignalScope(StrEnum):
    PRODUCT = "product"
    BATCH = "batch"


@dataclass(frozen=True, slots=True)
class ObservationDefinition:
    code: str
    severity: ObservationSeverity
    label_key: str
    requires_photo_for_public_signal: bool = False
    requires_batch_when_available: bool = False
    requires_condition_context: bool = False


# Closed vocabulary: public product reports never accept a user-authored claim
# such as "fake", "adulterated", "toxic", or "unsafe".  Those words convert
# an observation into a conclusion and are therefore not report codes.
OBSERVATIONS: Final[dict[str, ObservationDefinition]] = {
    # Product-data freshness / matching.
    "barcode_mismatch": ObservationDefinition(
        "barcode_mismatch", ObservationSeverity.LOW, "community.barcode_mismatch",
    ),
    "ingredients_changed": ObservationDefinition(
        "ingredients_changed", ObservationSeverity.LOW, "community.ingredients_changed",
        requires_photo_for_public_signal=True,
    ),
    "nutrition_changed": ObservationDefinition(
        "nutrition_changed", ObservationSeverity.LOW, "community.nutrition_changed",
        requires_photo_for_public_signal=True,
    ),
    "pack_size_changed": ObservationDefinition(
        "pack_size_changed", ObservationSeverity.LOW, "community.pack_size_changed",
        requires_photo_for_public_signal=True,
    ),
    "date_marking_unreadable": ObservationDefinition(
        "date_marking_unreadable", ObservationSeverity.MEDIUM,
        "community.date_marking_unreadable", requires_photo_for_public_signal=True,
        requires_batch_when_available=True,
    ),
    # Pack / product-condition observations.
    "seal_broken": ObservationDefinition(
        "seal_broken", ObservationSeverity.HIGH, "community.seal_broken",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "pack_leaking": ObservationDefinition(
        "pack_leaking", ObservationSeverity.HIGH, "community.pack_leaking",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "pack_swollen": ObservationDefinition(
        "pack_swollen", ObservationSeverity.HIGH, "community.pack_swollen",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "visible_foreign_material": ObservationDefinition(
        "visible_foreign_material", ObservationSeverity.HIGH,
        "community.visible_foreign_material", requires_photo_for_public_signal=True,
        requires_batch_when_available=True,
    ),
    "insect_observed": ObservationDefinition(
        "insect_observed", ObservationSeverity.HIGH, "community.insect_observed",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    "unusual_colour": ObservationDefinition(
        "unusual_colour", ObservationSeverity.MEDIUM, "community.unusual_colour",
        requires_batch_when_available=True,
    ),
    "unusual_smell": ObservationDefinition(
        "unusual_smell", ObservationSeverity.MEDIUM, "community.unusual_smell",
        requires_batch_when_available=True,
    ),
    "appeared_spoiled": ObservationDefinition(
        "appeared_spoiled", ObservationSeverity.HIGH, "community.appeared_spoiled",
        requires_photo_for_public_signal=True, requires_batch_when_available=True,
    ),
    # Traditional consumer observations are retained as observations only.  A
    # failed fridge/curdling observation is not an adulteration test.
    "did_not_solidify_as_expected": ObservationDefinition(
        "did_not_solidify_as_expected", ObservationSeverity.MEDIUM,
        "community.did_not_solidify_as_expected", requires_batch_when_available=True,
        requires_condition_context=True,
    ),
    "did_not_curdle_as_expected": ObservationDefinition(
        "did_not_curdle_as_expected", ObservationSeverity.MEDIUM,
        "community.did_not_curdle_as_expected", requires_batch_when_available=True,
        requires_condition_context=True,
    ),
}


@dataclass(frozen=True, slots=True)
class DisplayThreshold:
    emerging_independent_reporters: int
    established_independent_reporters: int
    emerging_photo_reports: int
    established_photo_reports: int


# Versioned, severity-sensitive defaults rather than one magic "N reports"
# threshold.  These values are product policy, not scientific constants; they
# must be calibrated with abuse and false-positive data before broad release.
DISPLAY_THRESHOLDS: Final[dict[ObservationSeverity, DisplayThreshold]] = {
    ObservationSeverity.LOW: DisplayThreshold(5, 15, 1, 3),
    ObservationSeverity.MEDIUM: DisplayThreshold(4, 10, 1, 3),
    ObservationSeverity.HIGH: DisplayThreshold(3, 8, 2, 4),
}


@dataclass(frozen=True, slots=True)
class SignalEvidenceSummary:
    """De-duplicated evidence entering the public-display decision.

    ``independent_reporters`` is deliberately not raw report count.  Device,
    account, replay, and abuse controls belong upstream; this policy consumes
    only the resulting independent count.
    """

    independent_reporters: int
    photo_reports: int
    distinct_batches: int = 0
    dominant_batch_reporters: int = 0
    report_window_days: int | None = None

    def __post_init__(self) -> None:
        for value in (
            self.independent_reporters,
            self.photo_reports,
            self.distinct_batches,
            self.dominant_batch_reporters,
        ):
            if value < 0:
                raise ValueError("community signal counts cannot be negative")
        if self.photo_reports > self.independent_reporters:
            raise ValueError("photo reports cannot exceed independent reporters")
        if self.dominant_batch_reporters > self.independent_reporters:
            raise ValueError("dominant batch reporters cannot exceed independent reporters")
        if self.report_window_days is not None and self.report_window_days < 0:
            raise ValueError("report window cannot be negative")


@dataclass(frozen=True, slots=True)
class SignalDecision:
    policy_version: str
    observation_code: str
    stage: SignalStage
    public: bool
    internal_review: bool
    scope: SignalScope
    analysis_score_eligible: bool
    official_finding: bool
    reason_codes: tuple[str, ...]


def observation_definition(code: str) -> ObservationDefinition:
    try:
        return OBSERVATIONS[code]
    except KeyError as exc:
        raise ValueError(f"unknown community observation code: {code}") from exc


def evaluate_signal(code: str, evidence: SignalEvidenceSummary) -> SignalDecision:
    """Evaluate visibility without converting observations into product truth."""

    definition = observation_definition(code)
    threshold = DISPLAY_THRESHOLDS[definition.severity]
    reasons: list[str] = []

    # One serious report deserves internal attention, but one customer (or one
    # competitor) must not be able to place a public warning on a product.
    internal_review = definition.severity is ObservationSeverity.HIGH and evidence.independent_reporters >= 1

    emerging = evidence.independent_reporters >= threshold.emerging_independent_reporters
    established = evidence.independent_reporters >= threshold.established_independent_reporters

    if definition.requires_photo_for_public_signal:
        emerging = emerging and evidence.photo_reports >= threshold.emerging_photo_reports
        established = established and evidence.photo_reports >= threshold.established_photo_reports
        if evidence.photo_reports < threshold.emerging_photo_reports:
            reasons.append("supporting_photos_below_public_floor")

    if evidence.independent_reporters < threshold.emerging_independent_reporters:
        reasons.append("independent_reports_below_public_floor")

    if established:
        stage = SignalStage.ESTABLISHED
    elif emerging:
        stage = SignalStage.EMERGING
    else:
        stage = SignalStage.COLLECTING

    # Prefer the narrowest claim the evidence can support.  A concentrated
    # single-batch pattern is a batch signal; it must not automatically stain
    # every batch sold under the brand/product name.
    scope = SignalScope.PRODUCT
    if (
        evidence.distinct_batches == 1
        and evidence.dominant_batch_reporters >= threshold.emerging_independent_reporters
    ):
        scope = SignalScope.BATCH
        reasons.append("signal_scoped_to_reported_batch")

    if definition.requires_condition_context:
        # Conditions are captured per report in the persistence/API layer.  The
        # public copy must always retain the "under reported conditions" caveat.
        reasons.append("observation_requires_condition_context")

    return SignalDecision(
        policy_version=COMMUNITY_SIGNAL_POLICY_VERSION,
        observation_code=code,
        stage=stage,
        public=stage is not SignalStage.COLLECTING,
        internal_review=internal_review,
        scope=scope,
        # These invariants are intentionally literal fields so downstream code
        # cannot accidentally infer that a popular report belongs in grading.
        analysis_score_eligible=False,
        official_finding=False,
        reason_codes=tuple(reasons),
    )


__all__ = [
    "COMMUNITY_SIGNAL_POLICY_VERSION",
    "DISPLAY_THRESHOLDS",
    "OBSERVATIONS",
    "DisplayThreshold",
    "ObservationDefinition",
    "ObservationSeverity",
    "SignalDecision",
    "SignalEvidenceSummary",
    "SignalScope",
    "SignalStage",
    "evaluate_signal",
    "observation_definition",
]
