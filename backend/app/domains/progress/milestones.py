"""Milestones, and the rules about what may earn one.

The brief is blunt about this and so is the code: reward useful behaviour, do
not reward opening the app, and use mature wording.

Three things enforce that rather than merely intending it.

**A closed list of behaviours.** :data:`REWARDABLE_BEHAVIOURS` is every action
that can produce a gamification event. It contains no engagement metric —
no app opens, no sessions, no time spent, no logins — and
:func:`validate_rules` rejects any rule naming a behaviour outside the list.

**A banned-word sweep on the copy.** Milestone labels are checked against
:data:`FORBIDDEN_WORDS`, which covers both the childish register the brief warns
about ("badge", "level up", "congratulations!") and the shaming register
("finally", "at last"). Reaching a milestone is a fact, stated plainly.

**Deduplication in the database, not in code.** Every gamification event carries
a hash of the real-world thing that happened, under a unique constraint. Logging
the same action twenty times counts once, so the reward cannot be farmed.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

MILESTONES_VERSION = "phase7-v1"

# Every behaviour that may earn anything. Adding to this list is a deliberate
# product decision; note there is nothing here about opening the app, session
# length, logging in, or coming back.
BEHAVIOUR_USED_LOW_USE_PRODUCT = "used_low_use_product"
BEHAVIOUR_COMPLETED_ROUTINE = "completed_routine"
BEHAVIOUR_WORE_DIVERSE_OUTFIT = "wore_diverse_outfit"
BEHAVIOUR_PREPARED_FOR_OCCASION = "prepared_for_occasion"
BEHAVIOUR_AVOIDED_DUPLICATE = "avoided_duplicate_purchase"
BEHAVIOUR_NO_BUY_MAINTAINED = "no_buy_maintained"
BEHAVIOUR_USED_BEFORE_EXPIRY = "used_product_before_expiry"
BEHAVIOUR_ORGANISED_INVENTORY = "organised_inventory"
BEHAVIOUR_REACHED_OWN_GOAL = "reached_own_goal"

REWARDABLE_BEHAVIOURS: tuple[str, ...] = (
    BEHAVIOUR_USED_LOW_USE_PRODUCT,
    BEHAVIOUR_COMPLETED_ROUTINE,
    BEHAVIOUR_WORE_DIVERSE_OUTFIT,
    BEHAVIOUR_PREPARED_FOR_OCCASION,
    BEHAVIOUR_AVOIDED_DUPLICATE,
    BEHAVIOUR_NO_BUY_MAINTAINED,
    BEHAVIOUR_USED_BEFORE_EXPIRY,
    BEHAVIOUR_ORGANISED_INVENTORY,
    BEHAVIOUR_REACHED_OWN_GOAL,
)

# Things that must never become rewardable. Kept explicitly rather than left
# implicit, so an attempt to add one fails loudly with an explanation.
NEVER_REWARDABLE: tuple[str, ...] = (
    "app_opened", "session_started", "logged_in", "returned_to_app",
    "time_in_app", "screens_viewed", "notification_opened", "streak_of_opens",
    "daily_login", "invited_a_friend",
)

# Wording that would make this childish, or make it shame somebody.
FORBIDDEN_WORDS: tuple[str, ...] = (
    "badge", "trophy", "level up", "levelled up", "unlocked!", "congratulations",
    "well done!", "champion", "superstar", "streak master", "points", "xp",
    "finally", "at last", "about time", "you failed", "you lost",
)


@dataclass(frozen=True)
class MilestoneRule:
    rule_id: str
    label: str
    description: str
    behaviour: str
    threshold: int
    repeatable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "label": self.label, "description": self.description,
            "behaviour": self.behaviour, "threshold": self.threshold,
            "repeatable": self.repeatable,
        }


# Wording is deliberately flat. A milestone states what happened; it does not
# cheer. "Five products brought back into use" reads like a fact, which is what
# it is.
RULES: tuple[MilestoneRule, ...] = (
    MilestoneRule(
        "milestone.used_five_low_use", "Five unused products back in use",
        "You used five products that had been sitting untouched.",
        BEHAVIOUR_USED_LOW_USE_PRODUCT, 5, repeatable=True,
    ),
    MilestoneRule(
        "milestone.routine_ten_days", "Ten days of your routine",
        "You completed at least one routine step on ten separate days.",
        BEHAVIOUR_COMPLETED_ROUTINE, 10, repeatable=True,
    ),
    MilestoneRule(
        "milestone.routine_thirty_days", "Thirty days of your routine",
        "You completed at least one routine step on thirty separate days.",
        BEHAVIOUR_COMPLETED_ROUTINE, 30, repeatable=True,
    ),
    MilestoneRule(
        "milestone.ten_distinct_outfits", "Ten different outfits worn",
        "You wore ten distinct combinations from what you own.",
        BEHAVIOUR_WORE_DIVERSE_OUTFIT, 10, repeatable=True,
    ),
    MilestoneRule(
        "milestone.prepared_three_occasions", "Three occasions planned ahead",
        "You had an outfit ready before the day for three occasions.",
        BEHAVIOUR_PREPARED_FOR_OCCASION, 3, repeatable=True,
    ),
    MilestoneRule(
        "milestone.avoided_duplicate", "A duplicate purchase avoided",
        "You checked before buying and already owned something that did the job.",
        BEHAVIOUR_AVOIDED_DUPLICATE, 1, repeatable=True,
    ),
    MilestoneRule(
        "milestone.no_buy_thirty", "Thirty days into your no-buy",
        "Thirty days since you started a no-buy period, with nothing added.",
        BEHAVIOUR_NO_BUY_MAINTAINED, 30,
    ),
    MilestoneRule(
        "milestone.no_buy_ninety", "Ninety days into your no-buy",
        "Ninety days since you started a no-buy period, with nothing added.",
        BEHAVIOUR_NO_BUY_MAINTAINED, 90,
    ),
    MilestoneRule(
        "milestone.finished_before_expiry", "A product finished before its date",
        "You used something up rather than letting it go past its date.",
        BEHAVIOUR_USED_BEFORE_EXPIRY, 1, repeatable=True,
    ),
    MilestoneRule(
        "milestone.catalogued_twenty", "Twenty items catalogued",
        "Twenty things recorded, which is enough for the suggestions to get useful.",
        BEHAVIOUR_ORGANISED_INVENTORY, 20,
    ),
    MilestoneRule(
        "milestone.catalogued_fifty", "Fifty items catalogued",
        "Fifty things recorded.",
        BEHAVIOUR_ORGANISED_INVENTORY, 50,
    ),
    MilestoneRule(
        "milestone.reached_a_goal", "A goal you set yourself, reached",
        "You reached a target you set for yourself.",
        BEHAVIOUR_REACHED_OWN_GOAL, 1, repeatable=True,
    ),
)

RULE_BY_ID: dict[str, MilestoneRule] = {row.rule_id: row for row in RULES}


def validate_rules() -> None:
    """Assert the rules obey the phase's own constraints. Called at import."""
    seen = set()
    for rule in RULES:
        if rule.rule_id in seen:
            raise AssertionError(f"Duplicate milestone rule: {rule.rule_id}")
        seen.add(rule.rule_id)

        if rule.behaviour in NEVER_REWARDABLE:
            raise AssertionError(
                f"Milestone '{rule.rule_id}' rewards '{rule.behaviour}', which is engagement, "
                "not useful behaviour. That is exactly what this phase must not do."
            )
        if rule.behaviour not in REWARDABLE_BEHAVIOURS:
            raise AssertionError(
                f"Milestone '{rule.rule_id}' names unknown behaviour '{rule.behaviour}'."
            )
        if rule.threshold < 1:
            raise AssertionError(f"Milestone '{rule.rule_id}' has a meaningless threshold.")

        text = f"{rule.label} {rule.description}".lower()
        for word in FORBIDDEN_WORDS:
            if word in text:
                raise AssertionError(
                    f"Milestone '{rule.rule_id}' uses wording this product does not use: {word!r}"
                )


validate_rules()


def dedup_hash(
    account_id: uuid.UUID, behaviour: str, occurred_on: date, subject_id: uuid.UUID | None
) -> str:
    """Identity of one real-world action.

    Includes the subject, so wearing two different unused products on the same
    day is two events, but logging the same one twice is one.
    """
    payload = json.dumps({
        "account": str(account_id), "behaviour": behaviour,
        "occurred_on": occurred_on.isoformat(),
        "subject": str(subject_id) if subject_id else None,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rules_for(behaviour: str) -> list[MilestoneRule]:
    return [row for row in RULES if row.behaviour == behaviour]


def earned(behaviour: str, count: int, already_earned: Sequence[str] = ()) -> list[MilestoneRule]:
    """Which rules this count has just satisfied.

    A non-repeatable rule earned before is not earned again. A repeatable one
    fires each time the count crosses a fresh multiple of its threshold, so
    "five unused products back in use" can happen more than once without
    firing on every single event.
    """
    out: list[MilestoneRule] = []
    for rule in rules_for(behaviour):
        if count < rule.threshold:
            continue
        if rule.rule_id in already_earned and not rule.repeatable:
            continue
        if rule.repeatable and count % rule.threshold != 0:
            continue
        out.append(rule)
    return out


def all_rules() -> list[dict[str, Any]]:
    return [row.as_dict() for row in RULES]
