"""Deterministic compatibility scoring.

Everything in this module is a pure function of stored facts. No model is
called, nothing is random, and the same inputs always produce the same score.
That matters for two reasons: the ranking has to be explainable to a user who
asks "why this and not that", and a test has to be able to assert on it.

Every score is a float in ``[0, 1]``. When an input is missing, the function
returns a neutral ``0.5`` and names the missing field rather than guessing —
a confident score built on an absent fabric or colour would be a fabrication.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Optional

from app.domains.recommendation.occasions import DRESS_CODES

NEUTRAL = 0.5

# --- Colour -----------------------------------------------------------------
# Real hue angles in degrees, not evenly spaced name-slots. Even spacing looks
# tidier but is wrong: the eye does not give green-through-blue the same span as
# red-through-yellow, and slotting them evenly made red-and-teal — a pairing
# this product has recommended since V1 — score as a clash. Degrees keep the
# classic relationships (monochrome, analogous, triadic, complementary) where
# they actually fall.
COLOUR_HUES: dict[str, int] = {
    "red": 0, "scarlet": 5, "crimson": 348, "cherry": 350, "maroon": 345, "burgundy": 345, "wine": 340,
    "coral": 16, "terracotta": 18, "rust": 20, "peach": 28, "orange": 30, "tangerine": 30, "apricot": 32,
    "amber": 45, "ochre": 42, "mustard": 48, "gold": 50, "saffron": 45, "yellow": 55, "lemon": 58,
    "lime": 80, "chartreuse": 80, "olive green": 80, "moss": 95,
    "sage": 110, "green": 120, "forest": 130, "bottle green": 135, "emerald": 145, "mint": 150,
    "sea green": 160, "jade": 165, "turquoise": 175, "aqua": 175, "teal": 180, "cyan": 185,
    "sky": 200, "powder blue": 205, "ice blue": 200, "azure": 210, "cerulean": 208, "cobalt": 215,
    "blue": 220, "royal blue": 225, "sapphire": 220, "denim blue": 215, "electric blue": 210,
    "periwinkle": 250, "indigo": 260, "violet": 270, "lavender": 275, "lilac": 278,
    "purple": 285, "mauve": 295, "plum": 300, "aubergine": 300, "magenta": 300, "fuchsia": 310,
    "pink": 330, "rani pink": 325, "rose": 340, "blush": 350,
}

# Neutrals sit outside the wheel: they read as a background to whatever they are
# worn with, so they pair broadly rather than by hue relationship.
NEUTRALS = {
    "black", "white", "off-white", "ivory", "cream", "beige", "sand", "stone", "oatmeal",
    "grey", "gray", "charcoal", "slate", "silver", "taupe", "tan", "camel", "khaki",
    "brown", "chocolate", "coffee", "navy", "midnight", "ecru", "nude", "denim",
}

# Angular separation in degrees -> (score, relationship). Ordered bands; the
# first band whose upper bound the separation falls under wins.
_HUE_BANDS = (
    (15, 0.80, "is a single-colour pairing"),                       # monochrome
    (45, 0.78, "sit next to each other on the colour wheel"),       # analogous
    (75, 0.62, "are near neighbours on the colour wheel"),
    (105, 0.50, "sit at an awkward distance on the colour wheel"),
    (135, 0.70, "are evenly spaced, which balances them"),          # triadic
    (165, 0.78, "sit far enough apart to contrast cleanly"),
    (181, 0.84, "are opposites on the colour wheel, so they lift each other"),
)


def normalise_colour(value: Any) -> Optional[str]:
    """Reduce a stored colour string to something the wheel can look up."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.lower().split())
    if not text:
        return None
    if text in NEUTRALS or text in COLOUR_HUES:
        return text
    # "dark teal", "light olive green", "pastel pink" all resolve to their base.
    words = text.split()
    for size in (3, 2, 1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if phrase in COLOUR_HUES or phrase in NEUTRALS:
                return phrase
    return None


def is_neutral(colour: Optional[str]) -> bool:
    return colour is not None and colour in NEUTRALS


def hue_distance(a: str, b: str) -> int:
    """Separation between two named hues, in degrees (0-180)."""
    raw = abs(COLOUR_HUES[a] - COLOUR_HUES[b])
    return min(raw, 360 - raw)


def colour_compatibility(first: Any, second: Any) -> tuple[float, str]:
    """How well two colours sit together, with the reason in plain English."""
    left, right = normalise_colour(first), normalise_colour(second)
    if left is None or right is None:
        return NEUTRAL, "Colour is not recorded on one of these items, so this is not scored."
    if is_neutral(left) and is_neutral(right):
        if {left, right} == {"black", "navy"}:
            return 0.62, "Black with navy is a close call in daylight, but it works indoors."
        return 0.86, f"{left.title()} and {right} are both neutrals, so they always sit together."
    if is_neutral(left) or is_neutral(right):
        neutral, colour = (left, right) if is_neutral(left) else (right, left)
        return 0.88, f"{neutral.title()} is a neutral, so it lets the {colour} lead."
    distance = hue_distance(left, right)
    for upper, score, relationship in _HUE_BANDS:
        if distance < upper:
            return score, f"{left.title()} and {right} {relationship}."
    # Unreachable: the last band covers everything up to 180 degrees.
    return NEUTRAL, f"{left.title()} and {right} were not scored."


def palette_fit(colour: Any, favourites: Sequence[str], disliked: Sequence[str]) -> tuple[float, str]:
    """How the user's own stated colour preferences see this colour."""
    normalised = normalise_colour(colour)
    if normalised is None:
        return NEUTRAL, "Colour is not recorded, so your colour preferences were not applied."
    liked = {normalise_colour(value) for value in favourites}
    avoided = {normalise_colour(value) for value in disliked}
    if normalised in avoided:
        return 0.0, f"You told us {normalised} is a colour to avoid."
    if normalised in liked:
        return 1.0, f"{normalised.title()} is one of your favourite colours."
    return 0.6, f"{normalised.title()} is not on either of your colour lists."


# --- Formality --------------------------------------------------------------
# Free-text formality on an item is mapped onto the same 1-5 scale the occasions
# use, so a wardrobe item and an event can be compared directly.
FORMALITY_WORDS: dict[str, int] = {
    "loungewear": 1, "sleepwear": 1, "nightwear": 1, "home": 1,
    "sportswear": 1, "activewear": 1, "athleisure": 1, "gym": 1,
    "casual": 2, "everyday": 2, "relaxed": 2, "weekend": 2,
    "smart casual": 3, "smart-casual": 3, "semi formal": 3, "semi-formal": 3, "dressy casual": 3,
    "business casual": 4, "business-casual": 4, "workwear": 4, "office": 4,
    "festive": 4, "traditional": 4, "ethnic": 4, "occasion wear": 4,
    "formal": 5, "business formal": 5, "black tie": 5, "evening": 5, "bridal": 5,
}


def item_formality(details: dict[str, Any]) -> Optional[int]:
    """The 1-5 formality of an inventory item, or None when unrecorded."""
    raw = details.get("formality")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(1, min(5, int(raw)))
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.lower().split())
    if text in FORMALITY_WORDS:
        return FORMALITY_WORDS[text]
    if text in DRESS_CODES:
        return DRESS_CODES[text]
    for word, level in FORMALITY_WORDS.items():
        if word in text:
            return level
    return None


def formality_match(level: Optional[int], target: int) -> tuple[float, str]:
    """How close an item's formality is to what the occasion asks for.

    Under-dressing is penalised harder than over-dressing: turning up too casual
    for an interview costs more than turning up a little too smart.
    """
    if level is None:
        return NEUTRAL, "This item has no formality recorded, so it was not scored on dress code."
    gap = level - target
    if gap == 0:
        return 1.0, "Matches the formality this occasion calls for."
    if gap > 0:
        return max(0.0, 1.0 - 0.18 * gap), f"A little dressier than needed (by {gap} level{'s' if gap > 1 else ''})."
    short = -gap
    return max(0.0, 1.0 - 0.30 * short), f"Dressed down by {short} level{'s' if short > 1 else ''} against the dress code."


# --- Weather and climate ----------------------------------------------------
HOT_CONDITIONS = {"hot", "humid", "warm"}
COLD_CONDITIONS = {"cold", "cool"}
WET_CONDITIONS = {"rainy", "humid"}

SEASON_FOR_CONDITION: dict[str, str] = {
    "hot": "summer", "warm": "summer", "humid": "monsoon", "rainy": "monsoon",
    "mild": "spring", "windy": "autumn", "cool": "autumn", "cold": "winter",
}

# Fabrics that behave predictably in a given condition. Kept small and honest:
# only fabrics where the guidance is genuinely uncontroversial.
BREATHABLE_FABRICS = {"cotton", "linen", "khadi", "chambray", "muslin", "rayon", "viscose", "seersucker"}
WARM_FABRICS = {"wool", "tweed", "velvet", "corduroy", "fleece", "cashmere", "flannel", "leather"}
WET_WEATHER_RISKY = {"silk", "suede", "velvet", "linen"}


def _contains(values: Any, needle: str) -> bool:
    if isinstance(values, str):
        return needle in values.lower()
    if isinstance(values, (list, tuple)):
        return any(isinstance(v, str) and needle in v.lower() for v in values)
    return False


def weather_suitability(details: dict[str, Any], condition: Optional[str]) -> tuple[float, str]:
    """How well an item copes with the expected weather."""
    if not condition:
        return NEUTRAL, "No weather was given, so nothing was ruled in or out on that basis."
    fabric = str(details.get("fabric") or "").lower()
    seasons = details.get("season") or []
    declared = details.get("weather_suitability") or []
    wanted_season = SEASON_FOR_CONDITION.get(condition)

    if _contains(declared, condition):
        return 1.0, f"You recorded this item as suitable for {condition} weather."
    if wanted_season and _contains(seasons, wanted_season):
        return 0.92, f"You recorded this item for {wanted_season}, which fits {condition} weather."

    if condition in HOT_CONDITIONS and fabric:
        if any(word in fabric for word in BREATHABLE_FABRICS):
            return 0.9, f"{fabric.title()} breathes well in {condition} weather."
        if any(word in fabric for word in WARM_FABRICS):
            return 0.2, f"{fabric.title()} traps heat, which is hard work in {condition} weather."
    if condition in COLD_CONDITIONS and fabric:
        if any(word in fabric for word in WARM_FABRICS):
            return 0.9, f"{fabric.title()} holds warmth for {condition} weather."
        if any(word in fabric for word in BREATHABLE_FABRICS):
            return 0.42, f"{fabric.title()} is light for {condition} weather — plan a layer over it."
    if condition in WET_CONDITIONS and fabric and any(word in fabric for word in WET_WEATHER_RISKY):
        return 0.3, f"{fabric.title()} marks easily in {condition} weather."
    if seasons or declared or fabric:
        return 0.6, "Nothing recorded rules this out for the expected weather."
    return NEUTRAL, "No fabric or season is recorded, so weather was not scored for this item."


# --- Occasion relevance -----------------------------------------------------

def occasion_relevance(details: dict[str, Any], occasion_key: str, label: str) -> tuple[float, str]:
    """Whether the user themselves tagged this item for this kind of event."""
    tagged = details.get("occasion") or []
    if _contains(tagged, occasion_key.replace("_", " ")) or _contains(tagged, label.lower()):
        return 1.0, f"You tagged this item for {label.lower()}."
    if tagged:
        return 0.55, "You tagged this item for other occasions."
    return NEUTRAL, "No occasion tags recorded on this item."


# --- Fit and comfort --------------------------------------------------------

def fit_alignment(details: dict[str, Any], fit_prefs: dict[str, Any]) -> tuple[float, list[str]]:
    """How the item sits against the fit preferences the user confirmed.

    Returns the score and any concrete fit risks worth telling the user about.
    Nothing here comments on the user's body — only on the garment and on
    preferences they typed themselves.
    """
    score = 0.7
    risks: list[str] = []
    silhouette = str(details.get("fit") or "").lower()
    avoided = [str(v).lower() for v in (fit_prefs.get("silhouettes_avoided") or [])]
    liked = [str(v).lower() for v in (fit_prefs.get("silhouettes_liked") or [])]
    if silhouette:
        if any(word and word in silhouette for word in avoided):
            score = 0.15
            risks.append(f"You listed {silhouette} as a silhouette you avoid.")
        elif any(word and word in silhouette for word in liked):
            score = 1.0
        else:
            score = 0.72
    size = str(details.get("size") or "").strip().lower()
    usual = [str(fit_prefs.get(key) or "").strip().lower() for key in ("usual_top_size", "usual_bottom_size")]
    usual = [value for value in usual if value]
    if size and usual and size not in usual:
        score = min(score, 0.55)
        risks.append(f"Recorded size {size.upper()} is not one of your usual sizes.")
    if not size:
        risks.append("No size recorded on this item.")
    return score, risks


def comfort_alignment(details: dict[str, Any], comfort_preference: Optional[str]) -> tuple[float, str]:
    """Weigh an item by how comfortable the user said they need to be."""
    if not comfort_preference:
        return NEUTRAL, "No comfort preference given."
    comfort = str(details.get("comfort") or "").lower()
    heel = details.get("heel_height")
    if comfort_preference == "comfort_first":
        if any(word in comfort for word in ("all day", "very comfortable", "comfortable", "cushioned")):
            return 1.0, "You recorded this as comfortable for long wear."
        if isinstance(heel, (int, float)) and float(heel) >= 5:
            return 0.2, f"A {float(heel):g} cm heel is a lot when you asked for comfort first."
        return 0.6, "Nothing recorded suggests this is uncomfortable."
    if comfort_preference == "polish_first":
        if isinstance(heel, (int, float)) and float(heel) >= 3:
            return 0.9, "The heel adds the polish you asked for."
        return 0.65, "A polished look was requested; this is a quieter choice."
    return 0.7, "Balanced comfort was requested."


# --- Aggregation ------------------------------------------------------------
# The weights that turn per-factor scores into one item score. They are declared
# here, in one place, because the report has to document them and the tests
# assert against them.
ITEM_WEIGHTS: dict[str, float] = {
    "formality": 0.28,
    "occasion": 0.20,
    "weather": 0.18,
    "palette": 0.14,
    "fit": 0.12,
    "comfort": 0.08,
}


def weighted(scores: dict[str, float], weights: dict[str, float] = ITEM_WEIGHTS) -> float:
    """Weighted mean over whichever factors were actually scored."""
    total = sum(weights[key] for key in scores if key in weights)
    if total <= 0:
        return NEUTRAL
    return round(sum(scores[key] * weights[key] for key in scores if key in weights) / total, 4)


def cohesion(colours: Iterable[Any]) -> tuple[float, list[str]]:
    """How well a set of colours works as one outfit."""
    values = [value for value in colours if normalise_colour(value) is not None]
    if len(values) < 2:
        return NEUTRAL, ["Not enough recorded colours to judge how the outfit sits together."]
    scores: list[float] = []
    reasons: list[str] = []
    for index, first in enumerate(values):
        for second in values[index + 1:]:
            score, reason = colour_compatibility(first, second)
            scores.append(score)
            reasons.append(reason)
    return round(sum(scores) / len(scores), 4), reasons
