"""What is on sale, and what each thing gets you.

Three commercial products: Free, an Event Pass, and Plus. The shape of each is
defined here; **the prices are not**. Every amount comes from configuration
(``app.config``), because the brief is explicit that final prices must not be
hard-coded, and because a price is a business decision that should never need a
deploy.

The line this file draws, and the one the whole phase is built around:

> Do not monetise unlimited generic AI calls.

So there is no "tokens" entitlement, no "AI calls" entitlement, and no plan
whose headline is a volume of model usage. What is metered is a **decision** —
styling an occasion, evaluating a purchase, planning a week — because that is
the thing that has value to a person. :data:`FORBIDDEN_BENEFIT_WORDS` is swept
over every benefit string at import, so a future edit that reintroduces
"unlimited AI" fails loudly rather than shipping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.config import (
    EVENT_PASS_PRICE_INR, EVENT_PASS_VALID_DAYS, PLUS_MONTHLY_INR, PLUS_YEARLY_INR,
)

CATALOGUE_VERSION = "phase8-v1"

PLAN_FREE = "free"
PLAN_EVENT_PASS = "event_pass"
PLAN_PLUS = "plus"
PLAN_KEYS: Tuple[str, ...] = (PLAN_FREE, PLAN_EVENT_PASS, PLAN_PLUS)

# What can be metered. Every one of these is a *decision or a stored thing*,
# never a quantity of model usage.
FEATURE_INVENTORY_ITEMS = "inventory_items"
FEATURE_STYLE_DECISIONS = "style_decisions"
FEATURE_SHOPPING_EVALUATIONS = "shopping_evaluations"
FEATURE_SAVED_LOOKS = "saved_looks"
FEATURE_EVENT_LOOKS = "event_looks"
FEATURE_EVENT_REVISIONS = "event_revisions"
FEATURE_TODAY_ENGINE = "today_engine"
FEATURE_WEEKLY_PLANNER = "weekly_planner"
FEATURE_ROUTINE_INTELLIGENCE = "routine_intelligence"
FEATURE_SHELF_INTELLIGENCE = "shelf_intelligence"
FEATURE_FULL_PROGRESS = "full_progress"
FEATURE_LONG_TERM_MEMORY = "long_term_memory"
FEATURE_PACKING = "packing_and_occasion_prep"
FEATURE_LOOKBOARD = "shareable_lookboard"

METERED_FEATURES: Tuple[str, ...] = (
    FEATURE_INVENTORY_ITEMS, FEATURE_STYLE_DECISIONS, FEATURE_SHOPPING_EVALUATIONS,
    FEATURE_SAVED_LOOKS, FEATURE_EVENT_LOOKS, FEATURE_EVENT_REVISIONS,
)

# Features that are simply on or off for a plan.
BOOLEAN_FEATURES: Tuple[str, ...] = (
    FEATURE_TODAY_ENGINE, FEATURE_WEEKLY_PLANNER, FEATURE_ROUTINE_INTELLIGENCE,
    FEATURE_SHELF_INTELLIGENCE, FEATURE_FULL_PROGRESS, FEATURE_LONG_TERM_MEMORY,
    FEATURE_PACKING, FEATURE_LOOKBOARD,
)

ALL_FEATURES: Tuple[str, ...] = METERED_FEATURES + BOOLEAN_FEATURES

FEATURE_LABELS: Dict[str, str] = {
    FEATURE_INVENTORY_ITEMS: "Things you can catalogue",
    FEATURE_STYLE_DECISIONS: "Occasions we style for you",
    FEATURE_SHOPPING_EVALUATIONS: '"Should I buy this?" checks',
    FEATURE_SAVED_LOOKS: "Looks you can save",
    FEATURE_EVENT_LOOKS: "Complete looks for your event",
    FEATURE_EVENT_REVISIONS: "Changes to those looks",
    FEATURE_TODAY_ENGINE: "One plan for today",
    FEATURE_WEEKLY_PLANNER: "The Monday-to-Sunday planner",
    FEATURE_ROUTINE_INTELLIGENCE: "Beauty and hair routines from what you own",
    FEATURE_SHELF_INTELLIGENCE: "Full shelf intelligence",
    FEATURE_FULL_PROGRESS: "Your complete progress history",
    FEATURE_LONG_TERM_MEMORY: "Long-term memory of what suits you",
    FEATURE_PACKING: "Packing and event preparation",
    FEATURE_LOOKBOARD: "A shareable lookboard",
}

# Wording that would mean we had started selling model usage rather than
# outcomes. Swept over every benefit line at import.
FORBIDDEN_BENEFIT_WORDS: Tuple[str, ...] = (
    "unlimited ai", "unlimited scans", "unlimited generations", "unlimited requests",
    "more tokens", "tokens", "api calls", "ai calls", "gpu", "credits per month",
    "unlimited photos", "unlimited analysis",
)


@dataclass(frozen=True)
class Benefit:
    """One line on the paywall. An outcome, never a volume."""

    headline: str
    detail: str


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    tagline: str
    # None means the feature is unmetered *for this plan*; absent means not
    # included at all. A metered feature with a number is a hard cap.
    limits: Dict[str, Optional[int]]
    benefits: List[Benefit]
    # Set for one-off products like the Event Pass.
    valid_days: Optional[int] = None
    recurring: bool = False

    def includes(self, feature: str) -> bool:
        return feature in self.limits

    def limit_for(self, feature: str) -> Optional[int]:
        return self.limits.get(feature)


# --- Free ---------------------------------------------------------------------
# Deliberately usable on its own. The Digital Twin, a real wardrobe, one full
# event result and basic routines are all here — somebody who never pays should
# still have a working app, not a demo.

FREE = Plan(
    key=PLAN_FREE,
    label="Free",
    tagline="Your appearance profile, your wardrobe, and a real answer when it matters.",
    limits={
        FEATURE_INVENTORY_ITEMS: 40,
        FEATURE_STYLE_DECISIONS: 1,
        FEATURE_SHOPPING_EVALUATIONS: 3,
        FEATURE_SAVED_LOOKS: 5,
        FEATURE_ROUTINE_INTELLIGENCE: None,
    },
    benefits=[
        Benefit("Your appearance profile", "Skin tone, undertone, fit and style preferences, kept and confirmed by you."),
        Benefit("Catalogue up to 40 things", "Enough for a working wardrobe and a shelf."),
        Benefit("One occasion styled in full", "A complete look from what you own, with the reasoning."),
        Benefit("Three shopping checks", "Ask whether something is worth buying before you buy it."),
        Benefit("Basic routines", "A morning and evening routine built from products you already have."),
    ],
)

# --- Event Pass ---------------------------------------------------------------
# One occasion, done properly. Bought for a wedding on Saturday, not as a
# subscription somebody forgets to cancel.

EVENT_PASS = Plan(
    key=PLAN_EVENT_PASS,
    label="Event Pass",
    tagline="One occasion that matters, prepared properly.",
    valid_days=EVENT_PASS_VALID_DAYS,
    limits={
        FEATURE_EVENT_LOOKS: 3,
        FEATURE_EVENT_REVISIONS: 6,
        FEATURE_SHOPPING_EVALUATIONS: 5,
        FEATURE_SAVED_LOOKS: 10,
        FEATURE_LOOKBOARD: None,
        FEATURE_PACKING: None,
    },
    benefits=[
        Benefit("Three complete looks", "Not variations on one idea — three genuinely different options from your wardrobe."),
        Benefit("Change your mind", "Six revisions, so you can rule things out without starting again."),
        Benefit("A preparation timeline", "What to do the week before, the night before, and on the day."),
        Benefit("What is actually missing", "The gap between what the occasion needs and what you own — and whether it is worth buying."),
        Benefit("A lookboard you can share", "Send it to whoever you are getting ready with."),
    ],
)

# --- Plus ---------------------------------------------------------------------
# The recurring product. Every headline is a thing the person gets done.

PLUS = Plan(
    key=PLAN_PLUS,
    label="Plus",
    tagline="Every week planned, and your whole wardrobe actually working.",
    recurring=True,
    limits={
        FEATURE_INVENTORY_ITEMS: 600,
        FEATURE_STYLE_DECISIONS: 30,
        FEATURE_SHOPPING_EVALUATIONS: 40,
        FEATURE_SAVED_LOOKS: 200,
        FEATURE_TODAY_ENGINE: None,
        FEATURE_WEEKLY_PLANNER: None,
        FEATURE_ROUTINE_INTELLIGENCE: None,
        FEATURE_SHELF_INTELLIGENCE: None,
        FEATURE_FULL_PROGRESS: None,
        FEATURE_LONG_TERM_MEMORY: None,
        FEATURE_PACKING: None,
        FEATURE_LOOKBOARD: None,
    },
    benefits=[
        Benefit("Plan every week", "Monday to Sunday, around what is actually in your diary."),
        Benefit("Use your complete wardrobe", "Catalogue all of it, and get told what you already own before you buy again."),
        Benefit("Make better shopping decisions", "Forty checks a month on whether something earns its place."),
        Benefit("Prepare for important events", "Packing lists and event preparation, from what you own."),
        Benefit("Recover value from what you are not using", "Find the things sitting unused and put them back into rotation."),
        Benefit("Build routines using what you own", "Beauty and hair routines from your shelf, in the right order."),
        Benefit("Keep long-term memory", "It learns what suits you — and you can read, correct or delete any of it."),
    ],
)

PLANS: Tuple[Plan, ...] = (FREE, EVENT_PASS, PLUS)
PLAN_BY_KEY: Dict[str, Plan] = {row.key: row for row in PLANS}


# --- Offers -------------------------------------------------------------------
# An offer is a plan plus a price plus a billing period. Prices come from
# configuration; nothing here is a literal amount.


@dataclass(frozen=True)
class Offer:
    key: str
    plan_key: str
    label: str
    amount_inr: int
    period: str            # one_time | monthly | yearly
    currency: str = "INR"
    # Shown struck through next to the price where there is a genuine saving.
    compare_at_inr: Optional[int] = None

    @property
    def plan(self) -> Plan:
        return PLAN_BY_KEY[self.plan_key]

    def as_dict(self) -> Dict[str, object]:
        saving = None
        if self.compare_at_inr and self.compare_at_inr > self.amount_inr:
            saving = round(100 * (1 - self.amount_inr / self.compare_at_inr))
        return {
            "key": self.key, "plan_key": self.plan_key, "label": self.label,
            "amount_inr": self.amount_inr, "currency": self.currency,
            "period": self.period, "compare_at_inr": self.compare_at_inr,
            "saving_percent": saving,
            "valid_days": self.plan.valid_days,
            "tagline": self.plan.tagline,
            "benefits": [
                {"headline": row.headline, "detail": row.detail} for row in self.plan.benefits
            ],
        }


def offers() -> List[Offer]:
    """The offers currently on sale, priced from configuration.

    Built on each call rather than at import, so changing a price is a restart
    with a new environment variable — never a code change and never a deploy of
    new logic.
    """
    return [
        Offer(
            key="event_pass", plan_key=PLAN_EVENT_PASS,
            label="Event Pass", amount_inr=EVENT_PASS_PRICE_INR, period="one_time",
        ),
        Offer(
            key="plus_monthly", plan_key=PLAN_PLUS,
            label="Plus, monthly", amount_inr=PLUS_MONTHLY_INR, period="monthly",
        ),
        Offer(
            key="plus_yearly", plan_key=PLAN_PLUS,
            label="Plus, yearly", amount_inr=PLUS_YEARLY_INR, period="yearly",
            compare_at_inr=PLUS_MONTHLY_INR * 12,
        ),
    ]


def offer_by_key(key: str) -> Optional[Offer]:
    return next((row for row in offers() if row.key == key), None)


def validate_catalogue() -> None:
    """Assert the catalogue's own rules. Called at import.

    The important one is the benefit sweep. "Unlimited AI" on a paywall is the
    exact failure this phase is meant to avoid, and a comment asking people not
    to write it would not survive a year.
    """
    for plan in PLANS:
        for feature in plan.limits:
            if feature not in ALL_FEATURES:
                raise AssertionError(f"Plan '{plan.key}' names unknown feature '{feature}'.")
        for benefit in plan.benefits:
            text = f"{benefit.headline} {benefit.detail}".lower()
            for word in FORBIDDEN_BENEFIT_WORDS:
                if word in text:
                    raise AssertionError(
                        f"Plan '{plan.key}' sells '{word}'. This product monetises outcomes, "
                        "not model usage — see the Phase 8 brief."
                    )
        lowered = plan.tagline.lower()
        for word in FORBIDDEN_BENEFIT_WORDS:
            if word in lowered:
                raise AssertionError(f"Plan '{plan.key}' tagline sells '{word}'.")

    if not FREE.limits:
        raise AssertionError("Free must actually include something.")


validate_catalogue()


def entitlements_for(plan_key: str) -> Dict[str, Optional[int]]:
    plan = PLAN_BY_KEY.get(plan_key)
    return dict(plan.limits) if plan else {}


def as_dict() -> Dict[str, object]:
    return {
        "version": CATALOGUE_VERSION,
        "plans": [
            {
                "key": plan.key, "label": plan.label, "tagline": plan.tagline,
                "recurring": plan.recurring, "valid_days": plan.valid_days,
                "limits": {
                    key: {"limit": value, "label": FEATURE_LABELS.get(key, key)}
                    for key, value in plan.limits.items()
                },
                "benefits": [
                    {"headline": row.headline, "detail": row.detail} for row in plan.benefits
                ],
            }
            for plan in PLANS
        ],
    }
