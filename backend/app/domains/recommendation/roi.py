"""The Appearance ROI model.

A number a user is asked to act on has to be one they can check. So this is a
plain weighted sum of named factors, each of which is computed from stored
facts, carries its own explanation, and is saved to
``purchase_evaluation_factors`` so the total can always be shown as its parts.

    roi = Σ (factor_value × factor_weight) / Σ (factor_weight of scored factors)

Renormalising over *scored* factors is the important detail. When the price is
missing, the price factor is dropped rather than guessed, and the remaining
factors are reweighted — a missing price lowers confidence, not the score.

The verdict follows from the score by fixed thresholds, with two overrides that
protect the user from the model's own arithmetic: something that closely
duplicates what they already own cannot be a Buy, and something they cannot
wear anywhere cannot be a Buy.

No language model is involved in any part of this. The model is asked only to
phrase the result afterwards, and its wording is discarded if it disagrees with
the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.domains.recommendation import compatibility as compat
from app.domains.recommendation.candidates import garment_shape, shape_from_parts
from app.domains.recommendation.context import OwnedItem
from app.domains.recommendation.models import ROI_VERSION, VERDICT_BUY, VERDICT_SKIP, VERDICT_WAIT
from app.domains.recommendation.occasions import OCCASIONS, SLOT_CATEGORY

# Factor weights. They do not need to sum to 1 — the score renormalises over
# whichever factors could actually be scored.
FACTOR_WEIGHTS: Dict[str, float] = {
    "new_combinations": 0.22,
    "category_gap": 0.16,
    "duplicate_penalty": 0.16,
    "occasion_relevance": 0.12,
    "colour_compatibility": 0.12,
    "climate_suitability": 0.08,
    "expected_use_frequency": 0.08,
    "versatility": 0.06,
    "price_context": 0.10,
}

FACTOR_LABELS: Dict[str, str] = {
    "new_combinations": "New outfit combinations",
    "category_gap": "Fills a gap",
    "duplicate_penalty": "How different it is from what you own",
    "occasion_relevance": "Occasions it covers",
    "colour_compatibility": "Colour fit with your wardrobe",
    "climate_suitability": "Suits your climate",
    "expected_use_frequency": "How often you would wear it",
    "versatility": "Versatility",
    "price_context": "Price against expected wears",
}

BUY_THRESHOLD = 0.65
WAIT_THRESHOLD = 0.45

# Above this, two items are close enough that owning both is hard to justify.
DUPLICATE_LIMIT = 0.82

# Above this, an owned item is worth showing as "you already have something
# that could do this job". Deliberately low: inventory rows are often sparse —
# shoes record no formality at all — so demanding a high score here would mean
# silently showing the user nothing whenever their details are thin.
ALTERNATIVE_LIMIT = 0.25

# What "good value" means for cost per wear, in rupees. Used only when the user
# supplied a price; never fetched, never estimated.
GOOD_COST_PER_WEAR_INR = 150.0
POOR_COST_PER_WEAR_INR = 2000.0


@dataclass
class Candidate:
    """The thing being considered. Deliberately not an inventory item."""

    category: str
    display_name: str
    brand: Optional[str] = None
    subcategory: Optional[str] = None
    colour: Optional[str] = None
    size: Optional[str] = None
    fabric: Optional[str] = None
    fit: Optional[str] = None
    formality: Optional[str] = None
    occasion_tags: Sequence[str] = ()
    season_tags: Sequence[str] = ()
    price: Optional[Decimal] = None
    currency: str = "INR"

    def as_details(self) -> Dict[str, Any]:
        """The same shape as inventory details, so the shared scorers apply."""
        return {
            "colour": self.colour, "size": self.size, "fabric": self.fabric,
            "fit": self.fit, "formality": self.formality,
            "occasion": list(self.occasion_tags), "season": list(self.season_tags),
        }


@dataclass
class Factor:
    key: str
    label: str
    raw_value: float
    weight: float
    contribution: float
    explanation: str


@dataclass
class ROIResult:
    score: float
    verdict: str
    confidence: float
    factors: List[Factor]
    new_combinations: int
    duplicate_item_ids: List[str]
    alternative_item_ids: List[str]
    fit_risks: List[str]
    colour_risks: List[str]
    climate_notes: List[str]
    missing_information: List[str]
    version: str = ROI_VERSION


# --- Individual factors -----------------------------------------------------


def _pairing_slots(category: str) -> List[str]:
    """Which owned categories a new item would be worn alongside."""
    if category == "wardrobe":
        return ["wardrobe", "shoes", "accessories"]
    if category == "shoes":
        return ["wardrobe", "accessories"]
    if category == "accessories":
        return ["wardrobe", "shoes"]
    return []


def estimate_new_combinations(candidate: Candidate, owned: Sequence[OwnedItem]) -> Tuple[int, List[str]]:
    """How many workable new outfits this item would actually create.

    A pairing counts only when the colours work and the formality is within one
    level. Counting every theoretical permutation would produce a big, flattering
    and meaningless number.
    """
    details = candidate.as_details()
    level = compat.item_formality(details)
    notes: List[str] = []

    def works(other: OwnedItem) -> bool:
        score, _ = compat.colour_compatibility(candidate.colour, other.details.get("colour"))
        if score < 0.6:
            return False
        other_level = compat.item_formality(other.details)
        if level is not None and other_level is not None and abs(level - other_level) > 1:
            return False
        return True

    if candidate.category == "wardrobe":
        shape = shape_from_parts(candidate.subcategory, candidate.display_name, candidate.fit)
        shoes = [item for item in owned if item.category == "shoes" and works(item)]
        if shape == "one_piece":
            combinations = len(shoes)
            notes.append(f"Worn on its own with {len(shoes)} of your shoes.")
        else:
            wanted = "bottom" if shape in ("top", "layer", "unknown") else "top"
            partners = [
                item for item in owned
                if item.category == "wardrobe" and garment_shape(item) == wanted and works(item)
            ]
            combinations = len(partners) * max(len(shoes), 1)
            notes.append(f"Pairs with {len(partners)} of your {wanted}s and {len(shoes)} pairs of shoes.")
        return combinations, notes

    partners = [
        item for item in owned
        if item.category in _pairing_slots(candidate.category) and item.category == "wardrobe" and works(item)
    ]
    notes.append(f"Works with {len(partners)} things you already own.")
    return len(partners), notes


def _normalise_combinations(count: int) -> float:
    """Diminishing returns: the tenth new combination matters less than the first."""
    if count <= 0:
        return 0.0
    return round(min(1.0, count / 12.0) ** 0.7, 4)


def _colour_similarity(first: Any, second: Any) -> float:
    """How much two colours contribute to two items looking like the same buy."""
    left, right = compat.normalise_colour(first), compat.normalise_colour(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 0.30
    if compat.is_neutral(left) and compat.is_neutral(right):
        return 0.18
    if compat.is_neutral(left) or compat.is_neutral(right):
        return 0.0
    # Within about a quarter-turn the two read as the same colour choice.
    return 0.12 if compat.hue_distance(left, right) <= 30 else 0.0


def similarity(candidate: Candidate, item: OwnedItem) -> float:
    """How close a candidate is to something already owned, 0-1."""
    if item.category != candidate.category:
        return 0.0
    score = 0.0
    if candidate.subcategory and item.subcategory and candidate.subcategory.lower() == item.subcategory.lower():
        score += 0.35
    elif candidate.subcategory and item.subcategory:
        score += 0.05
    score += _colour_similarity(candidate.colour, item.details.get("colour"))
    candidate_level = compat.item_formality(candidate.as_details())
    item_level = compat.item_formality(item.details)
    if candidate_level is not None and item_level is not None and candidate_level == item_level:
        score += 0.20
    if candidate.brand and item.brand and candidate.brand.lower() == item.brand.lower():
        score += 0.05
    words = set(candidate.display_name.lower().split()) - {"the", "a", "and", "with"}
    other_words = set(item.display_name.lower().split())
    if words and len(words & other_words) / len(words) >= 0.6:
        score += 0.10
    return round(min(1.0, score), 4)


def _category_gap(candidate: Candidate, owned: Sequence[OwnedItem], occasion_key: Optional[str]) -> Tuple[float, str]:
    same = [item for item in owned if item.category == candidate.category]
    if not same:
        return 1.0, f"You have nothing recorded in {candidate.category} yet."
    if occasion_key:
        occasion = OCCASIONS[occasion_key]
        usable = [
            item for item in same
            if compat.occasion_relevance(item.details, occasion.key, occasion.label)[0] >= 0.9
            or (compat.item_formality(item.details) or 0) == occasion.formality
        ]
        if not usable:
            return 0.9, f"You own {len(same)} {candidate.category} items but none tagged for {occasion.label.lower()}."
        return round(max(0.1, 1.0 - min(len(usable), 8) / 8.0), 4), f"You already have {len(usable)} {candidate.category} options for {occasion.label.lower()}."
    return round(max(0.1, 1.0 - min(len(same), 12) / 12.0), 4), f"You already own {len(same)} things in {candidate.category}."


def _colour_factor(candidate: Candidate, owned: Sequence[OwnedItem], favourites: Sequence[str], disliked: Sequence[str]) -> Tuple[float, str, List[str]]:
    risks: List[str] = []
    palette, palette_reason = compat.palette_fit(candidate.colour, favourites, disliked)
    if palette == 0.0:
        risks.append(palette_reason)
        return 0.0, palette_reason, risks
    partners = [item.details.get("colour") for item in owned if item.category == "wardrobe"]
    scores = [compat.colour_compatibility(candidate.colour, colour)[0] for colour in partners if colour]
    if not scores:
        return palette, f"{palette_reason} Nothing recorded to compare it against in your wardrobe.", risks
    average = round(sum(scores) / len(scores), 4)
    weak = sum(1 for score in scores if score < 0.5)
    if weak and weak / len(scores) > 0.5:
        risks.append("This colour clashes with more than half of the wardrobe colours you have recorded.")
    combined = round(0.6 * average + 0.4 * palette, 4)
    return combined, f"Averages {average:.2f} against {len(scores)} recorded wardrobe colours. {palette_reason}", risks


def _versatility(candidate: Candidate) -> Tuple[float, str]:
    level = compat.item_formality(candidate.as_details())
    tags = [str(tag).lower() for tag in candidate.occasion_tags]
    if tags:
        return round(min(1.0, len(set(tags)) / 4.0), 4), f"Tagged for {len(set(tags))} kinds of occasion."
    if level is None:
        return compat.NEUTRAL, "No formality or occasion recorded, so versatility could not be scored."
    reach = sum(1 for occasion in OCCASIONS.values() if abs(occasion.formality - level) <= 1)
    return round(min(1.0, reach / len(OCCASIONS)) ** 0.5, 4), f"At this formality it would suit around {reach} of the {len(OCCASIONS)} occasions we support."


def _expected_use(candidate: Candidate, owned: Sequence[OwnedItem], combinations: int) -> Tuple[float, str]:
    same = [item for item in owned if item.category == candidate.category]
    if same:
        used = [item for item in same if item.usage_count > 0]
        rate = len(used) / len(same)
        blended = round(0.6 * min(1.0, combinations / 10.0) + 0.4 * rate, 4)
        return blended, f"You actually wear {len(used)} of your {len(same)} {candidate.category} items, and this would create {combinations} new combinations."
    return round(min(1.0, combinations / 10.0), 4), f"Estimated from the {combinations} new combinations it would create."


def _price_factor(candidate: Candidate, combinations: int) -> Optional[Tuple[float, str]]:
    """Cost against expected wears. Returns None when no price was supplied."""
    if candidate.price is None:
        return None
    price = float(candidate.price)
    expected_wears = max(4, min(combinations * 3, 60))
    cost_per_wear = price / expected_wears
    if cost_per_wear <= GOOD_COST_PER_WEAR_INR:
        value = 1.0
    elif cost_per_wear >= POOR_COST_PER_WEAR_INR:
        value = 0.05
    else:
        span = POOR_COST_PER_WEAR_INR - GOOD_COST_PER_WEAR_INR
        value = round(1.0 - (cost_per_wear - GOOD_COST_PER_WEAR_INR) / span, 4)
    return value, (
        f"At {candidate.currency} {price:,.0f} over roughly {expected_wears} wears, that is about "
        f"{candidate.currency} {cost_per_wear:,.0f} each time you wear it."
    )


# --- The model ---------------------------------------------------------------


def evaluate(
    candidate: Candidate,
    owned: Sequence[OwnedItem],
    *,
    occasion_key: Optional[str] = None,
    favourite_colours: Sequence[str] = (),
    disliked_colours: Sequence[str] = (),
    fit_preferences: Optional[Dict[str, Any]] = None,
    climate: Optional[str] = None,
) -> ROIResult:
    """Score a purchase and return Buy, Wait or Skip with the full arithmetic."""
    details = candidate.as_details()
    missing: List[str] = []
    factors: List[Factor] = []

    combinations, combination_notes = estimate_new_combinations(candidate, owned)

    similarities = sorted(
        ((similarity(candidate, item), item) for item in owned if item.category == candidate.category),
        key=lambda pair: -pair[0],
    )
    duplicates = [item for score, item in similarities if score >= DUPLICATE_LIMIT]
    close = [item for score, item in similarities if ALTERNATIVE_LIMIT <= score < DUPLICATE_LIMIT]
    top_similarity = similarities[0][0] if similarities else 0.0

    gap_value, gap_reason = _category_gap(candidate, owned, occasion_key)
    colour_value, colour_reason, colour_risks = _colour_factor(candidate, owned, favourite_colours, disliked_colours)
    climate_value, climate_reason = compat.weather_suitability(details, climate)
    versatility_value, versatility_reason = _versatility(candidate)
    use_value, use_reason = _expected_use(candidate, owned, combinations)

    if occasion_key:
        occasion = OCCASIONS[occasion_key]
        occasion_value, occasion_reason = compat.occasion_relevance(details, occasion.key, occasion.label)
        level = compat.item_formality(details)
        formality_value, formality_reason = compat.formality_match(level, occasion.formality)
        occasion_value = round((occasion_value + formality_value) / 2, 4)
        occasion_reason = f"{occasion_reason} {formality_reason}"
    else:
        occasion_value, occasion_reason = versatility_value, "No occasion was named, so this was scored on general versatility."

    def add(key: str, value: float, explanation: str) -> None:
        weight = FACTOR_WEIGHTS[key]
        factors.append(Factor(key=key, label=FACTOR_LABELS[key], raw_value=round(value, 4), weight=weight, contribution=round(value * weight, 4), explanation=explanation))

    add("new_combinations", _normalise_combinations(combinations), f"Creates about {combinations} new outfit combinations. " + " ".join(combination_notes))
    add("category_gap", gap_value, gap_reason)
    add("duplicate_penalty", round(1.0 - top_similarity, 4), (
        f"Closest match in your inventory scores {top_similarity:.2f} similarity."
        if similarities else "You own nothing in this category to compare it against."
    ))
    add("occasion_relevance", occasion_value, occasion_reason)
    add("colour_compatibility", colour_value, colour_reason)
    add("climate_suitability", climate_value, climate_reason)
    add("expected_use_frequency", use_value, use_reason)
    add("versatility", versatility_value, versatility_reason)

    price = _price_factor(candidate, combinations)
    if price is not None:
        add("price_context", price[0], price[1])
    else:
        missing.append("No price was given, so cost per wear was left out of the score.")

    if not climate:
        missing.append("No climate or weather was given, so that factor is a neutral placeholder.")
    if not candidate.size:
        missing.append("No size was given, so fit could not be checked against your usual sizes.")
    if not candidate.colour:
        missing.append("No colour was read, so colour matching is a neutral placeholder.")

    total_weight = sum(factor.weight for factor in factors)
    score = round(sum(factor.contribution for factor in factors) / total_weight, 4) if total_weight else 0.5

    fit_score, fit_risks = compat.fit_alignment(details, fit_preferences or {})

    verdict = VERDICT_BUY if score >= BUY_THRESHOLD else VERDICT_WAIT if score >= WAIT_THRESHOLD else VERDICT_SKIP
    # Two overrides the arithmetic must not be allowed to talk us out of.
    if duplicates and verdict == VERDICT_BUY:
        verdict = VERDICT_WAIT
    if duplicates and score < 0.6:
        verdict = VERDICT_SKIP
    if combinations == 0 and verdict == VERDICT_BUY:
        verdict = VERDICT_WAIT

    confidence = round(max(0.2, min(0.95, 0.9 - 0.12 * len(missing))), 4)

    return ROIResult(
        score=score,
        verdict=verdict,
        confidence=confidence,
        factors=factors,
        new_combinations=combinations,
        duplicate_item_ids=[str(item.id) for item in duplicates],
        alternative_item_ids=[str(item.id) for item in close[:5]],
        fit_risks=fit_risks if fit_score < 0.7 else [risk for risk in fit_risks if "No size recorded" in risk],
        colour_risks=colour_risks,
        climate_notes=[climate_reason],
        missing_information=missing,
    )


VERDICT_HEADLINES = {
    VERDICT_BUY: "Buy — this earns its place",
    VERDICT_WAIT: "Wait — worth thinking about",
    VERDICT_SKIP: "Skip — you already have this covered",
}


def deterministic_summary(result: ROIResult, candidate: Candidate) -> str:
    """The explanation shown when no model is involved, or when its wording fails."""
    top = sorted(result.factors, key=lambda factor: -factor.contribution)[:3]
    weakest = sorted(result.factors, key=lambda factor: factor.contribution)[0]
    parts = [
        f"{candidate.display_name} scores {result.score:.2f} on the Appearance ROI model ({result.version}).",
        "Strongest points: " + "; ".join(f"{factor.label.lower()} ({factor.raw_value:.2f})" for factor in top) + ".",
        f"Weakest point: {weakest.label.lower()} ({weakest.raw_value:.2f}). {weakest.explanation}",
    ]
    if result.duplicate_item_ids:
        parts.append(f"You already own {len(result.duplicate_item_ids)} very similar thing(s), which is why this is not a straight Buy.")
    if result.alternative_item_ids:
        parts.append(f"There are {len(result.alternative_item_ids)} things in your inventory that could do a similar job.")
    if result.missing_information:
        parts.append("Missing information: " + " ".join(result.missing_information))
    return " ".join(parts)
