"""Authoritative V3-04 closure regressions."""
from __future__ import annotations

import ast
import importlib
import re
import uuid
from pathlib import Path

import pytest
from app.bootstrap import run as run_seed
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.nutrition_guidance_seed import SOURCE_KEY
from app.domains.identity import service as identity
from app.domains.nutrition import service as nutrition_service
from app.domains.nutrition.evidence_applicability import NUTRITION_EVIDENCE_APPLICABILITY_VERSION
from app.domains.nutrition.food_options import (
    BALANCED_VARIETY_OPTIONS,
    NUTRITION_FOOD_OPTIONS_VERSION,
    PROTEIN_FOOD_OPTIONS,
)
from app.domains.nutrition.guidance import NUTRITION_GUIDANCE_VERSION
from app.domains.nutrition.guidance_rules import NUTRITION_GUIDANCE_RULES, NUTRITION_GUIDANCE_RULESET_VERSION
from app.domains.nutrition.models import FoodCompositionDataset, FoodNutrientValue, FoodReferenceItem
from app.domains.nutrition.preferences import (
    NUTRITION_PREFERENCE_TAXONOMY_VERSION,
    SUPPORTED_DIETS,
    SUPPORTED_FOCUS_KEYS,
)
from app.domains.nutrition.schemas import HydrationPreferencePatch, NutritionPreferencePatch
from app.domains.routines import models as routines_models
from app.domains.routines import schemas as routines_schemas
from app.domains.routines.models import HydrationPreference, NutritionPreference
from app.shared.database.sql import get_engine, get_sessionmaker
from sqlalchemy import event, func, select

ROOT = Path(__file__).parents[1]
APP = ROOT / "app"
NUTRITION = APP / "domains" / "nutrition"
CLAIM_KEYS = {
    "nutrition.food_pattern_balanced_variety",
    "nutrition.protein_food_first",
    "nutrition.general_hydration_water",
}
RULE_IDS = {
    "nutrition.pattern.balanced_variety",
    "nutrition.pattern.protein_food_first",
    "nutrition.pattern.hydration_context",
}


async def _preference_counts(account_id: uuid.UUID) -> tuple[int, int]:
    factory = get_sessionmaker()
    async with factory() as session:
        nutrition_count = await session.scalar(
            select(func.count()).select_from(NutritionPreference).where(
                NutritionPreference.account_id == account_id
            )
        )
        hydration_count = await session.scalar(
            select(func.count()).select_from(HydrationPreference).where(
                HydrationPreference.account_id == account_id
            )
        )
    return int(nutrition_count or 0), int(hydration_count or 0)


def _production_sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
        if "tests" not in path.parts
    }


def test_legacy_engine_is_deleted_and_old_names_are_absent_from_production() -> None:
    legacy = APP / "domains" / "routines" / "nutrition.py"
    assert not legacy.exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.domains.routines.nutrition")

    forbidden = ("app.domains.routines.nutrition", "NUTRIENT_RULES", "NUTRIENT_BY_KEY", "normalise_diet", "foods_for")
    for path, source in _production_sources().items():
        assert not any(term in source for term in forbidden), path

    imports = []
    for path in APP.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "app.domains.routines.nutrition":
                imports.append(path)
            if isinstance(node, ast.Import) and any(alias.name == "app.domains.routines.nutrition" for alias in node.names):
                imports.append(path)
    assert imports == []


def test_first_class_service_and_schema_ownership_is_unique() -> None:
    owned = (
        "nutrition_preference", "hydration_preference", "patch_nutrition_preference",
        "patch_hydration_preference", "serialize_nutrition_preference",
        "serialize_hydration_preference", "nutrition_suggestions",
    )
    from app.domains.routines import service as routines_service

    for name in owned:
        assert callable(getattr(nutrition_service, name))
        assert not hasattr(routines_service, name)
    assert routines_schemas.NutritionPreferencePatch is NutritionPreferencePatch
    assert routines_schemas.HydrationPreferencePatch is HydrationPreferencePatch

    api_source = (APP / "api" / "v2" / "routines.py").read_text(encoding="utf-8")
    assert "from app.domains.nutrition import service as nutrition_service" in api_source
    nutrition_section = api_source.split("# --- Nutrition and hydration", 1)[1].split("# --- Observations", 1)[0]
    assert "nutrition_service." in nutrition_section
    assert not re.search(r"(?<!nutrition_)\bservice\.(?:nutrition_|hydration_|patch_|serialize_)", nutrition_section)


def test_frozen_taxonomy_rules_versions_and_options() -> None:
    assert SUPPORTED_DIETS == ("vegan", "vegetarian", "jain", "eggetarian", "non_vegetarian", "pescatarian")
    assert SUPPORTED_FOCUS_KEYS == ("protein", "vitamin_c", "vitamin_a", "vitamin_e", "iron", "zinc", "copper", "omega_3", "collagen_support", "hydration")
    assert NUTRITION_GUIDANCE_VERSION == "v3-04.2"
    assert NUTRITION_FOOD_OPTIONS_VERSION == "v3-04.2"
    assert NUTRITION_PREFERENCE_TAXONOMY_VERSION == "v3-04.2"
    assert NUTRITION_GUIDANCE_RULESET_VERSION == "v3-04.1-r1"
    assert NUTRITION_EVIDENCE_APPLICABILITY_VERSION == "v3-04.1"
    assert tuple(
        (row.rule_id, row.rule_version, row.priority, row.title, row.body)
        for row in NUTRITION_GUIDANCE_RULES
    ) == (
        (
            "nutrition.pattern.balanced_variety",
            "v1",
            10,
            "Build variety into your meals",
            "A balanced food pattern comes from variety, not one “perfect” food. Keep the mix flexible around the foods and traditions that fit you.",
        ),
        (
            "nutrition.pattern.protein_food_first",
            "v1",
            20,
            "Keep protein food-first",
            "If protein is something you want to pay attention to, start with ordinary foods that fit your diet rather than treating protein supplements as the default.",
        ),
        (
            "nutrition.pattern.hydration_context",
            "v1",
            30,
            "Keep water in the day",
            "If you want hydration reminders, keep water part of the day. GlamGenius does not set a litre target or treat hydration as a diagnosis.",
        ),
    )
    assert len(BALANCED_VARIETY_OPTIONS) == 5
    assert len(PROTEIN_FOOD_OPTIONS) == 10
    assert [(row.option_id, row.label) for row in (*BALANCED_VARIETY_OPTIONS, *PROTEIN_FOOD_OPTIONS)] == [
        ("variety.vegetables", "Vegetables"),
        ("variety.fruit", "Fruit"),
        ("variety.pulses_legumes", "Pulses / legumes"),
        ("variety.grains_millets", "Grains / millets"),
        ("variety.nuts_seeds", "Nuts / seeds"),
        ("protein.dal", "Dal"),
        ("protein.chana", "Chana"),
        ("protein.dahi", "Dahi / curd"),
        ("protein.eggs", "Eggs"),
        ("protein.soy_foods", "Soy foods"),
        ("protein.fish", "Fish"),
        ("protein.paneer", "Paneer"),
        ("protein.chicken", "Chicken"),
        ("protein.rajma", "Rajma"),
        ("protein.peanuts", "Peanuts"),
    ]


def test_nutrition_runtime_uses_context_and_not_cross_domain_or_ai_inputs() -> None:
    source = (NUTRITION / "service.py").read_text(encoding="utf-8")
    assert "app.domains.planning" in source and "planning_context.gather" in source
    assert not any(term in source for term in ("weather_provider", "resolve_climate_context", "temperature_threshold", "humidity_threshold", "season_for"))
    for term in ("UserReportedObservation", "RoutineAdherence", "CareDecisionSet", "CareRoutinePlan", "InventoryItem", "purchase", "shopping", "meal_plan", "Gemini", "google.generativeai", "llm"):
        assert term.casefold() not in source.casefold()
    for name in ("preferences.py", "food_options.py", "guidance.py", "service.py"):
        source = (NUTRITION / name).read_text(encoding="utf-8")
        assert "FoodReferenceItem" not in source and "FoodNutrientValue" not in source


@pytest.mark.asyncio
async def test_database_evidence_ifct_and_public_api_closure(db_clean, app_client, fake_supabase_user) -> None:
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        session.add(NutritionPreference(account_id=account_id, enabled=True, focus_nutrients=["protein"], diet="vegan"))
        session.add(HydrationPreference(account_id=account_id, enabled=False))
        claims = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key.in_(CLAIM_KEYS), EvidenceClaim.claim_version == 1))).scalars().all()
        links = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.rule_id.in_(RULE_IDS), RuleEvidenceLink.rule_version == "v1"))).scalars().all()
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == SOURCE_KEY))).scalar_one()
        dgi_links = await session.scalar(select(func.count()).select_from(EvidenceClaimSource).join(EvidenceClaim).where(EvidenceClaimSource.source_id == source.id, EvidenceClaim.claim_key.in_(CLAIM_KEYS)))
        rda_links = await session.scalar(select(func.count()).select_from(EvidenceClaimSource).join(EvidenceClaim).join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id).where(EvidenceClaim.claim_key.in_(CLAIM_KEYS), EvidenceSource.source_key == "icmr_nin.nutrient_requirements.rda_ear.2020"))
        dataset = (await session.execute(select(FoodCompositionDataset).where(FoodCompositionDataset.dataset_key == "icmr_nin.ifct.2017"))).scalar_one()
        await session.commit()
    assert {row.claim_key for row in claims} == CLAIM_KEYS
    assert len(links) == 3 and {row.rule_id for row in links} == RULE_IDS
    assert dgi_links == 3 and rda_links == 0
    assert dataset.rights_status == "restricted_reference" and dataset.import_status == "metadata_only"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(FoodReferenceItem)) == 0
        assert await session.scalar(select(func.count()).select_from(FoodNutrientValue)) == 0

    token, _ = fake_supabase_user(user_id=account_id)
    headers = {"Authorization": f"Bearer {token}"}
    async with factory() as session:
        stored_nutrition = (await session.execute(
            select(NutritionPreference).where(NutritionPreference.account_id == account_id)
        )).scalar_one()
        stored_hydration = (await session.execute(
            select(HydrationPreference).where(HydrationPreference.account_id == account_id)
        )).scalar_one()
        before_state = (
            stored_nutrition.diet,
            list(stored_nutrition.avoid_foods),
            list(stored_nutrition.focus_nutrients),
            stored_nutrition.enabled,
            stored_hydration.enabled,
            stored_hydration.remind_in_hot_weather_only,
            stored_hydration.note,
        )
    first = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers=headers)
    second = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers=headers)
    assert first.status_code == second.status_code == 200
    payload = first.json()
    assert second.json() == payload
    assert payload["guidance_version"] == payload["food_options_version"] == "v3-04.2"
    assert payload["ruleset_version"] == "v3-04.1-r1"
    assert payload["suggestions"]
    assert {"rule_id", "rule_version", "title", "body", "trigger_codes", "food_options"} <= set(payload["suggestions"][0])
    assert not any(key in str(payload) for key in ("claim_id", "source_id", "option_id", "priority", "compatible_diets", "avoid_aliases"))

    async with factory() as session:
        stored_nutrition = (await session.execute(
            select(NutritionPreference).where(NutritionPreference.account_id == account_id)
        )).scalar_one()
        stored_hydration = (await session.execute(
            select(HydrationPreference).where(HydrationPreference.account_id == account_id)
        )).scalar_one()
        assert before_state == (
            stored_nutrition.diet,
            list(stored_nutrition.avoid_foods),
            list(stored_nutrition.focus_nutrients),
            stored_nutrition.enabled,
            stored_hydration.enabled,
            stored_hydration.remind_in_hot_weather_only,
            stored_hydration.note,
        )


@pytest.mark.asyncio
async def test_fresh_account_gets_are_persistence_free_and_patches_create_rows(
    db_clean, app_client, fake_supabase_user
) -> None:
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        await session.commit()

    token, _ = fake_supabase_user(user_id=account_id)
    headers = {"Authorization": f"Bearer {token}"}
    assert await _preference_counts(account_id) == (0, 0)

    preference = await app_client.get("/api/v2/nutrition/preferences", headers=headers)
    hydration = await app_client.get("/api/v2/nutrition/hydration", headers=headers)
    suggestions = await app_client.get(
        "/api/v2/nutrition/appearance-suggestions", headers=headers
    )
    assert preference.status_code == hydration.status_code == suggestions.status_code == 200
    assert preference.json() == {
        "diet": "non_vegetarian",
        "avoid_foods": [],
        "focus_nutrients": [],
        "enabled": False,
    }
    assert hydration.json() == {
        "enabled": False,
        "remind_in_hot_weather_only": True,
        "note": None,
        "no_target": "We do not set a litre target. That would be a health instruction, and this is not that kind of app.",
    }
    assert suggestions.json()["enabled"] is False
    assert suggestions.json()["suggestions"] == []
    assert await _preference_counts(account_id) == (0, 0)

    repeated = (
        await app_client.get("/api/v2/nutrition/preferences", headers=headers),
        await app_client.get("/api/v2/nutrition/hydration", headers=headers),
        await app_client.get("/api/v2/nutrition/appearance-suggestions", headers=headers),
    )
    assert [response.json() for response in repeated] == [
        preference.json(), hydration.json(), suggestions.json()
    ]
    assert await _preference_counts(account_id) == (0, 0)

    patch_nutrition = await app_client.patch(
        "/api/v2/nutrition/preferences",
        headers=headers,
        json={"diet": "vegan", "avoid_foods": ["chana"], "focus_nutrients": ["protein"], "enabled": True},
    )
    assert patch_nutrition.status_code == 200
    assert await _preference_counts(account_id) == (1, 0)

    patch_hydration = await app_client.patch(
        "/api/v2/nutrition/hydration",
        headers=headers,
        json={"enabled": True, "remind_in_hot_weather_only": False},
    )
    assert patch_hydration.status_code == 200
    assert await _preference_counts(account_id) == (1, 1)


@pytest.mark.asyncio
async def test_public_nutrition_runtime_never_queries_historical_rule_table(
    db_clean, app_client, fake_supabase_user
) -> None:
    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        session.add(NutritionPreference(account_id=account_id, enabled=True, focus_nutrients=["protein"], diet="vegan"))
        session.add(HydrationPreference(account_id=account_id, enabled=False))
        await session.commit()

    token, _ = fake_supabase_user(user_id=account_id)
    statements: list[str] = []

    def reject_historical_table(conn, cursor, statement, parameters, context, executemany) -> None:
        if "appearance_nutrition_rules" in statement.casefold():
            statements.append(statement)
            raise AssertionError("V3 Nutrition runtime queried the historical rule table")

    engine = get_engine()
    event.listen(engine.sync_engine, "before_cursor_execute", reject_historical_table)
    try:
        response = await app_client.get(
            "/api/v2/nutrition/appearance-suggestions",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", reject_historical_table)

    assert response.status_code == 200
    assert response.json()["guidance_version"] == "v3-04.2"
    assert response.json()["suggestions"]
    assert statements == []


def test_historical_appearance_nutrition_table_is_inert() -> None:
    assert routines_models.AppearanceNutritionRule.__tablename__ == "appearance_nutrition_rules"
    assert "Historical compatibility model; not runtime V3 Nutrition authority." in (APP / "domains" / "routines" / "models.py").read_text(encoding="utf-8")
    assert "AppearanceNutritionRule" not in (NUTRITION / "service.py").read_text(encoding="utf-8")
    allowed = {
        APP / "domains" / "routines" / "models.py",
        APP / "domains" / "privacy" / "__init__.py",
    }
    for path, source in _production_sources().items():
        if path not in allowed:
            assert "AppearanceNutritionRule" not in source, path
            assert "appearance_nutrition_rules" not in source, path
