"""Turning the environment into one decision.

The engine is pure: it takes today's readings plus a short history of stored
air-quality days and returns **one primary decision and at most one supporting
note**. That cap is the point. On a Delhi morning in November, four of the ten
rules can be true at once, and a person told four environmental things in one
day stops reading them.

Nothing here talks to a database, a provider or an AI. ``EnvironmentWindow`` is
assembled from data the planning domain already stores.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domains.care.environment_rules import (
    CARE_ENVIRONMENT_RULESET_VERSION,
    ENVIRONMENT_RULES,
    CareEnvironmentRule,
    EnvironmentAction,
)
from app.domains.planning.environment import (
    HUMIDITY_DRY_AT_OR_BELOW,
    HUMIDITY_HUMID_AT_OR_ABOVE,
    NAQI_INDEX_SYSTEM,
    naqi_at_least,
    naqi_at_most,
)

CARE_ENVIRONMENT_DECISION_VERSION = "v3-06"

#: A UV Index of 3 is the published threshold at which sun protection is
#: advised (WHO). Above 8 the guidance is the same, only more insistent; we use
#: the one published threshold rather than inventing a second one.
UV_PROTECTION_THRESHOLD = 3.0
#: "High temperature" for the occlusion rule. Matches the existing
#: ``temperature_band`` boundary between warm and hot rather than adding a
#: third opinion about what hot means.
OCCLUSION_TEMP_AT_OR_ABOVE_C = 30.0
#: Consecutive Poor-or-worse days before antioxidant support is suggested.
SUSTAINED_POOR_DAYS = 5
#: Consecutive clean days before deferred actives resume.
CLEAN_DAYS_TO_RESUME = 2
#: Days at Poor or worse that make rain a recovery window rather than weather.
RAIN_RECOVERY_AFTER_POOR_DAYS = 3
#: Rain is "significant" at this precipitation chance, matching the existing
#: wet-regime threshold in planning.environment.
RAIN_SIGNIFICANT_AT_OR_ABOVE = 60

#: What is deferred, named the way a person would name it. Never "actives".
DEFERRED_LABEL = "Exfoliation and retinoids"


@dataclass(frozen=True, slots=True)
class EnvironmentDay:
    """One stored day, reduced to what the rules actually read."""

    for_date: date
    aqi: int | None = None
    index_system: str | None = None
    category: str | None = None
    humidity: int | None = None
    temp_max_c: float | None = None
    uv_index: float | None = None
    precipitation_chance: int | None = None
    condition: str | None = None

    @property
    def is_indian_reading(self) -> bool:
        """Only an Indian reading may drive an Indian category decision."""
        return self.index_system == NAQI_INDEX_SYSTEM and self.category is not None


@dataclass(frozen=True, slots=True)
class EnvironmentWindow:
    """Today, plus the recent days the streak rules need.

    ``history`` is ordered oldest-first and excludes today.
    """

    today: EnvironmentDay
    history: tuple[EnvironmentDay, ...] = ()

    def _recent(self) -> tuple[EnvironmentDay, ...]:
        """History then today, newest last, gaps left as gaps."""
        return (*self.history, self.today)

    def consecutive_at_least(self, threshold: str) -> int:
        """Days ending today that were ``threshold`` or worse, counting back.

        A day with no Indian reading breaks the run rather than being assumed
        clean or assumed dirty. An unknown day is unknown.
        """
        streak = 0
        for day in reversed(self._recent()):
            if not day.is_indian_reading or not naqi_at_least(day.category, threshold):
                break
            streak += 1
        return streak

    def consecutive_at_most(self, threshold: str) -> int:
        """Days ending today that were ``threshold`` or better, counting back."""
        streak = 0
        for day in reversed(self._recent()):
            if not day.is_indian_reading or not naqi_at_most(day.category, threshold):
                break
            streak += 1
        return streak

    def poor_run_before_clean_days(self, clean_days: int) -> int:
        """How long the Poor-or-worse run was, immediately before the clean run."""
        days = list(reversed(self._recent()))[clean_days:]
        streak = 0
        for day in days:
            if not day.is_indian_reading or not naqi_at_least(day.category, "Poor"):
                break
            streak += 1
        return streak


@dataclass(frozen=True, slots=True)
class EnvironmentDecision:
    """One decision, its reason, and at most one supporting note."""

    decision_version: str
    ruleset_version: str
    rule_id: str
    action: EnvironmentAction
    headline: str
    reason: str
    note: str | None = None
    note_rule_id: str | None = None
    #: Every rule that was true today, in precedence order. Recorded so the
    #: decision is auditable without being shown.
    fired_rule_ids: tuple[str, ...] = ()
    aqi: int | None = None
    aqi_category: str | None = None
    evidence_claim_ids: tuple[uuid.UUID, ...] = field(default=())

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision_version": self.decision_version,
            "ruleset_version": self.ruleset_version,
            "rule_id": self.rule_id,
            "action": self.action.value,
            "headline": self.headline,
            "reason": self.reason,
            "note": self.note,
            "note_rule_id": self.note_rule_id,
            "fired_rule_ids": list(self.fired_rule_ids),
            "aqi": self.aqi,
            "aqi_category": self.aqi_category,
            "evidence_claim_ids": [str(value) for value in self.evidence_claim_ids],
        }


def _facts(window: EnvironmentWindow) -> dict[str, Any]:
    today = window.today
    return {
        "aqi": today.aqi,
        "category": today.category,
        "category_lower": (today.category or "unknown").lower(),
        "humidity": today.humidity,
        "temp_max_c": (
            int(today.temp_max_c) if today.temp_max_c is not None and float(today.temp_max_c).is_integer()
            else today.temp_max_c
        ),
        "uv_index": (
            int(today.uv_index) if today.uv_index is not None and float(today.uv_index).is_integer()
            else today.uv_index
        ),
        "deferred_label": DEFERRED_LABEL,
        "deferred_label_lower": DEFERRED_LABEL[0].lower() + DEFERRED_LABEL[1:],
        "streak_days": 0,
        "clean_days": 0,
    }


def _is_wet(day: EnvironmentDay) -> bool:
    return day.condition == "rainy" or (
        day.precipitation_chance is not None
        and day.precipitation_chance >= RAIN_SIGNIFICANT_AT_OR_ABOVE
    )


def _fires(rule: CareEnvironmentRule, window: EnvironmentWindow, facts: dict[str, Any]) -> bool:
    """Whether one rule is true today. Facts are filled in as a side effect."""
    today = window.today
    air = today.is_indian_reading
    category = today.category if air else None

    if rule.rule_id == "care.env.dry_air_and_poor_naqi":
        return (
            air
            and naqi_at_least(category, "Poor")
            and today.humidity is not None
            and today.humidity <= HUMIDITY_DRY_AT_OR_BELOW
        )

    if rule.rule_id == "care.env.very_poor_post_exposure_cleanse":
        return air and naqi_at_least(category, "Very Poor")

    if rule.rule_id == "care.env.poor_defer_strong_actives":
        return air and naqi_at_least(category, "Poor")

    if rule.rule_id == "care.env.high_uv_photosensitivity":
        return today.uv_index is not None and today.uv_index >= UV_PROTECTION_THRESHOLD

    if rule.rule_id == "care.env.very_poor_hair_hold_cadence":
        return air and naqi_at_least(category, "Very Poor")

    if rule.rule_id == "care.env.humid_heat_occlusion":
        return (
            today.humidity is not None
            and today.humidity >= HUMIDITY_HUMID_AT_OR_ABOVE
            and today.temp_max_c is not None
            and today.temp_max_c >= OCCLUSION_TEMP_AT_OR_ABOVE_C
        )

    if rule.rule_id == "care.env.sustained_poor_antioxidant_am":
        streak = window.consecutive_at_least("Poor")
        facts["streak_days"] = streak
        return streak >= SUSTAINED_POOR_DAYS

    if rule.rule_id == "care.env.resume_needs_two_clean_days":
        clean = window.consecutive_at_most("Satisfactory")
        if clean != 1:
            return False
        prior_poor = window.poor_run_before_clean_days(clean)
        facts["clean_days"] = clean
        facts["streak_days"] = prior_poor
        return prior_poor >= 1

    if rule.rule_id == "care.env.air_cleared_resume_actives":
        clean = window.consecutive_at_most("Satisfactory")
        if clean < CLEAN_DAYS_TO_RESUME:
            return False
        # Only worth saying when something was actually taken away.
        prior_poor = window.poor_run_before_clean_days(clean)
        facts["clean_days"] = clean
        return prior_poor >= 1

    if rule.rule_id == "care.env.rain_recovery_window":
        if not _is_wet(today):
            return False
        # The rain is today; the poor stretch is the days before it.
        prior = EnvironmentWindow(
            today=window.history[-1], history=window.history[:-1]
        ) if window.history else None
        streak = prior.consecutive_at_least("Poor") if prior is not None else 0
        facts["streak_days"] = streak
        return streak >= RAIN_RECOVERY_AFTER_POOR_DAYS

    raise ValueError(f"unknown environment rule {rule.rule_id}")


def evaluate_environment(
    window: EnvironmentWindow,
    *,
    allowed_rule_ids: frozenset[str] | None = None,
) -> EnvironmentDecision | None:
    """Resolve the day to one decision, or to nothing at all.

    ``allowed_rule_ids`` is the evidence gate: a rule with no reviewed source
    behind it never speaks. Passing ``None`` means "no gate", which is only for
    unit tests of the rule logic itself.

    Returns ``None`` when no rule fires. A quiet day should be quiet.
    """
    facts = _facts(window)
    fired: list[CareEnvironmentRule] = []
    for rule in sorted(ENVIRONMENT_RULES, key=lambda row: row.precedence):
        if allowed_rule_ids is not None and rule.rule_id not in allowed_rule_ids:
            continue
        if _fires(rule, window, facts):
            fired.append(rule)
    if not fired:
        return None

    primary = fired[0]
    note_rule = _supporting_note(primary, fired[1:])
    return EnvironmentDecision(
        decision_version=CARE_ENVIRONMENT_DECISION_VERSION,
        ruleset_version=CARE_ENVIRONMENT_RULESET_VERSION,
        rule_id=primary.rule_id,
        action=primary.action,
        headline=primary.headline.format(**facts),
        reason=primary.reason_template.format(**facts),
        note=note_rule.note.format(**facts) if note_rule is not None else None,
        note_rule_id=note_rule.rule_id if note_rule is not None else None,
        fired_rule_ids=tuple(rule.rule_id for rule in fired),
        aqi=window.today.aqi,
        aqi_category=window.today.category,
    )


#: Rules whose note would only repeat what the primary decision already said.
#: Keyed by primary rule, valued by the notes it makes redundant.
_REDUNDANT_NOTES: dict[str, frozenset[str]] = {
    "care.env.dry_air_and_poor_naqi": frozenset({
        "care.env.poor_defer_strong_actives",
        "care.env.humid_heat_occlusion",
    }),
    "care.env.poor_defer_strong_actives": frozenset({
        "care.env.resume_needs_two_clean_days",
    }),
    "care.env.air_cleared_resume_actives": frozenset({
        "care.env.rain_recovery_window",
    }),
}


def _supporting_note(
    primary: CareEnvironmentRule, remaining: list[CareEnvironmentRule]
) -> CareEnvironmentRule | None:
    """The one note worth adding, or none.

    Preference goes to a rule that gives something back: when a day both
    restricts and restores, the restoration is the half a person will not work
    out on their own.
    """
    redundant = _REDUNDANT_NOTES.get(primary.rule_id, frozenset())
    eligible = [rule for rule in remaining if rule.rule_id not in redundant]
    if not eligible:
        return None
    restores = [rule for rule in eligible if rule.action is EnvironmentAction.RESTORE]
    if restores and primary.action is not EnvironmentAction.RESTORE:
        return restores[0]
    return eligible[0]


__all__ = [
    "CARE_ENVIRONMENT_DECISION_VERSION",
    "EnvironmentDay",
    "EnvironmentDecision",
    "EnvironmentWindow",
    "evaluate_environment",
]
