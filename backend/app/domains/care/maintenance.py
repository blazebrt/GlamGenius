"""Deterministic maintenance timing decisions (VC-06).

Timing only. This module answers "is this upkeep act due?" and nothing else:
it never picks a product, never books anything, and never invents a date the
customer has not given us. A tracked kind with no recorded history stays
``needs_anchor`` rather than being anchored to today, because guessing would
silently manufacture a schedule the customer never set.

Pure functions over plain values. No database, no AI, no clock reads — the
plan date is always passed in so a plan is reproducible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from app.domains.care.maintenance_rules import (
    MAINTENANCE_CATALOGUE_VERSION,
    MAINTENANCE_KINDS,
    MAINTENANCE_VERSION,
    MAX_INTERVAL_DAYS,
    MIN_INTERVAL_DAYS,
    MaintenanceKind,
    lead_days_for,
)


class MaintenanceStatus(StrEnum):
    DUE = "due"
    COMING_UP = "coming_up"
    NOT_DUE = "not_due"
    #: Tracked, but the customer has not chosen a rhythm yet.
    NEEDS_CADENCE = "needs_cadence"
    #: Rhythm chosen, but no date to count from yet.
    NEEDS_ANCHOR = "needs_anchor"
    NOT_TRACKED = "not_tracked"

#: The statuses that mean a fact is missing rather than a schedule exists.
INCOMPLETE_STATUSES = frozenset({MaintenanceStatus.NEEDS_CADENCE, MaintenanceStatus.NEEDS_ANCHOR})


class MaintenanceReason(StrEnum):
    NOT_TRACKED = "not_tracked"
    NO_CADENCE_SET = "no_cadence_set"
    NO_RECORDED_DATE = "no_recorded_date"
    INTERVAL_ELAPSED = "interval_elapsed"
    INTERVAL_APPROACHING = "interval_approaching"
    INTERVAL_NOT_ELAPSED = "interval_not_elapsed"


@dataclass(frozen=True, slots=True)
class MaintenanceState:
    """What the customer has told us about one kind.

    ``last_done_on`` is only ever a date they recorded themselves.
    """

    kind_key: str
    tracked: bool = False
    #: The rhythm the customer chose. ``None`` means they have not chosen one,
    #: which is a missing fact — never a reason to apply the catalogue preset.
    interval_days: int | None = None
    last_done_on: date | None = None
    reminders_enabled: bool = False


@dataclass(frozen=True, slots=True)
class MaintenanceDecision:
    maintenance_version: str
    plan_date: date
    kind_key: str
    label: str
    domain: str
    description: str
    status: MaintenanceStatus
    reason: MaintenanceReason
    tracked: bool
    reminders_enabled: bool
    #: The customer's declared rhythm, or ``None`` when they have not set one.
    interval_days: int | None
    #: The catalogue preset, offered as a starting point and nothing more.
    suggested_interval_days: int
    #: Derived from the declared rhythm, so a short rhythm gets a short window.
    lead_days: int | None
    last_done_on: date | None
    next_due_on: date | None
    days_until_due: int | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind_key,
            "label": self.label,
            "domain": self.domain,
            "description": self.description,
            "status": self.status.value,
            "reason": self.reason.value,
            "tracked": self.tracked,
            "reminders_enabled": self.reminders_enabled,
            "interval_days": self.interval_days,
            "suggested_interval_days": self.suggested_interval_days,
            "lead_days": self.lead_days,
            "last_done_on": self.last_done_on.isoformat() if self.last_done_on else None,
            "next_due_on": self.next_due_on.isoformat() if self.next_due_on else None,
            "days_until_due": self.days_until_due,
        }


@dataclass(frozen=True, slots=True)
class MaintenanceSet:
    maintenance_version: str
    catalogue_version: str
    plan_date: date
    decisions: tuple[MaintenanceDecision, ...]

    @property
    def due(self) -> tuple[MaintenanceDecision, ...]:
        """Tracked kinds that have reached their interval, soonest-due first."""
        return tuple(
            sorted(
                (row for row in self.decisions if row.status is MaintenanceStatus.DUE),
                key=lambda row: (row.days_until_due if row.days_until_due is not None else 0, row.kind_key),
            )
        )

    @property
    def coming_up(self) -> tuple[MaintenanceDecision, ...]:
        return tuple(
            sorted(
                (row for row in self.decisions if row.status is MaintenanceStatus.COMING_UP),
                key=lambda row: (row.days_until_due if row.days_until_due is not None else 0, row.kind_key),
            )
        )

    @property
    def needs_cadence(self) -> tuple[MaintenanceDecision, ...]:
        return tuple(row for row in self.decisions if row.status is MaintenanceStatus.NEEDS_CADENCE)

    @property
    def incomplete(self) -> tuple[MaintenanceDecision, ...]:
        """Tracked kinds still missing a fact, so no schedule exists for them."""
        return tuple(row for row in self.decisions if row.status in INCOMPLETE_STATUSES)

    @property
    def needs_anchor(self) -> tuple[MaintenanceDecision, ...]:
        return tuple(row for row in self.decisions if row.status is MaintenanceStatus.NEEDS_ANCHOR)

    def tracked_decisions(self) -> tuple[MaintenanceDecision, ...]:
        return tuple(row for row in self.decisions if row.tracked)

    def as_payload(self) -> dict[str, Any]:
        return {
            "version": self.maintenance_version,
            "catalogue_version": self.catalogue_version,
            "plan_date": self.plan_date.isoformat(),
            "kinds": [row.as_payload() for row in self.decisions],
        }


def clamp_interval(value: int | None) -> int | None:
    """Return a customer interval only when it is inside the allowed range.

    Out-of-range values are rejected by the API rather than silently squashed;
    this helper exists so the engine can never be handed an absurd rhythm from
    an older row.
    """
    if value is None:
        return None
    if value < MIN_INTERVAL_DAYS or value > MAX_INTERVAL_DAYS:
        return None
    return value


def decide_kind(
    kind: MaintenanceKind, state: MaintenanceState | None, *, plan_date: date,
) -> MaintenanceDecision:
    """Decide one kind's timing for one local day."""
    state = state or MaintenanceState(kind_key=kind.key)
    # The customer's declared rhythm is the only authority. The catalogue value
    # travels alongside as a suggestion they can accept, and is never applied
    # on their behalf.
    declared = clamp_interval(state.interval_days)

    def build(
        status: MaintenanceStatus, reason: MaintenanceReason,
        *, next_due_on: date | None = None, days_until_due: int | None = None,
    ) -> MaintenanceDecision:
        return MaintenanceDecision(
            maintenance_version=MAINTENANCE_VERSION,
            plan_date=plan_date,
            kind_key=kind.key,
            label=kind.label,
            domain=kind.domain.value,
            description=kind.description,
            status=status,
            reason=reason,
            tracked=state.tracked,
            reminders_enabled=state.tracked and state.reminders_enabled,
            interval_days=declared,
            suggested_interval_days=kind.suggested_interval_days,
            lead_days=lead_days_for(declared) if declared is not None else None,
            last_done_on=state.last_done_on,
            next_due_on=next_due_on,
            days_until_due=days_until_due,
        )

    if not state.tracked:
        return build(MaintenanceStatus.NOT_TRACKED, MaintenanceReason.NOT_TRACKED)
    if declared is None:
        # Tracking is not a schedule. Applying the catalogue preset here would
        # quietly turn our suggestion into their declared rhythm.
        return build(MaintenanceStatus.NEEDS_CADENCE, MaintenanceReason.NO_CADENCE_SET)
    if state.last_done_on is None:
        # No anchor means no schedule. Saying so is honest; picking today for
        # them would fabricate a starting point and then quietly act on it.
        return build(MaintenanceStatus.NEEDS_ANCHOR, MaintenanceReason.NO_RECORDED_DATE)

    next_due_on = state.last_done_on + timedelta(days=declared)
    days_until_due = (next_due_on - plan_date).days
    if days_until_due <= 0:
        return build(
            MaintenanceStatus.DUE, MaintenanceReason.INTERVAL_ELAPSED,
            next_due_on=next_due_on, days_until_due=days_until_due,
        )
    if days_until_due <= lead_days_for(declared):
        return build(
            MaintenanceStatus.COMING_UP, MaintenanceReason.INTERVAL_APPROACHING,
            next_due_on=next_due_on, days_until_due=days_until_due,
        )
    return build(
        MaintenanceStatus.NOT_DUE, MaintenanceReason.INTERVAL_NOT_ELAPSED,
        next_due_on=next_due_on, days_until_due=days_until_due,
    )


def decide_maintenance(
    states: dict[str, MaintenanceState] | None, *, plan_date: date,
) -> MaintenanceSet:
    """Decide every catalogue kind for one local day, in catalogue order."""
    states = states or {}
    return MaintenanceSet(
        maintenance_version=MAINTENANCE_VERSION,
        catalogue_version=MAINTENANCE_CATALOGUE_VERSION,
        plan_date=plan_date,
        decisions=tuple(
            decide_kind(kind, states.get(kind.key), plan_date=plan_date)
            for kind in MAINTENANCE_KINDS
        ),
    )


def reminder_eligible(decided: MaintenanceSet) -> tuple[MaintenanceDecision, ...]:
    """Due kinds the customer has explicitly opted into reminders for.

    Notification eligibility is decided here, from canonical maintenance state,
    so no caller can infer consent from the module being present in a plan.
    """
    return tuple(row for row in decided.due if row.reminders_enabled)


def maintenance_fingerprint(decided: MaintenanceSet) -> str:
    """Stable identity for the maintenance material behind a plan.

    Only tracked kinds contribute: an untracked kind has no bearing on the
    day, so toggling one off must not invalidate an otherwise identical plan.
    """
    material = [
        {
            "kind": row.kind_key,
            "status": row.status.value,
            "reason": row.reason.value,
            "interval_days": row.interval_days,
            "last_done_on": row.last_done_on.isoformat() if row.last_done_on else None,
            "next_due_on": row.next_due_on.isoformat() if row.next_due_on else None,
            "reminders_enabled": row.reminders_enabled,
        }
        for row in decided.tracked_decisions()
    ]
    payload = {
        "maintenance_version": decided.maintenance_version,
        "catalogue_version": decided.catalogue_version,
        "kinds": material,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def due_by_event_date(decided: MaintenanceSet, event_date: date) -> tuple[MaintenanceDecision, ...]:
    """Tracked kinds whose next date falls on or before an event.

    Used by Event Ready so preparation timing reuses this one authority rather
    than recomputing a second, divergent schedule.
    """
    rows = [
        row for row in decided.tracked_decisions()
        if row.next_due_on is not None and row.next_due_on <= event_date
    ]
    return tuple(sorted(rows, key=lambda row: (row.next_due_on or event_date, row.kind_key)))


__all__ = [
    "INCOMPLETE_STATUSES",
    "MaintenanceDecision",
    "MaintenanceReason",
    "MaintenanceSet",
    "MaintenanceState",
    "MaintenanceStatus",
    "clamp_interval",
    "decide_kind",
    "decide_maintenance",
    "due_by_event_date",
    "maintenance_fingerprint",
    "reminder_eligible",
]
