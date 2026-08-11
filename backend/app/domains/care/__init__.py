"""Care Context and deterministic decision contracts."""

from app.domains.care.cadence import CARE_CADENCE_VERSION, HairWashCadenceDecision
from app.domains.care.decisions import CARE_DECISION_VERSION, CareDecisionSet, evaluate_care_context
from app.domains.care.product_preferences import CARE_PRODUCT_PREFERENCE_VERSION, CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY
from app.domains.care.routine_plan import (
    CARE_ROUTINE_PLAN_VERSION,
    CareEffortSource,
    CareInclusionReason,
    CareRoutineEffort,
    CareRoutinePlan,
    CareSelectionBasis,
    CareSlotPlan,
    plan_care_routine,
    routine_plan_fingerprint,
)
from app.domains.care.schemas import CARE_CONTEXT_VERSION, CareContext
from app.domains.care.simplification import (
    CARE_SIMPLIFICATION_VERSION,
    CareSimplificationDecision,
    CareSimplificationStatus,
    decide_care_simplification,
)
from app.domains.care.snapshot import (
    CARE_RECOMMENDATION_SNAPSHOT_VERSION,
    build_care_recommendation_snapshot,
    care_recommendation_snapshot_fingerprint,
)

__all__ = [
    "CARE_CONTEXT_VERSION",
    "CARE_CADENCE_VERSION",
    "CARE_DECISION_VERSION",
    "CARE_PRODUCT_PREFERENCE_VERSION",
    "CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY",
    "CARE_ROUTINE_PLAN_VERSION",
    "CARE_RECOMMENDATION_SNAPSHOT_VERSION",
    "CareEffortSource",
    "CareInclusionReason",
    "CareContext",
    "HairWashCadenceDecision",
    "CareDecisionSet",
    "CareRoutineEffort",
    "CareRoutinePlan",
    "CareSelectionBasis",
    "CareSlotPlan",
    "evaluate_care_context",
    "plan_care_routine",
    "routine_plan_fingerprint",
    "build_care_recommendation_snapshot",
    "care_recommendation_snapshot_fingerprint",
    "CARE_SIMPLIFICATION_VERSION",
    "CareSimplificationDecision",
    "CareSimplificationStatus",
    "decide_care_simplification",
]
