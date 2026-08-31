"""Step 1: what was done to the food before it reached the packet.

NOVA classifies by **degree of processing**, not by nutrient content, which is
exactly why it goes first. A biscuit made of refined flour, palm oil, invert
syrup and an emulsifier is a different kind of thing from dal, and no amount of
good numbers should let it claim otherwise.

Group 4 is detected by marker: a substance that only exists because a factory
put it there. The marker list is the one the product specifies, expanded into
the words Indian labels actually print.

Group 3 is detected by addition: a whole food with a culinary ingredient —
salt, sugar, oil, jaggery — added to it. That definition is doing real work.
Paneer whose label reads "milk, citric acid" has had nothing added to it in
that sense and stays in group 1, while bread, which is flour plus salt plus
sugar plus oil, does not.

Source for the classification itself: Monteiro CA et al., "Ultra-processed
foods: what they are and how to identify them", Public Health Nutrition 22(5),
2019. The marker vocabulary below is our own mapping onto Indian label wording,
and is stated as such rather than attributed to the authors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.domains.nutrition.food_reference import MONTEIRO_NOVA_2019, Source

NOVA_UNPROCESSED = 1
NOVA_CULINARY_INGREDIENT = 2
NOVA_PROCESSED = 3
NOVA_ULTRA_PROCESSED = 4

#: The published classification behind every processing finding.
#:
#: The ``Source`` itself, not its name: the processing gate lowers grades, and
#: a lowering factor has to show the customer something they can open.
NOVA_SOURCE = MONTEIRO_NOVA_2019

#: Group 4 markers, keyed by the family the product named. The values are the
#: label wordings that mean the same thing on an Indian pack.
NOVA4_MARKERS: dict[str, tuple[str, ...]] = {
    "maltodextrin": ("maltodextrin", "malto dextrin"),
    "invert syrup": ("invert syrup", "invert sugar syrup", "invert sugar"),
    "high-fructose corn syrup": (
        "high fructose corn syrup", "hfcs", "high maltose corn syrup",
        "corn syrup", "glucose syrup", "liquid glucose",
    ),
    "protein isolate": ("protein isolate", "isolated protein", "soy protein isolate",
                        "whey protein isolate", "milk protein isolate"),
    "hydrolysed protein": ("hydrolysed protein", "hydrolyzed protein", "protein hydrolysate",
                           "hydrolysed vegetable protein", "textured vegetable protein"),
    "hydrogenated or interesterified oil": (
        "hydrogenated", "partially hydrogenated", "interesterified", "vanaspati",
    ),
    "emulsifier": ("emulsifier", "emulsifiers", "ins 471", "ins 322", "ins 322(i)",
                   "ins 481", "ins 322i", "soy lecithin", "soya lecithin",
                   "mono and diglycerides", "mono- and diglycerides"),
    "thickener": ("thickener", "thickeners", "stabiliser", "stabilizer", "ins 415",
                  "ins 412", "ins 407", "guar gum", "xanthan gum", "carrageenan",
                  "modified starch", "modified corn starch"),
    "humectant": ("humectant", "humectants", "ins 422", "glycerol", "propylene glycol",
                  "ins 1520"),
    "anti-caking agent": ("anticaking", "anti caking", "anti-caking", "ins 551",
                          "ins 554", "silicon dioxide"),
    "artificial colour": ("artificial colour", "artificial color", "synthetic colour",
                          "synthetic food colour", "ins 102", "ins 110", "ins 122",
                          "ins 129", "ins 133", "ins 143", "tartrazine",
                          "sunset yellow", "carmoisine", "allura red", "brilliant blue"),
    "artificial flavour": ("artificial flavour", "artificial flavor", "artificial flavouring",
                           "nature identical flavouring", "nature-identical flavouring",
                           "added flavour", "added flavor", "synthetic flavour"),
    "flavour enhancer": ("flavour enhancer", "flavor enhancer", "ins 621", "ins 627",
                         "ins 631", "ins 635", "monosodium glutamate", "msg"),
}

#: Adding one of these to a whole food is what makes it group 3.
CULINARY_ADDITIONS: tuple[str, ...] = (
    "salt", "iodised salt", "iodized salt", "namak", "sugar", "cane sugar",
    "refined sugar", "jaggery", "gur", "honey", "oil", "edible vegetable oil",
    "palm oil", "palmolein", "refined palmolein", "sunflower oil", "soybean oil",
    "rice bran oil", "groundnut oil", "mustard oil", "coconut oil", "butter", "ghee",
)

#: Words that mean "a whole food went in", used to keep group 1 honest.
_WHOLE_FOOD_HINTS: tuple[str, ...] = (
    "milk", "curd", "dahi", "dal", "gram", "chana", "rice", "wheat", "atta",
    "water", "culture", "cultures", "yeast", "fruit", "vegetable", "nut",
    "almond", "cashew", "peanut", "groundnut", "spice", "spices", "citric acid",
)


@dataclass(frozen=True)
class NovaResult:
    """Which group, and the exact words that put it there."""

    group: int
    #: Marker family -> the label wording that matched it.
    matched_markers: tuple[tuple[str, str], ...]
    #: Culinary additions found in the ingredient list.
    culinary_additions: tuple[str, ...]
    reason: str
    source: Source = NOVA_SOURCE


def normalise(text: str) -> str:
    """Lowercase, strip punctuation runs, collapse whitespace."""
    return " ".join(re.sub(r"[^a-z0-9%.]+", " ", (text or "").lower()).split())


def _contains(haystack: str, needle: str) -> bool:
    """Whole-token containment, so 'oil' does not match 'boiled'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def find_nova4_markers(ingredients: list[str]) -> tuple[tuple[str, str], ...]:
    """Every group-4 marker present, as (family, matched wording)."""
    joined = normalise(" ; ".join(ingredients))
    found: list[tuple[str, str]] = []
    for family, wordings in NOVA4_MARKERS.items():
        for wording in wordings:
            if _contains(joined, wording):
                found.append((family, wording))
                break
    return tuple(found)


def find_culinary_additions(ingredients: list[str]) -> tuple[str, ...]:
    found: list[str] = []
    for raw in ingredients:
        text = normalise(raw)
        for addition in CULINARY_ADDITIONS:
            if _contains(text, addition) and addition not in found:
                found.append(addition)
    return tuple(found)


def classify(ingredients: list[str], *, is_culinary_product: bool = False) -> NovaResult:
    """Classify one product from its ingredient list."""
    if is_culinary_product:
        return NovaResult(
            group=NOVA_CULINARY_INGREDIENT,
            matched_markers=(),
            culinary_additions=(),
            reason="The product is itself a culinary ingredient.",
        )

    markers = find_nova4_markers(ingredients)
    if markers:
        names = ", ".join(sorted({wording for _, wording in markers}))
        return NovaResult(
            group=NOVA_ULTRA_PROCESSED,
            matched_markers=markers,
            culinary_additions=find_culinary_additions(ingredients),
            reason=f"The ingredient list contains {names}.",
        )

    additions = find_culinary_additions(ingredients)
    if additions:
        return NovaResult(
            group=NOVA_PROCESSED,
            matched_markers=(),
            culinary_additions=additions,
            reason=f"A whole food with {', '.join(additions)} added.",
        )

    return NovaResult(
        group=NOVA_UNPROCESSED,
        matched_markers=(),
        culinary_additions=(),
        reason="Whole foods only; nothing has been added.",
    )


__all__ = [
    "CULINARY_ADDITIONS",
    "NOVA4_MARKERS",
    "NOVA_CULINARY_INGREDIENT",
    "NOVA_PROCESSED",
    "NOVA_SOURCE",
    "NOVA_ULTRA_PROCESSED",
    "NOVA_UNPROCESSED",
    "NovaResult",
    "classify",
    "find_culinary_additions",
    "find_nova4_markers",
    "normalise",
]
