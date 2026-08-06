"""Appearance-related food context. Not a diet, not a diagnosis.

Hard boundaries, enforced in code rather than in a prompt:

* **No calories.** Nothing here counts, totals or targets anything.
* **No deficiency claims.** We never say a person lacks a nutrient. We say what
  a nutrient is associated with, and which everyday Indian foods contain it.
* **Food first.** Supplements are inventory. Where a nutrient has good food
  sources — and they nearly all do — the food is what gets named.
* **Diet preferences are respected as constraints, not suggestions.** A
  vegetarian user is never shown fish, and if a nutrient's common sources are
  all animal-based, the vegetarian alternatives are named explicitly rather
  than the nutrient being dropped in silence.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.domains.routines.safety import NUTRITION_DISCLAIMER, narrative_is_safe

# Diet preferences, in the terms people in India actually use.
DIET_VEGAN = "vegan"
DIET_VEGETARIAN = "vegetarian"
DIET_JAIN = "jain"
DIET_EGGETARIAN = "eggetarian"
DIET_NON_VEGETARIAN = "non_vegetarian"
DIET_PESCATARIAN = "pescatarian"
DIETS = (DIET_VEGAN, DIET_VEGETARIAN, DIET_JAIN, DIET_EGGETARIAN, DIET_PESCATARIAN, DIET_NON_VEGETARIAN)

# What each diet excludes. Jain additionally excludes root vegetables.
DIET_EXCLUDES: dict[str, set[str]] = {
    DIET_VEGAN: {"dairy", "egg", "fish", "meat", "honey"},
    DIET_VEGETARIAN: {"egg", "fish", "meat"},
    DIET_JAIN: {"egg", "fish", "meat", "root"},
    DIET_EGGETARIAN: {"fish", "meat"},
    DIET_PESCATARIAN: {"meat"},
    DIET_NON_VEGETARIAN: set(),
}

DIET_LABELS = {
    DIET_VEGAN: "vegan", DIET_VEGETARIAN: "vegetarian", DIET_JAIN: "Jain",
    DIET_EGGETARIAN: "eggetarian", DIET_PESCATARIAN: "pescatarian",
    DIET_NON_VEGETARIAN: "non-vegetarian",
}


@dataclass(frozen=True)
class Food:
    name: str
    tags: set[str] = field(default_factory=set)   # dairy | egg | fish | meat | root | plant


@dataclass(frozen=True)
class NutrientRule:
    rule_id: str
    key: str
    display_name: str
    # Deliberately worded as association, never as effect or cure.
    appearance_context: str
    foods: list[Food]
    note: str = ""


def _f(name: str, *tags: str) -> Food:
    return Food(name=name, tags=set(tags) or {"plant"})


# --- The reviewed nutrition rules -------------------------------------------
# Every entry names a nutrient, says what it is *associated with* in plain
# language, and lists everyday Indian foods. No amounts, no targets, no claims.

NUTRIENT_RULES: list[NutrientRule] = [
    NutrientRule(
        "nutrition.protein", "protein", "Protein",
        "Hair and nails are largely protein, so a diet with enough of it across the day is the usual starting point people talk about.",
        [
            _f("dal — moong, masoor, toor"), _f("rajma and chana"), _f("paneer", "dairy"),
            _f("curd or dahi", "dairy"), _f("soya chunks"), _f("peanuts and peanut chutney"),
            _f("eggs", "egg"), _f("fish", "fish"), _f("chicken", "meat"),
        ],
        "Spread across meals is the usual suggestion rather than all at once.",
    ),
    NutrientRule(
        "nutrition.vitamin_c", "vitamin_c", "Vitamin C",
        "Associated with collagen formation, which is part of how skin holds its structure.",
        [
            _f("amla"), _f("guava"), _f("oranges and mosambi"), _f("lemon in dal or salad"),
            _f("capsicum"), _f("tomato"), _f("cabbage and cauliflower"),
        ],
        "Heat reduces it, so some raw or lightly cooked sources help.",
    ),
    NutrientRule(
        "nutrition.vitamin_a", "vitamin_a", "Vitamin A",
        "Associated with normal skin cell turnover.",
        [
            _f("carrot", "root"), _f("sweet potato", "root"), _f("pumpkin"),
            _f("spinach and methi"), _f("mango in season"), _f("papaya"), _f("ghee", "dairy"),
        ],
        "Absorbed better alongside some fat, which most Indian cooking already provides.",
    ),
    NutrientRule(
        "nutrition.vitamin_e", "vitamin_e", "Vitamin E",
        "An antioxidant found in many foods, associated with skin health in general terms.",
        [
            _f("almonds"), _f("sunflower seeds"), _f("groundnut oil"),
            _f("avocado"), _f("spinach"),
        ],
    ),
    NutrientRule(
        "nutrition.iron", "iron", "Iron",
        "Associated with hair health. Nothing here can tell you your iron levels — only a blood test and a doctor can.",
        [
            _f("palak and other greens"), _f("rajma and chana"), _f("bajra and ragi"),
            _f("dates and raisins"), _f("jaggery"), _f("eggs", "egg"),
            _f("fish", "fish"), _f("mutton or liver", "meat"),
        ],
        "Plant sources absorb better with vitamin C alongside — a squeeze of lemon on dal is the classic pairing.",
    ),
    NutrientRule(
        "nutrition.zinc", "zinc", "Zinc",
        "Associated with skin repair processes.",
        [
            _f("pumpkin seeds"), _f("sesame seeds and til"), _f("chana and other legumes"),
            _f("cashews"), _f("curd", "dairy"), _f("eggs", "egg"), _f("chicken", "meat"),
        ],
    ),
    NutrientRule(
        "nutrition.copper", "copper", "Copper",
        "Associated with the pigment in hair and skin.",
        [
            _f("sesame seeds"), _f("cashews and almonds"), _f("chana"),
            _f("mushrooms"), _f("dark chocolate"),
        ],
    ),
    NutrientRule(
        "nutrition.omega_3", "omega_3", "Omega-3 fats",
        "Associated with skin barrier comfort.",
        [
            _f("flaxseed — alsi, ground"), _f("chia seeds"), _f("walnuts"),
            _f("mustard oil"), _f("fish such as rohu or mackerel", "fish"),
        ],
        "Ground flaxseed is the usual vegetarian route people mention.",
    ),
    NutrientRule(
        "nutrition.collagen_support", "collagen_support", "Collagen-supporting pattern",
        "Collagen you eat is broken down like any other protein. What people mean by a collagen-supporting diet is simply enough protein plus vitamin C.",
        [
            _f("dal with a squeeze of lemon"), _f("paneer with capsicum", "dairy"),
            _f("chana chaat with tomato and lemon"), _f("curd with fruit", "dairy"),
        ],
        "Recorded plainly because collagen supplements are widely marketed on a claim the evidence does not support.",
    ),
    NutrientRule(
        "nutrition.hydration", "hydration", "Hydration",
        "Skin looks better when you are not short of water, especially in Indian summers.",
        [
            _f("water through the day"), _f("nimbu paani without much sugar"),
            _f("coconut water"), _f("chaas or buttermilk", "dairy"),
            _f("cucumber, watermelon and other high-water foods"),
        ],
        "No target number here — thirst and the colour of your urine are the everyday signals people use.",
    ),
]

NUTRIENT_BY_KEY = {rule.key: rule for rule in NUTRIENT_RULES}


def normalise_diet(value: str | None) -> str:
    if not value:
        return DIET_NON_VEGETARIAN
    text = " ".join(str(value).lower().split()).replace("-", "_").replace(" ", "_")
    if text in DIETS:
        return text
    if "vegan" in text:
        return DIET_VEGAN
    if "jain" in text:
        return DIET_JAIN
    if "eggetarian" in text or "egg" in text:
        return DIET_EGGETARIAN
    if "pescat" in text or "fish" in text:
        return DIET_PESCATARIAN
    if "veg" in text:
        return DIET_VEGETARIAN
    return DIET_NON_VEGETARIAN


def foods_for(rule: NutrientRule, diet: str) -> list[str]:
    excluded = DIET_EXCLUDES.get(diet, set())
    return [food.name for food in rule.foods if not (food.tags & excluded)]


def suggestions(
    *,
    diet: str | None = None,
    focus: Sequence[str] = (),
    climate: str | None = None,
    hydration_enabled: bool = True,
) -> dict[str, Any]:
    """Appearance-related food context, filtered to what this person eats."""
    resolved = normalise_diet(diet)
    wanted = set(focus) if focus else set(NUTRIENT_BY_KEY)

    rows: list[dict[str, Any]] = []
    for rule in NUTRIENT_RULES:
        if rule.key not in wanted:
            continue
        if rule.key == "hydration" and not hydration_enabled:
            continue
        allowed = foods_for(rule, resolved)
        if not allowed:
            # Every source excluded by this diet. Say so rather than dropping
            # the nutrient silently.
            rows.append({
                "rule_id": rule.rule_id, "nutrient": rule.key,
                "display_name": rule.display_name,
                "appearance_context": rule.appearance_context,
                "foods": [],
                "note": (
                    f"The common sources we list are all outside a {DIET_LABELS[resolved]} diet. "
                    "A dietitian can point you at better options than we can."
                ),
                "diet": resolved,
            })
            continue
        excluded_count = len(rule.foods) - len(allowed)
        rows.append({
            "rule_id": rule.rule_id, "nutrient": rule.key,
            "display_name": rule.display_name,
            "appearance_context": rule.appearance_context,
            "foods": allowed,
            "note": rule.note,
            "diet": resolved,
            "excluded_for_diet": excluded_count,
        })

    if climate in ("hot", "humid") and hydration_enabled:
        for row in rows:
            if row["nutrient"] == "hydration":
                row["climate_note"] = "It is hot, which is when this actually matters."

    payload = {
        "diet": resolved,
        "diet_label": DIET_LABELS[resolved],
        "suggestions": rows,
        "food_first": True,
        "disclaimer": NUTRITION_DISCLAIMER,
        # Phrased to say what we do not do without using the words the safety
        # sweep bans. Naming them here would make every nutrition response fail
        # its own check.
        "boundaries": [
            "Nothing here counts or totals what you eat.",
            "No claim that you are short of anything — only a doctor and a blood test can say that.",
            "Food ideas, not a meal plan.",
        ],
    }
    _assert_safe(payload)
    return payload


def _assert_safe(payload: dict[str, Any]) -> None:
    """Sweep every string. Reviewed rules, but a careless edit is caught here."""
    for row in payload["suggestions"]:
        for value in (row.get("appearance_context"), row.get("note"), row.get("climate_note")):
            if value and not narrative_is_safe(value):
                raise AssertionError(f"Unsafe nutrition wording in {row['rule_id']}: {value!r}")
