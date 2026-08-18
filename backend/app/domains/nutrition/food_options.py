"""Small, immutable catalogue of descriptive ordinary-food ideas.

This is application reference data, not a nutrient or composition database.
"""
from __future__ import annotations

import string
from collections.abc import Iterable
from dataclasses import dataclass

from app.domains.nutrition.preferences import SUPPORTED_DIETS

NUTRITION_FOOD_OPTIONS_VERSION = "v3-04.2"


@dataclass(frozen=True, slots=True)
class NutritionFoodOption:
    option_id: str
    label: str
    kind: str
    priority: int
    compatible_diets: tuple[str, ...]
    avoid_aliases: tuple[str, ...] = ()


_ALL_DIETS = tuple(SUPPORTED_DIETS)
_VEGETARIAN_DIETS = ("vegetarian", "jain", "eggetarian", "pescatarian", "non_vegetarian")
_EGG_DIETS = ("eggetarian", "pescatarian", "non_vegetarian")
_FISH_DIETS = ("pescatarian", "non_vegetarian")
_MEAT_DIETS = ("non_vegetarian",)

BALANCED_VARIETY_OPTIONS: tuple[NutritionFoodOption, ...] = (
    NutritionFoodOption("variety.vegetables", "Vegetables", "food_group", 10, _ALL_DIETS, ("vegetable", "vegetables")),
    NutritionFoodOption("variety.fruit", "Fruit", "food_group", 20, _ALL_DIETS, ("fruit", "fruits")),
    NutritionFoodOption("variety.pulses_legumes", "Pulses / legumes", "food_group", 30, _ALL_DIETS, ("pulse", "pulses", "legume", "legumes")),
    NutritionFoodOption("variety.grains_millets", "Grains / millets", "food_group", 40, _ALL_DIETS, ("grain", "grains", "millet", "millets", "cereal", "cereals")),
    NutritionFoodOption("variety.nuts_seeds", "Nuts / seeds", "food_group", 50, _ALL_DIETS, ("nut", "nuts", "seed", "seeds")),
)

PROTEIN_FOOD_OPTIONS: tuple[NutritionFoodOption, ...] = (
    NutritionFoodOption("protein.dal", "Dal", "ordinary_food", 10, _ALL_DIETS, ("dal", "lentil", "lentils")),
    NutritionFoodOption("protein.chana", "Chana", "ordinary_food", 20, _ALL_DIETS, ("chana", "chickpea", "chickpeas", "chick pea", "chick peas")),
    NutritionFoodOption("protein.dahi", "Dahi / curd", "ordinary_food", 30, _VEGETARIAN_DIETS, ("dahi", "curd", "yogurt", "yoghurt", "dairy", "milk")),
    NutritionFoodOption("protein.eggs", "Eggs", "ordinary_food", 40, _EGG_DIETS, ("egg", "eggs")),
    NutritionFoodOption("protein.soy_foods", "Soy foods", "ordinary_food", 50, _ALL_DIETS, ("soy", "soya", "tofu", "soy food", "soy foods", "soya food", "soya foods")),
    NutritionFoodOption("protein.fish", "Fish", "ordinary_food", 60, _FISH_DIETS, ("fish", "seafood")),
    NutritionFoodOption("protein.paneer", "Paneer", "ordinary_food", 70, _VEGETARIAN_DIETS, ("paneer", "dairy", "milk")),
    NutritionFoodOption("protein.chicken", "Chicken", "ordinary_food", 80, _MEAT_DIETS, ("chicken", "poultry", "meat")),
    NutritionFoodOption("protein.rajma", "Rajma", "ordinary_food", 90, _ALL_DIETS, ("rajma", "kidney bean", "kidney beans")),
    NutritionFoodOption("protein.peanuts", "Peanuts", "ordinary_food", 100, _ALL_DIETS, ("peanut", "peanuts", "groundnut", "groundnuts")),
)

# Descriptive aliases keep the catalogue easy to discover without creating a
# second registry.
BALANCED_VARIETY_FOOD_OPTIONS = BALANCED_VARIETY_OPTIONS
PROTEIN_FOOD_FIRST_OPTIONS = PROTEIN_FOOD_OPTIONS

FOOD_OPTIONS_BY_RULE: dict[str, tuple[NutritionFoodOption, ...]] = {
    "nutrition.pattern.balanced_variety": BALANCED_VARIETY_OPTIONS,
    "nutrition.pattern.protein_food_first": PROTEIN_FOOD_OPTIONS,
    "nutrition.pattern.hydration_context": (),
}

_BROAD_PROTEIN_GROUPS = {
    "legume": {"protein.dal", "protein.chana", "protein.rajma", "protein.soy_foods"},
    "legumes": {"protein.dal", "protein.chana", "protein.rajma", "protein.soy_foods"},
    "pulse": {"protein.dal", "protein.chana", "protein.rajma"},
    "pulses": {"protein.dal", "protein.chana", "protein.rajma"},
    "nut": {"protein.peanuts"},
    "nuts": {"protein.peanuts"},
    "dairy": {"protein.dahi", "protein.paneer"},
    "milk": {"protein.dahi", "protein.paneer"},
}


def normalize_avoid_term(value: str) -> str:
    text = str(value).casefold().strip().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    punctuation = string.punctuation.replace("_", "").replace("-", "")
    return text.strip(punctuation)


def _excluded_ids(avoid_foods: Iterable[str]) -> set[str]:
    excluded: set[str] = set()
    aliases = {alias: option.option_id for option in (*BALANCED_VARIETY_OPTIONS, *PROTEIN_FOOD_OPTIONS) for alias in option.avoid_aliases}
    for raw in avoid_foods or ():
        term = normalize_avoid_term(raw)
        excluded.update(_BROAD_PROTEIN_GROUPS.get(term, set()))
        option_id = aliases.get(term)
        if option_id:
            excluded.add(option_id)
    return excluded


def options_for_rule(rule_id: str, *, diet: str | None, avoid_foods: Iterable[str] = ()) -> tuple[NutritionFoodOption, ...]:
    """Resolve only the fixed options attached to an already-supported rule."""
    options = FOOD_OPTIONS_BY_RULE.get(rule_id, ())
    if diet not in SUPPORTED_DIETS:
        return ()
    excluded = _excluded_ids(avoid_foods)
    selected = [row for row in options if diet in row.compatible_diets and row.option_id not in excluded]
    selected.sort(key=lambda row: (row.priority, row.option_id))
    if rule_id == "nutrition.pattern.protein_food_first":
        selected = selected[:8]
    return tuple(selected)


resolve_food_options = options_for_rule
food_options_for_rule = options_for_rule


__all__ = [
    "BALANCED_VARIETY_OPTIONS",
    "BALANCED_VARIETY_FOOD_OPTIONS",
    "FOOD_OPTIONS_BY_RULE",
    "NUTRITION_FOOD_OPTIONS_VERSION",
    "NutritionFoodOption",
    "PROTEIN_FOOD_OPTIONS",
    "PROTEIN_FOOD_FIRST_OPTIONS",
    "food_options_for_rule",
    "normalize_avoid_term",
    "options_for_rule",
    "resolve_food_options",
]
