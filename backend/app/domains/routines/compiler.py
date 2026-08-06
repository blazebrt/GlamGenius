"""The routine compiler.

Builds the five routines the brief asks for — morning, evening, wash day,
weekly and event preparation — out of products the user already owns.

Two things it will not do:

* **Invent a product.** A step with nothing owned to fill it becomes a gap with
  a plain description, never a brand or a link. Required steps only: suggesting
  something for every empty slot would make this a shop.
* **Warn without a rule.** Every warning attached to a routine comes from
  ``rules.py`` and carries its ``rule_id``.

Every step carries what the brief requires: the owned product, why the step
exists, its order, its frequency, whether it is optional, a safety note where
one applies, and what to do if the product is unavailable.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.domains.routines import rules as rules_engine
from app.domains.routines.ontology import (
    SLOT_BY_KEY,
)
from app.domains.routines.rules import Finding, ShelfProduct
from app.domains.routines.safety import ROUTINE_DISCLAIMER, narrative_is_safe

ROUTINE_MORNING = "morning"
ROUTINE_EVENING = "evening"
ROUTINE_WASH_DAY = "wash_day"
ROUTINE_WEEKLY = "weekly"
ROUTINE_EVENT = "event"
ROUTINE_KINDS = (ROUTINE_MORNING, ROUTINE_EVENING, ROUTINE_WASH_DAY, ROUTINE_WEEKLY, ROUTINE_EVENT)

ROUTINE_LABELS = {
    ROUTINE_MORNING: "Morning routine",
    ROUTINE_EVENING: "Evening routine",
    ROUTINE_WASH_DAY: "Wash day",
    ROUTINE_WEEKLY: "Weekly extras",
    ROUTINE_EVENT: "Before an event",
}

# Which slots belong to which routine, and how often each runs.
ROUTINE_SLOTS: dict[str, list[str]] = {
    ROUTINE_MORNING: ["cleanser", "toner", "treatment", "eye", "moisturiser", "sunscreen"],
    ROUTINE_EVENING: ["cleanser", "exfoliant", "toner", "treatment", "eye", "moisturiser", "face_oil"],
    ROUTINE_WASH_DAY: ["pre_wash_oil", "shampoo", "scalp_care", "conditioner", "leave_in", "heat_protectant", "styling"],
    ROUTINE_WEEKLY: ["exfoliant", "hair_mask"],
    ROUTINE_EVENT: ["cleanser", "moisturiser", "sunscreen", "heat_protectant", "styling"],
}

ROUTINE_FREQUENCY = {
    ROUTINE_MORNING: "Every morning",
    ROUTINE_EVENING: "Every evening",
    ROUTINE_WASH_DAY: "On the days you wash your hair",
    ROUTINE_WEEKLY: "Once or twice a week",
    ROUTINE_EVENT: "The day of something that matters",
}

# Slots where the ordinary advice is "not every day".
OCCASIONAL_SLOTS = {"exfoliant", "hair_mask"}

# Safety notes attached to a step because of what it is, not because of any
# condition. Plain product-care wording only.
SLOT_SAFETY_NOTES = {
    "exfoliant": "Two or three times a week is plenty. More is not better here.",
    "treatment": "Introduce a new active slowly — every other night to start — and stop if it stings.",
    "sunscreen": "Reapply if you are outdoors for a long stretch.",
    "heat_protectant": "Use it every single time you reach for a heat tool, not just occasionally.",
    "scalp_care": "Follow the product label for how long to leave it on.",
}


@dataclass
class RoutineStep:
    slot: str
    label: str
    order: int
    required: bool
    why: str
    frequency: str
    item_id: Optional[str] = None
    product_name: Optional[str] = None
    safety_note: str = ""
    alternative: str = ""
    climate_note: str = ""
    is_gap: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "label": self.label, "order": self.order,
            "required": self.required, "optional": not self.required,
            "why": self.why, "frequency": self.frequency,
            "inventory_item_id": self.item_id, "product_name": self.product_name,
            "owned": self.item_id is not None,
            "safety_note": self.safety_note, "alternative": self.alternative,
            "climate_note": self.climate_note, "is_gap": self.is_gap,
        }


@dataclass
class CompiledRoutine:
    kind: str
    label: str
    frequency: str
    steps: list[RoutineStep] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    climate_notes: list[dict[str, Any]] = field(default_factory=list)
    skipped_for_allergy: list[str] = field(default_factory=list)

    @property
    def owned_step_count(self) -> int:
        return sum(1 for step in self.steps if step.item_id)

    @property
    def gap_count(self) -> int:
        return sum(1 for step in self.steps if step.is_gap)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "label": self.label, "frequency": self.frequency,
            "steps": [step.as_dict() for step in self.steps],
            "owned_step_count": self.owned_step_count,
            "gap_count": self.gap_count,
            "warnings": [row.as_dict() for row in self.findings],
            "climate_notes": self.climate_notes,
            "skipped_for_allergy": self.skipped_for_allergy,
            "disclaimer": ROUTINE_DISCLAIMER,
        }


# Descriptions used when a required step has nothing owned to fill it. Generic
# on purpose — a category, never a product.
GAP_TEXT = {
    "cleanser": "a gentle face wash",
    "moisturiser": "a basic moisturiser",
    "sunscreen": "a sunscreen you will actually wear every day",
    "shampoo": "a shampoo",
    "conditioner": "a conditioner",
}

ALTERNATIVE_TEXT = {
    "cleanser": "If you run out, plain water in the morning is better than a harsh soap.",
    "moisturiser": "Any other moisturiser you own will do the job.",
    "sunscreen": "Shade, a hat and long sleeves cover you until you replace it.",
    "exfoliant": "Skipping it entirely for a week does no harm.",
    "shampoo": "Any other shampoo you own works for one wash.",
    "conditioner": "A leave-in or a hair oil on the ends will do for one wash.",
    "heat_protectant": "With none on the shelf, skip the heat tool that day rather than going without.",
    "treatment": "Skip it and keep the cleanser and moisturiser — those two carry most of the routine.",
}


def compile_routine(
    kind: str,
    products: Sequence[ShelfProduct],
    *,
    allergies: Sequence[str] = (),
    climate: Optional[str] = None,
    today: Optional[date] = None,
) -> CompiledRoutine:
    """Build one routine from what the user owns."""
    if kind not in ROUTINE_SLOTS:
        raise ValueError(f"'{kind}' is not a routine we build.")

    blocked = rules_engine.excluded_by_allergy(products, allergies)
    usable = [product for product in products if product.id not in blocked]
    skipped = [
        product.item.display_name for product in products if product.id in blocked
    ]

    routine = CompiledRoutine(
        kind=kind, label=ROUTINE_LABELS[kind], frequency=ROUTINE_FREQUENCY[kind],
        skipped_for_allergy=skipped,
    )

    present_slots: set[str] = set()
    for slot_key in ROUTINE_SLOTS[kind]:
        spec = SLOT_BY_KEY[slot_key]
        ranked = rules_engine.rank_for_slot(usable, slot_key, today)
        chosen = ranked[0] if ranked else None

        frequency = "Two or three times a week" if slot_key in OCCASIONAL_SLOTS else routine.frequency
        step = RoutineStep(
            slot=slot_key, label=spec.label, order=spec.order,
            required=spec.required, why=spec.why, frequency=frequency,
            safety_note=SLOT_SAFETY_NOTES.get(slot_key, ""),
            alternative=ALTERNATIVE_TEXT.get(slot_key, ""),
        )
        if chosen is not None:
            step.item_id = chosen.id
            step.product_name = chosen.item.display_name
            present_slots.add(slot_key)
        elif spec.required:
            # A required step with nothing to fill it is the only kind of gap
            # we mention, and it names a category rather than a product.
            step.is_gap = True
            step.product_name = None
            # Not "you have nothing recorded" — ``narrative_is_safe`` bans the
            # phrase "you have " outright, because that is how a diagnosis
            # starts. The ban is blunt on purpose, so this is worded around it
            # rather than the ban being narrowed for one sentence.
            step.why = f"{spec.why} Nothing is recorded for this step yet."
            step.alternative = f"Add {GAP_TEXT.get(slot_key, 'something for this step')} you already own, if there is one."
        else:
            # Optional step with nothing owned: leave it out entirely rather
            # than showing an empty row.
            continue
        routine.steps.append(step)

    routine.steps.sort(key=lambda row: row.order)
    routine.climate_notes = rules_engine.climate_notes(climate, present_slots)
    for note in routine.climate_notes:
        for step in routine.steps:
            if step.slot == note["slot"]:
                step.climate_note = note["note"]

    # Warnings that apply to the products this routine actually uses.
    used_ids = {step.item_id for step in routine.steps if step.item_id}
    in_use = [product for product in usable if product.id in used_ids]
    routine.findings = (
        rules_engine.compatibility_findings(in_use)
        + rules_engine.allergy_findings(products, allergies)
    )

    _assert_safe(routine)
    return routine


def _assert_safe(routine: CompiledRoutine) -> None:
    """Sweep every string this routine will show.

    The rules are reviewed, so this should never fire — which is exactly why it
    is cheap to keep. A careless edit to a note is caught here rather than by a
    user.
    """
    for step in routine.steps:
        for value in (step.why, step.safety_note, step.alternative, step.climate_note):
            if value and not narrative_is_safe(value):
                raise AssertionError(f"Unsafe routine wording in step {step.slot}: {value!r}")
    for finding in routine.findings:
        for value in (finding.headline, finding.detail, finding.evidence_note):
            if value and not narrative_is_safe(value):
                raise AssertionError(f"Unsafe wording in finding {finding.rule_id}: {value!r}")


def compile_all(
    beauty: Sequence[ShelfProduct],
    hair: Sequence[ShelfProduct],
    *,
    allergies: Sequence[str] = (),
    climate: Optional[str] = None,
    today: Optional[date] = None,
) -> list[CompiledRoutine]:
    """Every routine the user has enough products to make sense of.

    A routine with no owned steps at all is left out. Showing someone an empty
    wash-day routine when they have recorded no hair products is noise.
    """
    routines: list[CompiledRoutine] = []
    for kind in ROUTINE_KINDS:
        source = hair if kind == ROUTINE_WASH_DAY else beauty if kind in (ROUTINE_MORNING, ROUTINE_EVENING) else list(beauty) + list(hair)
        routine = compile_routine(kind, source, allergies=allergies, climate=climate, today=today)
        if routine.owned_step_count == 0 and routine.gap_count == 0:
            continue
        routines.append(routine)
    return routines
