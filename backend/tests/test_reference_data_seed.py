"""Reference-data seed tests.

Covers:
    * Empty database seed produces the expected row counts.
    * Second run is a no-op (idempotent).
    * All seven inventory categories are available.
    * Invalid category is rejected by the FK.
    * Feature-flag defaults are seeded.
    * Metric definitions and milestone rules are seeded.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.bootstrap import (
    CANONICAL_INVENTORY_CATEGORIES,
    FEATURE_FLAG_DEFAULTS,
    METRIC_DEFINITIONS,
    SEED_VERSION,
    run as run_seed,
)
from app.domains.inventory.models import InventoryCategory, InventoryItem
from app.domains.progress.models import MetricDefinition, MilestoneRule
from app.domains.routines.models import CompatibilityRuleRow, Ingredient
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
    # No new categories, no new ingredients, no new flags.
    assert second["counts"]["ingredients_and_rules"] == 0
    assert second["counts"]["progress"] == 0
    assert second["counts"]["feature_flags"] == 0


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
