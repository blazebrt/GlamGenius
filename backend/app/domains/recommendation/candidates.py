"""Stage 3 and 5: deterministic filtering, then outfit assembly.

Nothing here calls a model. Candidates come from confirmed inventory and only
from confirmed inventory, which is what makes the guarantee at the top of the
API — every owned item in a look is a real row this user owns — structural
rather than something a prompt is asked to respect.

Clothing is assembled rather than picked: a look needs either one full piece
(a saree, a kurta set, a dress, a suit) or a top and a bottom that work
together. Getting that wrong produces confidently absurd output, so the shape
of the garment is classified from what the user recorded, and a look that
cannot be completed is reported as incomplete instead of being padded out.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from app.domains.recommendation import compatibility as compat
from app.domains.recommendation.context import OwnedItem, StyleContext
from app.domains.recommendation.occasions import (
    SLOT_ACCESSORIES,
    SLOT_CATEGORY,
    SLOT_CLOTHING,
    SLOT_GROOMING,
    SLOT_HAIR,
    SLOT_PERFUME,
    SLOT_SHOES,
)

# --- Garment shape ----------------------------------------------------------
ONE_PIECE_WORDS = (
    "saree", "sari", "lehenga", "anarkali", "gown", "dress", "jumpsuit", "romper",
    "kurta set", "sherwani", "suit", "salwar suit", "co-ord", "coord", "kaftan", "abaya",
)
TOP_WORDS = (
    "shirt", "t-shirt", "tshirt", "tee", "top", "blouse", "kurta", "kurti", "tunic",
    "sweater", "sweatshirt", "hoodie", "blazer", "jacket", "waistcoat", "polo", "crop",
)
BOTTOM_WORDS = (
    "trouser", "pant", "jeans", "chino", "skirt", "shorts", "palazzo", "salwar",
    "churidar", "dhoti", "leggings", "track pant", "joggers", "lungi", "culottes",
)
# Layers sit over a top rather than replacing it, so they never satisfy the
# "top" requirement on their own.
LAYER_WORDS = ("blazer", "jacket", "waistcoat", "cardigan", "shrug", "overcoat", "coat")


def shape_from_parts(subcategory: Optional[str], display_name: str, fit: Optional[str] = None) -> str:
    """``one_piece``, ``top``, ``bottom``, ``layer`` or ``unknown``.

    Takes loose parts rather than an item so a shopping candidate — which is
    deliberately not an inventory row — can be classified by the same rules.
    """
    text = " ".join(str(part).lower() for part in (subcategory or "", display_name or "", fit or "") if part)
    if any(word in text for word in ONE_PIECE_WORDS):
        return "one_piece"
    if any(word in text for word in LAYER_WORDS):
        return "layer"
    if any(word in text for word in BOTTOM_WORDS):
        return "bottom"
    if any(word in text for word in TOP_WORDS):
        return "top"
    return "unknown"


def garment_shape(item: OwnedItem) -> str:
    return shape_from_parts(item.subcategory, item.display_name, item.details.get("fit"))


# --- Scored candidates ------------------------------------------------------


@dataclass
class ScoredItem:
    item: OwnedItem
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    fit_risks: list[str] = field(default_factory=list)
    excluded_reason: Optional[str] = None

    @property
    def id(self) -> uuid.UUID:
        return self.item.id

    @property
    def shape(self) -> str:
        return garment_shape(self.item)


CONDITION_PENALTY = {"excellent": 1.0, "good": 1.0, "fair": 0.9, "worn": 0.7, "needs_attention": 0.5}


def score_item(item: OwnedItem, context: StyleContext) -> ScoredItem:
    """Score one owned item against the resolved constraints."""
    details = item.details
    factors: dict[str, float] = {}
    reasons: dict[str, str] = {}

    level = compat.item_formality(details)
    factors["formality"], reasons["formality"] = compat.formality_match(level, context.target_formality)
    factors["occasion"], reasons["occasion"] = compat.occasion_relevance(details, context.occasion.key, context.occasion.label)
    factors["weather"], reasons["weather"] = compat.weather_suitability(details, context.weather)
    factors["palette"], reasons["palette"] = compat.palette_fit(details.get("colour"), context.favourite_colours, context.disliked_colours)
    fit_score, fit_risks = compat.fit_alignment(details, context.fit_preferences)
    factors["fit"] = fit_score
    reasons["fit"] = fit_risks[0] if fit_risks else "Nothing recorded flags a fit problem."
    factors["comfort"], reasons["comfort"] = compat.comfort_alignment(details, context.comfort_preference)

    score = compat.weighted(factors)
    score *= CONDITION_PENALTY.get(item.condition, 0.9)

    scored = ScoredItem(item=item, score=round(score, 4), factors=factors, reasons=reasons, fit_risks=fit_risks)

    # Hard exclusions. Only two, and both are things the user themselves said.
    if factors["palette"] == 0.0:
        scored.excluded_reason = reasons["palette"]
    elif fit_score <= 0.2 and fit_risks:
        scored.excluded_reason = fit_risks[0]
    return scored


def _adjust_for_history(scored: ScoredItem, context: StyleContext) -> float:
    """Nudge by what the user has told us about this item before."""
    score = scored.score
    if scored.id in context.preferred_item_ids:
        score = min(1.0, score + 0.25)
    if scored.id in context.disliked_item_ids:
        score = max(0.0, score - 0.15)
    return round(score, 4)


def filter_candidates(context: StyleContext) -> dict[str, list[ScoredItem]]:
    """Stage 3: every owned item that could legitimately fill each slot."""
    buckets: dict[str, list[ScoredItem]] = {slot: [] for slot in SLOT_CATEGORY}
    for slot, category in SLOT_CATEGORY.items():
        for item in context.by_category(category):
            scored = score_item(item, context)
            if scored.excluded_reason:
                continue
            scored.score = _adjust_for_history(scored, context)
            buckets[slot].append(scored)
        buckets[slot].sort(key=lambda row: (-row.score, row.item.display_name))
    return buckets


# --- Outfit assembly --------------------------------------------------------


@dataclass
class OptionalAddition:
    """Something the look needs that the user does not own.

    It carries a description of the gap and nothing else. No brand, no price, no
    product: inventing those would be exactly the fabrication this phase bans.
    """

    slot: str
    description: str
    reason: str


@dataclass
class OutfitCandidate:
    clothing: list[ScoredItem] = field(default_factory=list)
    shoes: Optional[ScoredItem] = None
    accessories: list[ScoredItem] = field(default_factory=list)
    perfume: Optional[ScoredItem] = None
    hair: Optional[ScoredItem] = None
    grooming: Optional[ScoredItem] = None
    optional_additions: list[OptionalAddition] = field(default_factory=list)
    cohesion_score: float = 0.5
    cohesion_reasons: list[str] = field(default_factory=list)
    total_score: float = 0.0
    factor_scores: dict[str, Any] = field(default_factory=dict)

    def owned_items(self) -> list[ScoredItem]:
        rows = list(self.clothing)
        for single in (self.shoes, self.perfume, self.hair, self.grooming):
            if single is not None:
                rows.append(single)
        rows.extend(self.accessories)
        return rows

    def owned_ids(self) -> list[uuid.UUID]:
        return [row.id for row in self.owned_items()]

    def signature(self) -> tuple[str, ...]:
        return tuple(sorted(str(item_id) for item_id in self.owned_ids()))

    @property
    def colours(self) -> list[Any]:
        return [row.item.colour for row in self.owned_items() if row.item.colour]


CLOTHING_GAP_TEXT = {
    1: "something relaxed and comfortable for the top half",
    2: "a simple casual top",
    3: "a smart-casual top such as a well-cut shirt or kurta",
    4: "a business-casual top such as a pressed shirt or a structured kurta",
    5: "a formal top such as a tailored shirt or a formal kurta",
}
BOTTOM_GAP_TEXT = {
    1: "comfortable bottoms such as track pants or pyjamas",
    2: "casual bottoms such as jeans or cotton trousers",
    3: "smart-casual bottoms such as chinos or a well-cut skirt",
    4: "business-casual trousers or a tailored skirt",
    5: "formal trousers or a formal skirt",
}
SHOE_GAP_TEXT = {
    1: "comfortable flat shoes or trainers",
    2: "casual shoes such as trainers or sandals",
    3: "smart-casual shoes such as loafers or clean leather sandals",
    4: "business-appropriate closed shoes",
    5: "formal closed shoes in a dark neutral",
}


def _clothing_sets(candidates: Sequence[ScoredItem], limit: int = 6) -> list[list[ScoredItem]]:
    """Wearable clothing combinations, best first.

    A one-piece stands alone. Otherwise a top is paired with a bottom, and the
    pair is only offered if both halves exist — a "look" with no bottom half is
    not a look.
    """
    one_pieces = [row for row in candidates if row.shape == "one_piece"]
    tops = [row for row in candidates if row.shape in ("top", "unknown")]
    bottoms = [row for row in candidates if row.shape == "bottom"]
    layers = [row for row in candidates if row.shape == "layer"]

    sets: list[list[ScoredItem]] = [[row] for row in one_pieces[:limit]]
    for top in tops[:limit]:
        for bottom in bottoms[:limit]:
            score, _ = compat.colour_compatibility(top.item.colour, bottom.item.colour)
            if score <= 0.0:
                continue
            sets.append([top, bottom])
    # A layer is only ever added on top of a complete set, never instead of one.
    if layers:
        best_layer = layers[0]
        for existing in list(sets[:limit]):
            if best_layer not in existing:
                sets.append(existing + [best_layer])
    return sets


def _set_score(rows: Sequence[ScoredItem]) -> float:
    return round(sum(row.score for row in rows) / max(len(rows), 1), 4)


def _clothing_gaps(context: StyleContext, chosen: Sequence[ScoredItem]) -> list[OptionalAddition]:
    shapes = {row.shape for row in chosen}
    gaps: list[OptionalAddition] = []
    target = context.target_formality
    if "one_piece" in shapes:
        return gaps
    if not shapes & {"top", "unknown"}:
        gaps.append(OptionalAddition(SLOT_CLOTHING, CLOTHING_GAP_TEXT[target], "Nothing in your confirmed wardrobe fits this slot for this occasion."))
    if "bottom" not in shapes:
        gaps.append(OptionalAddition(SLOT_CLOTHING, BOTTOM_GAP_TEXT[target], "Nothing in your confirmed wardrobe fits this slot for this occasion."))
    return gaps


def build_candidates(
    context: StyleContext,
    buckets: dict[str, list[ScoredItem]],
    *,
    exclude_ids: Sequence[uuid.UUID] = (),
    limit: int = 12,
) -> list[OutfitCandidate]:
    """Stage 5: assemble complete looks from the filtered candidates."""
    blocked = set(exclude_ids)
    available = {
        slot: [row for row in rows if row.id not in blocked]
        for slot, rows in buckets.items()
    }

    clothing_sets = _clothing_sets(available[SLOT_CLOTHING])
    if not clothing_sets:
        clothing_sets = [[]]

    shoe_options: list[Optional[ScoredItem]] = list(available[SLOT_SHOES][:4]) or [None]
    candidates: list[OutfitCandidate] = []

    for clothing in clothing_sets:
        for shoes in shoe_options:
            candidate = OutfitCandidate(clothing=list(clothing), shoes=shoes)
            candidate.optional_additions = _clothing_gaps(context, clothing)
            if shoes is None and SLOT_SHOES in context.occasion.required_slots:
                candidate.optional_additions.append(OptionalAddition(
                    SLOT_SHOES, SHOE_GAP_TEXT[context.target_formality],
                    "You have no confirmed shoes that suit this occasion.",
                ))
            candidates.append(candidate)

    for candidate in candidates:
        _attach_optional_slots(context, available, candidate)
        _finalise(context, candidate)

    candidates.sort(key=lambda row: (-row.total_score, row.signature()))
    return candidates[:limit]


def _attach_optional_slots(context: StyleContext, available: dict[str, list[ScoredItem]], candidate: OutfitCandidate) -> None:
    """Fill the optional slots around a chosen base, best first."""
    base_colours = candidate.colours
    optional = set(context.occasion.optional_slots) | set(context.occasion.required_slots)

    if SLOT_ACCESSORIES in optional:
        ranked = sorted(
            available[SLOT_ACCESSORIES],
            key=lambda row: (-round((row.score + compat.cohesion(base_colours + [row.item.colour])[0]) / 2, 4), row.item.display_name),
        )
        candidate.accessories = ranked[:2]
    for slot, attribute in ((SLOT_PERFUME, "perfume"), (SLOT_HAIR, "hair"), (SLOT_GROOMING, "grooming")):
        if slot in optional and available[slot]:
            setattr(candidate, attribute, available[slot][0])


# Weights that turn the parts of a look into one number. Documented in
# PHASE_4_REPORT.md and asserted in the tests.
LOOK_WEIGHTS = {"items": 0.55, "cohesion": 0.30, "completeness": 0.15}


def _finalise(context: StyleContext, candidate: OutfitCandidate) -> None:
    owned = candidate.owned_items()
    item_score = _set_score(owned) if owned else 0.0
    cohesion_score, cohesion_reasons = compat.cohesion(candidate.colours)

    required = set(context.occasion.required_slots)
    filled = 0
    if SLOT_CLOTHING in required:
        needed_clothing = 1
        filled += 1 if candidate.clothing and not _clothing_gaps(context, candidate.clothing) else 0
    else:
        needed_clothing = 0
    needed = len(required)
    if SLOT_SHOES in required:
        filled += 1 if candidate.shoes is not None else 0
    completeness = round(filled / needed, 4) if needed else 1.0

    candidate.cohesion_score = cohesion_score
    candidate.cohesion_reasons = cohesion_reasons
    candidate.factor_scores = {
        "items": item_score,
        "cohesion": cohesion_score,
        "completeness": completeness,
        "weights": LOOK_WEIGHTS,
        "target_formality": context.target_formality,
        "owned_item_count": len(owned),
        "optional_addition_count": len(candidate.optional_additions),
        "clothing_pieces_required": needed_clothing,
    }
    candidate.total_score = round(
        item_score * LOOK_WEIGHTS["items"]
        + cohesion_score * LOOK_WEIGHTS["cohesion"]
        + completeness * LOOK_WEIGHTS["completeness"],
        4,
    )
