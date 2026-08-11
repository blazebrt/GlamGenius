"""Pure, explicit Care routine simplification decisions for V3-03.10."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domains.care.routine_plan import CareRoutineEffort

CARE_SIMPLIFICATION_VERSION = "v3-03.10"


class CareSimplificationStatus(StrEnum):
    AVAILABLE = "available"
    ALREADY_MINIMAL = "already_minimal"


@dataclass(frozen=True, slots=True)
class CareSimplificationDecision:
    version: str
    current_effort: CareRoutineEffort
    target_effort: CareRoutineEffort | None
    status: CareSimplificationStatus
    reason: str


def decide_care_simplification(
    current_effort: CareRoutineEffort,
) -> CareSimplificationDecision:
    """Lower exactly one canonical effort tier, with no inference."""
    targets = {
        CareRoutineEffort.DETAILED: CareRoutineEffort.BALANCED,
        CareRoutineEffort.BALANCED: CareRoutineEffort.MINIMAL,
        CareRoutineEffort.MINIMAL: None,
    }
    target = targets[current_effort]
    if target is None:
        return CareSimplificationDecision(
            version=CARE_SIMPLIFICATION_VERSION,
            current_effort=current_effort,
            target_effort=None,
            status=CareSimplificationStatus.ALREADY_MINIMAL,
            reason="already_minimal",
        )
    return CareSimplificationDecision(
        version=CARE_SIMPLIFICATION_VERSION,
        current_effort=current_effort,
        target_effort=target,
        status=CareSimplificationStatus.AVAILABLE,
        reason="explicit_user_simplification_request",
    )


__all__ = [
    "CARE_SIMPLIFICATION_VERSION",
    "CareSimplificationDecision",
    "CareSimplificationStatus",
    "decide_care_simplification",
]
