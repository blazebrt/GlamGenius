"""Reference-data seed tests.

Covers:
    * Empty database seed produces the expected row counts.
    * Second run is a no-op (idempotent).
    * All seven inventory categories are available.
    * Invalid category is rejected by the FK.
    * Feature-flag defaults are seeded.
    * Metric definitions and milestone rules are seeded.
    * Inventory subtypes, routine templates + steps, perfume context,
      supplement context, contraindications and sensitivities are seeded.
    * Seed-version audit trail is written and reused.
    * Operator changes to feature-flag ``enabled`` survive a re-run.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.bootstrap import (
    CANONICAL_INVENTORY_CATEGORIES,
    CONTRAINDICATION_DEFS,
    FEATURE_FLAG_DEFAULTS,
    INVENTORY_SUBTYPES,
    METRIC_DEFINITIONS,
    PERFUME_CONTEXT_DEFS,
    ROUTINE_TEMPLATE_DEFS,
    SEED_VERSION,
    SENSITIVITY_DEFS,
    SUPPLEMENT_CONTEXT_DEFS,
    run as run_seed,
)
from app.domains.inventory.models import InventoryCategory, InventoryItem
from app.domains.progress.models import MetricDefinition, MilestoneRule
from app.domains.reference import (
    IngredientContraindicationRule,
    IngredientSensitivityRule,
    InventorySubtypeDefinition,
    RoutineTemplateStep,
    SeedVersionRecord,
    SupplementContextRule,
)
from app.domains.routines.models import (
    CompatibilityRuleRow,
    Ingredient,
    PerfumeContextRule,
    RoutineTemplate,
)
from app.shared.database.sql import get_sessionmaker
from app.shared.flags.models import FeatureFlag


pytestmark = pytest.mark.asyncio


async def test_seed_produces_expected_counts_on_empty_db(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        result = await run_seed(session)
    assert result["seed_version"] == SEED_VERSION
    assert result["counts"]["inventory_categories"] == len(CANONICAL_INVENTORY_CATEGORIES)
    assert result["counts"]["feature_flags"] == len(FEATURE_FLAG_DEFAULTS)


async def test_seed_is_idempotent(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    async with factory() as session:
        second = await run_seed(session)
    # No new rows on the second pass.
    assert second["counts"]["ingredients_and_rules"] == 0
    assert second["counts"]["progress"] == 0
    assert second["counts"]["feature_flags"] == 0
    assert second["counts"]["inventory_subtypes"] == 0
    assert second["counts"]["routine_templates"] == 0
    assert second["counts"]["perfume_context"] == 0
    assert second["counts"]["supplement_context"] == 0
    assert second["counts"]["ingredient_contraindications"] == 0
    assert second["counts"]["ingredient_sensitivities"] == 0


async def test_seed_version_record_written(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    async with factory() as session:
        records = (await session.execute(
            select(SeedVersionRecord).where(
                SeedVersionRecord.seed_version == SEED_VERSION
            )
        )).scalars().all()
    domains = {r.seed_domain for r in records}
    # Every seeded domain has an audit record.
    for expected in ("inventory_categories", "inventory_subtypes",
                     "routine_templates", "perfume_context",
                     "supplement_context", "ingredient_contraindications",
                     "ingredient_sensitivities", "feature_flags", "progress"):
        assert expected in domains, f"missing {expected} in seed audit"


async def test_seed_upgrade_updates_versioned_records(db_clean):
    """Bumping SEED_VERSION rewrites versioned rows in-place, not duplicates.

    We simulate a version upgrade by running the seed twice with the version
    string temporarily replaced. The row count for versioned tables must not
    grow, but the ``version`` column on each row must reflect the new value.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    # Count rows now.
    async with factory() as session:
        pre_subtypes = (await session.execute(
            select(func.count(InventorySubtypeDefinition.category_key))
        )).scalar_one()

    # Force a new version and re-run.
    import app.bootstrap as bootstrap
    original = bootstrap.SEED_VERSION
    bootstrap.SEED_VERSION = "2026.09.01"
    try:
        async with factory() as session:
            await run_seed(session)
        async with factory() as session:
            post_subtypes = (await session.execute(
                select(func.count(InventorySubtypeDefinition.category_key))
            )).scalar_one()
            # No duplicates.
            assert post_subtypes == pre_subtypes
            # Rows now reflect the new version.
            versions = (await session.execute(
                select(InventorySubtypeDefinition.version).distinct()
            )).scalars().all()
            assert versions == ["2026.09.01"]
    finally:
        bootstrap.SEED_VERSION = original


async def test_seed_preserves_operator_flag_changes(db_clean):
    """If an operator flipped a flag ``enabled``, the seed must not overwrite it."""
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
    async with factory() as session:
        flag = (await session.execute(
            select(FeatureFlag).where(FeatureFlag.key == "v2_scan")
        )).scalar_one()
        flag.enabled = False  # operator override
        await session.commit()

    async with factory() as session:
        await run_seed(session)  # re-run
    async with factory() as session:
        flag = (await session.execute(
            select(FeatureFlag).where(FeatureFlag.key == "v2_scan")
        )).scalar_one()
        assert flag.enabled is False, "operator override was overwritten"


async def test_inventory_subtypes_cover_every_category(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        cats_with_subtypes = (await session.execute(
            select(InventorySubtypeDefinition.category_key).distinct()
        )).scalars().all()
    assert set(cats_with_subtypes) == {
        "wardrobe", "shoes", "accessories",
        "beauty", "hair", "perfumes", "supplements",
    }


async def test_routine_templates_cover_all_declared_kinds(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        kinds = (await session.execute(select(RoutineTemplate.kind))).scalars().all()
    for kind, _, _, _ in ROUTINE_TEMPLATE_DEFS:
        assert kind in kinds


async def test_routine_template_steps_render_in_order(db_clean):
    """Steps must be retrievable ordered by position with stable step keys."""
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        rows = (await session.execute(
            select(RoutineTemplateStep)
            .where(RoutineTemplateStep.template_kind == "morning_minimal")
            .order_by(RoutineTemplateStep.position)
        )).scalars().all()
    assert [r.step_key for r in rows] == ["cleanser", "moisturiser", "spf"]


async def test_perfume_context_covers_key_factors(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        factors = (await session.execute(
            select(PerfumeContextRule.factor).distinct()
        )).scalars().all()
    for expected in ("occasion", "time_of_day", "climate", "intensity", "layering"):
        assert expected in factors


async def test_supplement_context_never_contains_dosage(db_clean):
    """Every supplement rule must be general context — no dosage/diagnosis."""
    forbidden_words = ("mg", "dosage", "diagnosis", "prescription", "treatment")
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        rules = (await session.execute(select(SupplementContextRule))).scalars().all()
    for r in rules:
        text = (r.guidance or "").lower()
        for word in forbidden_words:
            assert word not in text, f"{r.rule_id} contains forbidden word {word}"


async def test_contraindication_and_sensitivity_lookup(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        pregnancy_flag = (await session.execute(
            select(IngredientContraindicationRule).where(
                IngredientContraindicationRule.ingredient_key == "retinol",
                IngredientContraindicationRule.condition_key == "pregnancy",
            )
        )).scalar_one_or_none()
        vc_sensitive = (await session.execute(
            select(IngredientSensitivityRule).where(
                IngredientSensitivityRule.ingredient_key == "vitamin_c",
                IngredientSensitivityRule.sensitivity_key == "sensitive_skin",
            )
        )).scalar_one_or_none()
    assert pregnancy_flag is not None
    assert vc_sensitive is not None
    # Rule wording never claims diagnosis.
    assert "diagnos" not in (pregnancy_flag.guidance or "").lower()


async def test_all_seven_inventory_categories_available(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        rows = (await session.execute(
            select(InventoryCategory.key).order_by(InventoryCategory.position)
        )).scalars().all()
    assert list(rows) == [
        "wardrobe", "shoes", "accessories",
        "beauty", "hair", "perfumes", "supplements",
    ]


async def test_invalid_category_is_rejected_by_fk(db_clean):
    """Category strings that don't exist in ``inventory_categories`` are refused."""
    from app.domains.identity import service as identity
    from sqlalchemy.exc import IntegrityError
    import uuid

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        account_id = uuid.uuid4()
        await identity.register_account(session, account_id)
        session.add(InventoryItem(
            account_id=account_id,
            category="not_a_real_category",
            display_name="X",
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_feature_flag_defaults_seeded(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        keys = (await session.execute(select(FeatureFlag.key))).scalars().all()
    assert "v2_privacy" in keys
    assert "v2_inventory" in keys
    # Unfinished features stay off by default.
    async with factory() as session:
        vto = (await session.execute(
            select(FeatureFlag).where(FeatureFlag.key == "v2_virtual_try_on")
        )).scalar_one()
        assert vto.enabled is False


async def test_metric_definitions_and_milestone_rules_seeded(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        metric_keys = (await session.execute(
            select(MetricDefinition.key)
        )).scalars().all()
        milestones = (await session.execute(select(MilestoneRule))).scalars().all()
    assert set(metric_keys) >= {m["key"] for m in METRIC_DEFINITIONS}
    assert len(milestones) >= 2


async def test_ingredient_and_compatibility_rules_seeded(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        ingredients = (await session.execute(
            select(Ingredient.key)
        )).scalars().all()
        rules = (await session.execute(select(CompatibilityRuleRow))).scalars().all()
    assert "retinol" in ingredients
    assert "spf" in ingredients
    # Never diagnostic or dosage guidance — evidence a conservative rule set.
    assert all("diagnosis" not in (r.guidance or "").lower() for r in rules)
    assert all("prescription" not in (r.guidance or "").lower() for r in rules)
