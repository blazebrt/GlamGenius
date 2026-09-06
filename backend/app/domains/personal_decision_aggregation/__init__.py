"""Step 8D deterministic personal decision signal aggregation.

Gathers one exact Step 8C result into the distinct reviewed rules it
represents, where each was encountered, which claim projections carry no
reviewed mapping, and which directions are present. Set membership, never
voting. No score, no weight, no conflict resolution, no verdict, and no
database, network or AI access of any kind.
"""

from app.domains.personal_decision_aggregation.enums import (
    PersonalSemanticMappingCoverage,
    PersonalSignalSet,
)
from app.domains.personal_decision_aggregation.service import (
    AggregatedPersonalDecisionRule,
    PersonalDecisionAggregation,
    PersonalDecisionAggregationInvariantError,
    PersonalDecisionSignalOccurrence,
    RuleEvidenceTarget,
    RuleIdentity,
    UnmappedPersonalDecisionClaim,
    aggregate_personal_decision_signals,
)

__all__ = [
    "AggregatedPersonalDecisionRule",
    "PersonalDecisionAggregation",
    "PersonalDecisionAggregationInvariantError",
    "PersonalDecisionSignalOccurrence",
    "PersonalSemanticMappingCoverage",
    "PersonalSignalSet",
    "RuleEvidenceTarget",
    "RuleIdentity",
    "UnmappedPersonalDecisionClaim",
    "aggregate_personal_decision_signals",
]
