"""Stage 4 and 6: rank candidates and pick up to three different options.

The three variants are not the top three scores. Top-three-by-score on a real
wardrobe returns the same shirt with three different trousers, which is not a
choice. Each variant is re-scored under a different priority, and a variant is
only offered if it is genuinely different from the ones already chosen:

* **Recommended** — the balanced answer.
* **Comfortable alternative** — comfort and easy wear weighted up, formality
  tolerance widened downward.
* **More expressive alternative** — colour contrast and less-worn items weighted
  up, so it is visibly a different decision rather than a shuffle.

Fewer than three is a legitimate outcome. Padding the list with a near-duplicate
would be pretending to offer a choice that does not exist.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domains.recommendation import compatibility as compat
from app.domains.recommendation.candidates import OutfitCandidate, ScoredItem
from app.domains.recommendation.context import StyleContext
from app.domains.recommendation.models import VARIANT_COMFORTABLE, VARIANT_EXPRESSIVE, VARIANT_RECOMMENDED

VARIANT_TITLES = {
    VARIANT_RECOMMENDED: "Recommended",
    VARIANT_COMFORTABLE: "Comfortable alternative",
    VARIANT_EXPRESSIVE: "More expressive alternative",
}
VARIANT_INTENT = {
    VARIANT_RECOMMENDED: "the balanced choice for this occasion",
    VARIANT_COMFORTABLE: "the choice that puts comfort first",
    VARIANT_EXPRESSIVE: "the choice that makes more of a statement",
}

# How much of a look may be shared with an already-chosen one before it stops
# counting as a different option.
MAX_CORE_OVERLAP = 0.6


@dataclass
class RankedLook:
    variant: str
    title: str
    candidate: OutfitCandidate
    score: float
    confidence: float
    why_it_works: str
    weather_note: str
    dress_code_note: str
    preparation_steps: list[str]
    missing_information: list[str]


def _core_ids(candidate: OutfitCandidate) -> set:
    ids = {row.id for row in candidate.clothing}
    if candidate.shoes is not None:
        ids.add(candidate.shoes.id)
    return ids


def is_distinct(candidate: OutfitCandidate, chosen: Sequence[OutfitCandidate]) -> bool:
    """True when this look is a real alternative to everything already picked."""
    core = _core_ids(candidate)
    if not core:
        return False
    for other in chosen:
        other_core = _core_ids(other)
        if core == other_core:
            return False
        union = core | other_core
        if union and len(core & other_core) / len(union) > MAX_CORE_OVERLAP:
            return False
    return True


# --- Variant priorities -----------------------------------------------------


def _comfort_bonus(candidate: OutfitCandidate) -> float:
    rows = candidate.owned_items()
    if not rows:
        return 0.0
    values = [row.factors.get("comfort", compat.NEUTRAL) for row in rows]
    heels = [
        float(row.item.details["heel_height"])
        for row in rows
        if isinstance(row.item.details.get("heel_height"), (int, float))
    ]
    penalty = 0.1 * len([height for height in heels if height >= 4])
    return round(sum(values) / len(values) - penalty, 4)


def _expressive_bonus(candidate: OutfitCandidate) -> float:
    """Reward visible contrast and clothes that do not get enough wear."""
    colours = [value for value in candidate.colours if compat.normalise_colour(value)]
    contrast = 0.0
    pairs = 0
    for index, first in enumerate(colours):
        for second in colours[index + 1:]:
            left, right = compat.normalise_colour(first), compat.normalise_colour(second)
            pairs += 1
            if left and right and not compat.is_neutral(left) and not compat.is_neutral(right):
                # A visible statement needs real separation, not two shades of
                # the same thing. 105 degrees is where triadic contrast starts.
                contrast += 1.0 if compat.hue_distance(left, right) >= 105 else 0.35
    contrast = contrast / pairs if pairs else 0.0
    rows = candidate.owned_items()
    unworn = sum(1 for row in rows if row.item.usage_count == 0)
    freshness = unworn / len(rows) if rows else 0.0
    return round(0.65 * contrast + 0.35 * freshness, 4)


VARIANT_SCORERS = {
    VARIANT_RECOMMENDED: lambda candidate: candidate.total_score,
    VARIANT_COMFORTABLE: lambda candidate: round(0.55 * candidate.total_score + 0.45 * _comfort_bonus(candidate), 4),
    VARIANT_EXPRESSIVE: lambda candidate: round(0.55 * candidate.total_score + 0.45 * _expressive_bonus(candidate), 4),
}


# --- Deterministic wording --------------------------------------------------
# Every look gets real, specific text built from its own computed factors before
# a model is ever involved. If the model is unavailable, or says something
# unusable, this is what the user sees — the look and its reasoning both stand
# on their own.


def _item_phrase(rows: Sequence[ScoredItem]) -> str:
    names = [row.item.display_name for row in rows]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def deterministic_why(candidate: OutfitCandidate, context: StyleContext, variant: str) -> str:
    parts: list[str] = []
    core = _item_phrase(candidate.clothing)
    if core:
        parts.append(f"{core} is {VARIANT_INTENT[variant]} for {context.occasion.label.lower()}.")
    if candidate.shoes is not None:
        parts.append(f"{candidate.shoes.item.display_name} was chosen to go with it.")
    strongest = candidate.cohesion_reasons[0] if candidate.cohesion_reasons else None
    if strongest:
        parts.append(strongest)
    if candidate.accessories:
        parts.append(f"{_item_phrase(candidate.accessories)} finishes it without competing with the rest.")
    if candidate.optional_additions:
        parts.append(
            "One part of this look is not in your confirmed inventory and is marked as an optional addition."
        )
    parts.append("Everything marked as owned comes from items you have confirmed you own.")
    return " ".join(parts)


def deterministic_weather_note(candidate: OutfitCandidate, context: StyleContext) -> str:
    if not context.weather:
        return "No weather was given for this occasion, so nothing was ruled in or out on that basis."
    rows = candidate.owned_items()
    scored = [(row, row.factors.get("weather", compat.NEUTRAL)) for row in rows]
    weakest = min(scored, key=lambda pair: pair[1], default=None)
    if weakest and weakest[1] < 0.45:
        return f"For {context.weather} weather: {weakest[0].reasons.get('weather', '')}".strip()
    strong = [row for row, score in scored if score >= 0.85]
    if strong:
        return f"Suits {context.weather} weather. {strong[0].reasons.get('weather', '')}".strip()
    return f"Nothing in this look is recorded as a problem in {context.weather} weather."


def deterministic_dress_code_note(candidate: OutfitCandidate, context: StyleContext) -> str:
    target = context.target_formality
    levels = [
        compat.item_formality(row.item.details)
        for row in candidate.clothing
    ]
    known = [level for level in levels if level is not None]
    code = (context.dress_code or "").replace("_", " ")
    if not known:
        return f"No formality is recorded on this clothing, so it was not checked against the {code} dress code."
    average = sum(known) / len(known)
    if abs(average - target) < 0.5:
        return f"This sits at the {code} level the occasion calls for."
    if average > target:
        return f"This is slightly dressier than {code}, which is the safer direction to be wrong in."
    return f"This is a little more relaxed than {code}. Add a layer or a smarter shoe if the room is formal."


def deterministic_preparation(candidate: OutfitCandidate, context: StyleContext) -> list[str]:
    steps: list[str] = []
    laundry = [
        row.item.display_name
        for row in candidate.clothing
        if str(row.item.details.get("laundry_state") or "").lower() in ("dirty", "needs wash", "in the wash", "unwashed")
    ]
    if laundry:
        steps.append(f"Wash or clean {_item_phrase([row for row in candidate.clothing if row.item.display_name in laundry])} before the day.")
    if any(row.item.condition in ("worn", "needs_attention") for row in candidate.owned_items()):
        steps.append("Check the items marked as worn — a quick press or polish makes the difference.")
    if candidate.optional_additions:
        steps.append("Decide whether you want to add the optional item, or wear the look without it.")
    if context.occasion_record.preparation_time:
        steps.append(f"You said you have {context.occasion_record.preparation_time} to get ready; lay this out the night before.")
    steps.append("Try the full look on once before the day so there are no surprises.")
    return steps[:6]


def confidence_for(candidate: OutfitCandidate, context: StyleContext) -> float:
    """How much this look is actually supported by recorded facts.

    Confidence goes down with missing information, not up with certainty of
    tone. It is a statement about the data, not about how good the look is.
    """
    value = candidate.total_score
    value -= 0.05 * min(len(context.missing_information), 4)
    value -= 0.12 * len([add for add in candidate.optional_additions if add.slot in context.occasion.required_slots])
    rows = candidate.owned_items()
    if rows:
        unscored = sum(
            1 for row in rows
            if row.factors.get("weather") == compat.NEUTRAL and row.factors.get("occasion") == compat.NEUTRAL
        )
        value -= 0.10 * (unscored / len(rows))
    return round(max(0.2, min(0.95, value)), 4)


def rank(context: StyleContext, candidates: Sequence[OutfitCandidate]) -> list[RankedLook]:
    """Stage 4 and 6: choose up to three genuinely different looks."""
    chosen: list[tuple[str, OutfitCandidate, float]] = []
    picked: list[OutfitCandidate] = []

    for variant in (VARIANT_RECOMMENDED, VARIANT_COMFORTABLE, VARIANT_EXPRESSIVE):
        scorer = VARIANT_SCORERS[variant]
        ordered = sorted(candidates, key=lambda row: (-scorer(row), row.signature()))
        for candidate in ordered:
            if not candidate.owned_items():
                continue
            if is_distinct(candidate, picked):
                chosen.append((variant, candidate, scorer(candidate)))
                picked.append(candidate)
                break

    return [
        RankedLook(
            variant=variant,
            title=VARIANT_TITLES[variant],
            candidate=candidate,
            score=score,
            confidence=confidence_for(candidate, context),
            why_it_works=deterministic_why(candidate, context, variant),
            weather_note=deterministic_weather_note(candidate, context),
            dress_code_note=deterministic_dress_code_note(candidate, context),
            preparation_steps=deterministic_preparation(candidate, context),
            missing_information=list(context.missing_information),
        )
        for variant, candidate, score in chosen
    ]
