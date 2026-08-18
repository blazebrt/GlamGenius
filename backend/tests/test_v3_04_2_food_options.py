"""V3-04.2 deterministic taxonomy and ordinary-food option coverage."""
from __future__ import annotations

import ast
import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.bootstrap import run as run_seed
from app.domains.evidence.models import EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.identity import service as identity
from app.domains.nutrition.food_options import (
    BALANCED_VARIETY_OPTIONS,
    NUTRITION_FOOD_OPTIONS_VERSION,
    PROTEIN_FOOD_OPTIONS,
    NutritionFoodOption,
    options_for_rule,
)
from app.domains.nutrition.models import FoodNutrientValue, FoodReferenceItem
from app.domains.nutrition.preferences import (
    NUTRITION_PREFERENCE_TAXONOMY_VERSION,
    SUPPORTED_DIETS,
    SUPPORTED_FOCUS_KEYS,
    diet_label,
)
from app.domains.nutrition.schemas import NutritionPreferencePatch
from app.domains.routines.models import HydrationPreference, NutritionPreference
from app.shared.database.sql import get_sessionmaker
from pydantic import ValidationError
from sqlalchemy import func, select


def labels(rule_id: str, diet: str, avoids: list[str] | None = None) -> list[str]:
    return [row.label for row in options_for_rule(rule_id, diet=diet, avoid_foods=avoids or [])]


async def _seed_account() -> uuid.UUID:
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        session.add(NutritionPreference(account_id=account_id))
        session.add(HydrationPreference(account_id=account_id))
        await session.commit()
    return account_id


async def _set_preferences(
    account_id: uuid.UUID,
    *,
    enabled: bool,
    diet: str = "non_vegetarian",
    focus: list[str] | None = None,
    avoid_foods: list[str] | None = None,
    hydration: bool = False,
    hot_weather_only: bool = True,
) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        nutrition = (await session.execute(select(NutritionPreference).where(NutritionPreference.account_id == account_id))).scalar_one()
        water = (await session.execute(select(HydrationPreference).where(HydrationPreference.account_id == account_id))).scalar_one()
        nutrition.enabled = enabled
        nutrition.diet = diet
        nutrition.focus_nutrients = focus or []
        nutrition.avoid_foods = avoid_foods or []
        water.enabled = hydration
        water.remind_in_hot_weather_only = hot_weather_only
        await session.commit()


async def _account_state(account_id: uuid.UUID) -> tuple[tuple, tuple]:
    factory = get_sessionmaker()
    async with factory() as session:
        nutrition = (await session.execute(select(NutritionPreference).where(NutritionPreference.account_id == account_id))).scalar_one()
        water = (await session.execute(select(HydrationPreference).where(HydrationPreference.account_id == account_id))).scalar_one()
        preferences = (
            nutrition.diet, list(nutrition.avoid_foods), list(nutrition.focus_nutrients), nutrition.enabled,
            water.enabled, water.remind_in_hot_weather_only, water.note,
        )
        evidence = tuple(
            tuple(str(getattr(row, column.name)) for column in RuleEvidenceLink.__table__.columns)
            for row in (await session.execute(select(RuleEvidenceLink))).scalars().all()
        )
    return preferences, tuple(sorted(evidence))


def test_taxonomy_is_exact_and_schema_authority_is_migrated() -> None:
    assert SUPPORTED_DIETS == ("vegan", "vegetarian", "jain", "eggetarian", "non_vegetarian", "pescatarian")
    assert SUPPORTED_FOCUS_KEYS == ("protein", "vitamin_c", "vitamin_a", "vitamin_e", "iron", "zinc", "copper", "omega_3", "collagen_support", "hydration")
    assert NUTRITION_PREFERENCE_TAXONOMY_VERSION == NUTRITION_FOOD_OPTIONS_VERSION == "v3-04.2"
    assert diet_label("jain") == "Jain" and diet_label("non_vegetarian") == "non-vegetarian"
    schema_path = Path(__file__).parents[1] / "app" / "domains" / "routines" / "schemas.py"
    tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    assert not any(
        (isinstance(node, ast.ImportFrom) and node.module == "app.domains.routines.nutrition")
        or (isinstance(node, ast.Import) and any(alias.name == "app.domains.routines.nutrition" for alias in node.names))
        for node in ast.walk(tree)
    )
    for diet in SUPPORTED_DIETS:
        assert NutritionPreferencePatch(diet=diet).diet == diet
    with pytest.raises(ValidationError):
        NutritionPreferencePatch(diet="not_a_diet")
    assert NutritionPreferencePatch(focus_nutrients=list(SUPPORTED_FOCUS_KEYS)).focus_nutrients == list(SUPPORTED_FOCUS_KEYS)
    assert NutritionPreferencePatch(focus_nutrients=["protein", "protein", "iron"]).focus_nutrients == ["protein", "iron"]
    with pytest.raises(ValidationError):
        NutritionPreferencePatch(focus_nutrients=["not_a_focus"])


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
    jain = labels("nutrition.pattern.protein_food_first", "jain")
    assert jain == ["Dal", "Chana", "Dahi / curd", "Soy foods", "Paneer", "Rajma", "Peanuts"]
    assert not {"Eggs", "Fish", "Chicken"}.intersection(jain)
    eggetarian = labels("nutrition.pattern.protein_food_first", "eggetarian")
    assert "Eggs" in eggetarian and "Fish" not in eggetarian and "Chicken" not in eggetarian
    pescatarian = labels("nutrition.pattern.protein_food_first", "pescatarian")
    assert "Eggs" in pescatarian and "Fish" in pescatarian and "Chicken" not in pescatarian
    non_vegetarian = labels("nutrition.pattern.protein_food_first", "non_vegetarian")
    assert len(non_vegetarian) == 8
    assert non_vegetarian == sorted(
        non_vegetarian,
        key=lambda label: next(row.priority for row in PROTEIN_FOOD_OPTIONS if row.label == label),
    )
    assert labels("nutrition.pattern.hydration_context", "non_vegetarian") == []
    assert labels("nutrition.pattern.protein_food_first", "corrupt") == []


def test_avoid_matching_is_exact_and_unknown_terms_do_nothing() -> None:
    base = labels("nutrition.pattern.protein_food_first", "vegetarian")
    assert {"Dahi / curd", "Paneer"}.isdisjoint(labels("nutrition.pattern.protein_food_first", "vegetarian", ["dairy"]))
    assert {"Dahi / curd", "Paneer"}.isdisjoint(labels("nutrition.pattern.protein_food_first", "vegetarian", ["milk"]))
    assert "Eggs" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["EGGS"])
    assert "Fish" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["fish"])
    assert "Chicken" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["meat"])
    assert "Fish" in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["meat"])
    assert "Soy foods" not in labels("nutrition.pattern.protein_food_first", "non_vegetarian", ["soya-foods"])
    chana_avoided = labels("nutrition.pattern.protein_food_first", "vegetarian", [" chana "])
    assert "Chana" not in chana_avoided and {"Dal", "Rajma"}.issubset(chana_avoided)
    lentils_avoided = labels("nutrition.pattern.protein_food_first", "vegetarian", ["lentils"])
    assert "Dal" not in lentils_avoided and {"Chana", "Rajma"}.issubset(lentils_avoided)
    legumes_avoided = labels("nutrition.pattern.protein_food_first", "vegetarian", ["legumes"])
    assert not {"Dal", "Chana", "Rajma", "Soy foods"}.intersection(legumes_avoided)
    assert labels("nutrition.pattern.protein_food_first", "vegetarian", ["not a food"]) == base
    assert labels("nutrition.pattern.protein_food_first", "vegetarian", ["SOYA-FOODS!"]) == labels("nutrition.pattern.protein_food_first", "vegetarian", ["soy foods"])
    assert "Rajma" not in labels("nutrition.pattern.protein_food_first", "vegetarian", ["rajma"])


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


async def _build_with_evidence(monkeypatch: pytest.MonkeyPatch, *, eligible_contexts: set[str] | None = None, focus: str | None = "protein", diet: str = "vegan", avoid_foods: tuple[str, ...] = ()):
    from app.domains.nutrition import guidance
    from app.domains.nutrition.evidence_applicability import NutritionApplicabilityResult

    allowed = eligible_contexts or {"food_pattern_guidance", "food_first_protein", "hydration_guidance"}

    async def assessed(*args, **kwargs):
        return SimpleNamespace(behavior_evidence_eligible=True)

    def applicable(assessment, signals):
        return NutritionApplicabilityResult("v3-04.1", signals.formulations[0] in allowed, True)

    monkeypatch.setattr(guidance, "assess_rule_evidence", assessed)
    monkeypatch.setattr(guidance, "resolve_nutrition_evidence_applicability", applicable)
    return await guidance.build_nutrition_guidance(
        object(), nutrition_enabled=True, protein_focus=focus == "protein", hydration_enabled=True,
        hot_weather=True, hot_weather_only=False, diet=diet, avoid_foods=avoid_foods,
    )


@pytest.mark.asyncio
async def test_guidance_activation_is_explicit_focus_and_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    no_protein = await _build_with_evidence(monkeypatch, focus=None)
    assert [item.rule_id for item in no_protein.items] == [
        "nutrition.pattern.balanced_variety", "nutrition.pattern.hydration_context",
    ]
    assert all(not item.food_options for item in no_protein.items if item.rule_id.endswith("hydration_context"))

    protein = await _build_with_evidence(monkeypatch, focus="protein")
    assert any(item.rule_id.endswith("protein_food_first") and item.food_options for item in protein.items)

    for inactive_focus in ("iron", "vitamin_c"):
        inactive = await _build_with_evidence(monkeypatch, focus=inactive_focus)
        assert not any(item.rule_id.endswith("protein_food_first") for item in inactive.items)

    balanced_blocked = await _build_with_evidence(monkeypatch, eligible_contexts={"food_first_protein"})
    assert not any(item.rule_id.endswith("balanced_variety") for item in balanced_blocked.items)
    assert all(not item.food_options or item.rule_id.endswith("protein_food_first") for item in balanced_blocked.items)
    protein_blocked = await _build_with_evidence(monkeypatch, eligible_contexts={"food_pattern_guidance"})
    assert not any(item.rule_id.endswith("protein_food_first") for item in protein_blocked.items)
    assert all(item.rule_id.endswith("balanced_variety") or not item.food_options for item in protein_blocked.items)


@pytest.mark.asyncio
async def test_guidance_fingerprint_tracks_visible_option_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    same_a = await _build_with_evidence(monkeypatch, diet="vegan")
    same_b = await _build_with_evidence(monkeypatch, diet="vegan")
    assert same_a.fingerprint == same_b.fingerprint

    changed_avoid = await _build_with_evidence(monkeypatch, diet="vegan", avoid_foods=("vegetables",))
    assert changed_avoid.fingerprint != same_a.fingerprint
    changed_diet = await _build_with_evidence(monkeypatch, diet="vegetarian")
    assert changed_diet.fingerprint != same_a.fingerprint
    unknown_avoid = await _build_with_evidence(monkeypatch, diet="vegan", avoid_foods=("unrecognised food",))
    assert unknown_avoid.fingerprint == same_a.fingerprint


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


@pytest.mark.asyncio
async def test_real_api_contract_and_get_is_read_only(db_clean, app_client, fake_supabase_user) -> None:
    account_a = await _seed_account()
    await _set_preferences(
        account_a, enabled=True, diet="vegan", focus=["protein"],
        avoid_foods=["chana", "unknown term"], hydration=True, hot_weather_only=False,
    )
    token_a, _ = fake_supabase_user(user_id=account_a)
    headers_a = {"Authorization": f"Bearer {token_a}"}

    before_preferences, before_evidence = await _account_state(account_a)
    async with get_sessionmaker()() as session:
        assert await session.scalar(select(func.count()).select_from(FoodReferenceItem)) == 0
        assert await session.scalar(select(func.count()).select_from(FoodNutrientValue)) == 0

    response = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers=headers_a)
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["food_options_version"] == payload["guidance_version"] == "v3-04.2"
    assert payload["ruleset_version"] == "v3-04.1-r1"
    assert len(payload["suggestions"]) <= 3
    assert all(isinstance(item["food_options"], list) for item in payload["suggestions"])
    protein = next(item for item in payload["suggestions"] if item["rule_id"].endswith("protein_food_first"))
    assert len(protein["food_options"]) <= 8 and "Chana" not in protein["food_options"]
    hydration = next(item for item in payload["suggestions"] if item["rule_id"].endswith("hydration_context"))
    assert hydration["food_options"] == []
    assert not any(key in str(payload) for key in ("option_id", "compatible_diets", "avoid_aliases", "priority"))
    repeated = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers=headers_a)
    assert repeated.json() == payload

    # This is intentionally the first DB read after the GETs: no later setup
    # write may repair a mutation that the read-only proof is meant to catch.
    after_preferences, after_evidence = await _account_state(account_a)
    assert after_preferences == before_preferences
    assert after_evidence == before_evidence


@pytest.mark.asyncio
async def test_real_api_account_isolation(db_clean, app_client, fake_supabase_user) -> None:
    account_a = await _seed_account()
    await _set_preferences(account_a, enabled=True, diet="vegan", focus=["protein"], avoid_foods=["chana"])
    token_a, _ = fake_supabase_user(user_id=account_a)
    response_a = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response_a.status_code == 200
    protein_a = next(item for item in response_a.json()["suggestions"] if item["rule_id"].endswith("protein_food_first"))
    assert "Chana" not in protein_a["food_options"]

    account_b = await _seed_account()
    await _set_preferences(account_b, enabled=True, diet="non_vegetarian", focus=["protein"], hydration=False)
    token_b, _ = fake_supabase_user(user_id=account_b)
    response_b = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response_b.status_code == 200
    payload_b = response_b.json()
    protein_b = next(item for item in payload_b["suggestions"] if item["rule_id"].endswith("protein_food_first"))
    assert "Chana" in protein_b["food_options"]


@pytest.mark.asyncio
async def test_disabled_response_has_no_options(db_clean, app_client, fake_supabase_user) -> None:
    account_id = await _seed_account()
    await _set_preferences(account_id, enabled=False, diet="vegan", focus=["protein"], avoid_foods=["chana"], hydration=True)
    token, _ = fake_supabase_user(user_id=account_id)
    response = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["suggestions"] == []


@pytest.mark.asyncio
async def test_runtime_response_safety_sweep_uses_actual_copy(db_clean, app_client, fake_supabase_user) -> None:
    account_id = await _seed_account()
    await _set_preferences(account_id, enabled=True, diet="vegan", focus=["protein"], hydration=False)
    token, _ = fake_supabase_user(user_id=account_id)
    response = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    visible_strings = []
    for item in payload.get("suggestions", []):
        visible_strings.extend([item.get("title", ""), item.get("body", "")])
        visible_strings.extend(item.get("food_options", []))
    visible_strings.extend(payload.get("boundaries", []))
    visible_strings.extend([payload.get("disclaimer", ""), payload.get("message", "")])
    visible = " ".join(value for group in visible_strings for value in (group if isinstance(group, list) else [group]) if isinstance(value, str)).casefold()
    forbidden = ("deficiency", "diagnosis", "treatment", "cure", "prescription", "therapeutic effect", "high protein", "protein-rich", "best protein source", "grams/day", "mg/day", "litres/day", "calorie target", "weight loss", "weight gain", "bmi")
    assert not any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", visible) for term in forbidden)


@pytest.mark.asyncio
async def test_runtime_metadata_and_zero_composition_rows(db_clean) -> None:
    account_id = await _seed_account()
    async with get_sessionmaker()() as session:
        from app.domains.nutrition.models import FoodCompositionDataset

        dataset = (await session.execute(select(FoodCompositionDataset).where(FoodCompositionDataset.dataset_key == "icmr_nin.ifct.2017"))).scalar_one()
        assert dataset.rights_status == "restricted_reference" and dataset.import_status == "metadata_only"
        assert await session.scalar(select(func.count()).select_from(FoodReferenceItem)) == 0
        assert await session.scalar(select(func.count()).select_from(FoodNutrientValue)) == 0
        rda_links = await session.scalar(
            select(func.count()).select_from(EvidenceClaimSource)
            .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
            .where(EvidenceSource.source_key == "icmr_nin.nutrient_requirements.rda_ear.2020")
        )
        assert rda_links == 0
    assert account_id

    nutrition_dir = Path(__file__).parents[1] / "app" / "domains" / "nutrition"
    for name in ("preferences.py", "food_options.py", "guidance.py"):
        source = (nutrition_dir / name).read_text(encoding="utf-8")
        assert "FoodReferenceItem" not in source and "FoodNutrientValue" not in source


def test_food_option_selection_has_no_observation_care_or_inventory_inputs() -> None:
    from app.domains.nutrition import food_options, guidance

    builder_parameters = set(inspect.signature(guidance.build_nutrition_guidance).parameters)
    resolver_parameters = set(inspect.signature(food_options.options_for_rule).parameters)
    forbidden_parameters = {"observation", "user_reported_observation", "adherence", "routine_adherence", "care", "inventory", "inventory_item"}
    assert not builder_parameters.intersection(forbidden_parameters)
    assert not resolver_parameters.intersection(forbidden_parameters)
    for module in (guidance, food_options):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not any(term in source for term in ("UserReportedObservation", "RoutineAdherence", "InventoryItem"))
