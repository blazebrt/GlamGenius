"""Pure, user-grounded Hair wash cadence decisions for V3-03.8.

Cadence decides when an already-selected Hair routine is relevant.  It never
selects products and deliberately keeps ambiguous user declarations unknown.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from enum import StrEnum

CARE_CADENCE_VERSION = "v3-03.8"


class HairWashCadenceStatus(StrEnum):
    DUE = "due"
    NOT_DUE = "not_due"
    NEEDS_ANCHOR = "needs_anchor"
    UNSCHEDULED = "unscheduled"


class HairWashCadenceReason(StrEnum):
    DAILY_DECLARATION = "daily_declaration"
    INTERVAL_ELAPSED = "interval_elapsed"
    INTERVAL_NOT_ELAPSED = "interval_not_elapsed"
    NO_WASH_HISTORY = "no_wash_history"
    FREQUENCY_MISSING = "frequency_missing"
    FREQUENCY_NOT_SURE = "frequency_not_sure"
    FREQUENCY_VARIABLE = "frequency_variable"
    FREQUENCY_IMPRECISE = "frequency_imprecise"


@dataclass(frozen=True, slots=True)
class HairWashCadenceDecision:
    cadence_version: str
    plan_date: date
    declared_frequency: str | None
    status: HairWashCadenceStatus
    reason: HairWashCadenceReason
    interval_days: int | None
    last_wash_on: date | None
    next_due_on: date | None

    def as_payload(self) -> dict[str, str | None]:
        return {
            "version": self.cadence_version,
            "status": self.status.value,
            "reason": self.reason.value,
            "declared_frequency": self.declared_frequency,
            "last_wash_on": self.last_wash_on.isoformat() if self.last_wash_on else None,
            "next_due_on": self.next_due_on.isoformat() if self.next_due_on else None,
        }


INTERVAL_DAYS: dict[str, int] = {
    "daily": 1,
    "several_times_week": 2,
    "weekly": 7,
}


def decide_hair_wash_cadence(
    declared_frequency: str | None,
    *,
    plan_date: date,
    last_wash_on: date | None,
) -> HairWashCadenceDecision:
    """Resolve timing from the trusted declaration and durable wash history."""
    frequency = declared_frequency.strip().casefold() if isinstance(declared_frequency, str) else None
    if last_wash_on is not None and last_wash_on > plan_date:
        last_wash_on = None
    interval_days = INTERVAL_DAYS.get(frequency or "")
    if frequency is None or frequency == "":
        return HairWashCadenceDecision(
            CARE_CADENCE_VERSION, plan_date, None, HairWashCadenceStatus.UNSCHEDULED,
            HairWashCadenceReason.FREQUENCY_MISSING, None, last_wash_on, None,
        )
    if frequency == "not_sure":
        reason = HairWashCadenceReason.FREQUENCY_NOT_SURE
    elif frequency == "variable":
        reason = HairWashCadenceReason.FREQUENCY_VARIABLE
    elif frequency == "less_than_weekly":
        reason = HairWashCadenceReason.FREQUENCY_IMPRECISE
    else:
        reason = None
    if interval_days is None:
        return HairWashCadenceDecision(
            CARE_CADENCE_VERSION, plan_date, frequency, HairWashCadenceStatus.UNSCHEDULED,
            reason or HairWashCadenceReason.FREQUENCY_IMPRECISE, None, last_wash_on, None,
        )
    if last_wash_on is None:
        if frequency == "daily":
            return HairWashCadenceDecision(
                CARE_CADENCE_VERSION, plan_date, frequency, HairWashCadenceStatus.DUE,
                HairWashCadenceReason.DAILY_DECLARATION, interval_days, None, None,
            )
        return HairWashCadenceDecision(
            CARE_CADENCE_VERSION, plan_date, frequency, HairWashCadenceStatus.NEEDS_ANCHOR,
            HairWashCadenceReason.NO_WASH_HISTORY, interval_days, None, None,
        )
    next_due = last_wash_on + timedelta(days=interval_days)
    status = HairWashCadenceStatus.DUE if plan_date >= next_due else HairWashCadenceStatus.NOT_DUE
    return HairWashCadenceDecision(
        CARE_CADENCE_VERSION, plan_date, frequency, status,
        HairWashCadenceReason.INTERVAL_ELAPSED if status is HairWashCadenceStatus.DUE
        else HairWashCadenceReason.INTERVAL_NOT_ELAPSED,
        interval_days, last_wash_on, next_due,
    )


def hair_wash_cadence_fingerprint(decision: HairWashCadenceDecision) -> str:
    """Hash only behaviorally meaningful cadence fields."""
    payload = asdict(decision)
    payload["plan_date"] = decision.plan_date.isoformat()
    payload["status"] = decision.status.value
    payload["reason"] = decision.reason.value
    payload["last_wash_on"] = decision.last_wash_on.isoformat() if decision.last_wash_on else None
    payload["next_due_on"] = decision.next_due_on.isoformat() if decision.next_due_on else None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CARE_CADENCE_VERSION",
    "HairWashCadenceDecision",
    "HairWashCadenceReason",
    "HairWashCadenceStatus",
    "INTERVAL_DAYS",
    "decide_hair_wash_cadence",
    "hair_wash_cadence_fingerprint",
]
