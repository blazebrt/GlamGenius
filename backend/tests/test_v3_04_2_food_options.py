"""V3-04.2 deterministic taxonomy and ordinary-food option coverage."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from app.domains.nutrition.food_options import (
    BALANCED_VARIETY_OPTIONS,
    NUTRITION_FOOD_OPTIONS_VERSION,
    PROTEIN_FOOD_OPTIONS,
    NutritionFoodOption,
    options_for_rule,
)
from app.domains.nutrition.preferences import (
    NUTRITION_PREFERENCE_TAXONOMY_VERSION,
    SUPPORTED_DIETS,
    SUPPORTED_FOCUS_KEYS,
    diet_label,
)
from app.domains.routines.schemas import NutritionPreferencePatch


def labels(rule_id: str, diet: str, avoids: list[str] | None = None) -> list[str]:
    return [row.label for row in options_for_rule(rule_id, diet=diet, avoid_foods=avoids or [])]


def test_taxonomy_is_exact_and_schema_authority_is_migrated() -> None:
    assert SUPPORTED_DIETS == ("vegan", "vegetarian", "jain", "eggetarian", "non_vegetarian", "pescatarian")
    assert SUPPORTED_FOCUS_KEYS == ("protein", "vitamin_c", "vitamin_a", "vitamin_e", "iron", "zinc", "copper", "omega_3", "collagen_support", "hydration")
    assert NUTRITION_PREFERENCE_TAXONOMY_VERSION == NUTRITION_FOOD_OPTIONS_VERSION == "v3-04.2"
    assert diet_label("jain") == "Jain" and diet_label("non_vegetarian") == "non-vegetarian"
    assert "app.domains.routines.nutrition" not in importlib.import_module("app.domains.routines.schemas").__dict__
    assert NutritionPreferencePatch(diet="vegan", focus_nutrients=["protein", "iron"]).focus_nutrients == ["protein", "iron"]


def test_registry_has_exact_immutable_entries_and_no_composition_fields() -> None:
    assert len(BALANCED_VARIETY_OPTIONS) == 5
    assert len(PROTEIN_FOOD_OPTIONS) == 10
    assert len({row.option_id for row in (*BALANCED_VARIETY_OPTIONS, *PROTEIN_FOOD_OPTIONS)}) == 15
    assert all(isinstance(row, NutritionFoodOption) and row.kind in {"food_group", "ordinary_food"} for row in (*BALANCED_VARIETY_OPTIONS, *PROTEIN_FOOD_OPTIONS))
    assert all(name not in NutritionFoodOption.__dataclass_fields__ for name in ("amount", "grams", "milligrams", "calories", "percentage"))
    try:
        BALANCED_VARIETY_OPTIONS[0].label = "changed"  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("food options must be immutable")


def test_diet_matrix_and_maximum_are_explicit() -> None:
    assert labels("nutrition.pattern.protein_food_first", "vegan") == ["Dal", "Chana", "Soy foods", "Rajma", "Peanuts"]
    vegetarian = labels("nutrition.pattern.protein_food_first", "vegetarian")
    assert vegetarian == ["Dal", "Chana", "Dahi / curd", "Soy foods", "Paneer", "Rajma", "Peanuts"]
    assert "Eggs" not in vegetarian and "Fish" not in vegetarian and "Chicken" not in vegetarian
    assert "Eggs" in labels("nutrition.pattern.protein_food_first", "eggetarian")
    assert "Fish" in labels("nutrition.pattern.protein_food_first", "pescatarian")
    assert "Chicken" not in labels("nutrition.pattern.protein_food_first", "pescatarian")
    assert len(labels("nutrition.pattern.protein_food_first", "non_vegetarian")) == 8
    assert labels("nutrition.pattern.hydration_context", "non_vegetarian") == []
    assert labels("nutrition.pattern.protein_food_first", "corrupt") == []


def test_avoid_matching_is_exact_and_unknown_terms_do_nothing() -> None:
    base = labels("nutrition.pattern.protein_food_first", "non_vegetarian")
    assert "Dahi / curd" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", [" dairy "])
    assert "Paneer" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["milk"])
    assert "Eggs" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["EGGS"])
    assert "Fish" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["fish"])
    assert "Chicken" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["meat"])
    assert "Fish" in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["meat"])
    assert "Soy foods" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["soya-foods"])
    assert "Chana" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["chana"])
    assert "Dal" in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["chana"])
    assert "Dal" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["lentils"])
    assert "Chana" in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["lentils"])
    assert labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["legumes"]) == ["Dahi / curd", "Eggs", "Fish", "Paneer", "Chicken", "Peanuts"]
    assert labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["not a food"]) == base


def test_balanced_group_avoids_do_not_expand_specific_foods() -> None:
    rule = "nutrition.pattern.balanced_variety"
    assert labels(rule, "vegan") == ["Vegetables", "Fruit", "Pulses / legumes", "Grains / millets", "Nuts / seeds"]
    assert labels(rule, "vegan", ["vegetables"]) == ["Fruit", "Pulses / legumes", "Grains / millets", "Nuts / seeds"]
    assert "Pulses / legumes" not in labels(rule, "vegan", ["legumes"])
    assert "Pulses / legumes" in labels(rule, "vegan", ["chana"])


@pytest.mark.asyncio
async def test_options_are_attached_only_after_the_existing_evidence_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.nutrition import guidance
    from app.domains.nutrition.evidence_applicability import NutritionApplicabilityResult

    async def assessed(*args, **kwargs):
        return SimpleNamespace(behavior_evidence_eligible=True)

    calls = 0

    def applicable(assessment, signals):
        nonlocal calls
        calls += 1
        return NutritionApplicabilityResult("v3-04.1", calls == 1, True)

    monkeypatch.setattr(guidance, "assess_rule_evidence", assessed)
    monkeypatch.setattr(guidance, "resolve_nutrition_evidence_applicability", applicable)
    result = await guidance.build_nutrition_guidance(
        object(), nutrition_enabled=True, protein_focus=True, hydration_enabled=False,
        hot_weather=False, hot_weather_only=False, diet="vegan", avoid_foods=(),
    )
    balanced = next(item for item in result.items if item.rule_id.endswith("balanced_variety"))
    assert balanced.food_options
    assert all(not item.rule_id.endswith("protein_food_first") for item in result.items)
    assert all(option.option_id.startswith("variety.") for option in balanced.food_options)


@pytest.mark.asyncio
async def test_disabled_guidance_does_not_resolve_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domains.nutrition import guidance

    async def forbidden(*args, **kwargs):
        raise AssertionError("Evidence should not be read when Nutrition is disabled")

    monkeypatch.setattr(guidance, "assess_rule_evidence", forbidden)
    result = await guidance.build_nutrition_guidance(
        object(), nutrition_enabled=False, protein_focus=True, hydration_enabled=True,
        hot_weather=True, hot_weather_only=False, diet="non_vegetarian", avoid_foods=("fish",),
    )
    assert result.items == ()
