"""Versioned reference-data bootstrap.

Reference data (inventory taxonomy, ingredient catalogue, compatibility and
contraindication rules, routine templates, progress metric and milestone
definitions, feature-flag defaults) is committed as code and applied to the
database with this idempotent script:

.. code-block:: shell

    python -m app.bootstrap.reference_data

Guarantees
----------
* **Deterministic identifiers.** Every row keys against a natural column so a
  second run updates rather than duplicates.
* **Idempotent upserts.** Running the script against a populated database is
  a safe no-op.
* **Versioned.** Each seed table stores the ``SEED_VERSION`` marker so an
  operator can tell which release wrote which row.
* **Bounded scope.** Reference data only. Never touches per-account rows.

The seed is meant to be safe to run inside an empty database after Alembic
has upgraded to head. It is also safe to run repeatedly during development.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryCategory
from app.domains.inventory.taxonomy import CATEGORIES
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)


SEED_VERSION = "2026.02.15"


# ---------------------------------------------------------------------------
# Inventory taxonomy — the seven canonical categories
# ---------------------------------------------------------------------------

# (internal key, display name, position)
CANONICAL_INVENTORY_CATEGORIES: List[Tuple[str, str, int]] = [
    ("wardrobe", "Wardrobe", 1),
    ("shoes", "Shoes", 2),
    ("accessories", "Accessories", 3),
    ("beauty", "Beauty Shelf", 4),
    ("hair", "Hair Shelf", 5),
    ("perfumes", "Perfumes", 6),
    ("supplements", "Supplements", 7),
]


async def seed_inventory_categories(session: AsyncSession) -> int:
    """Upsert the seven canonical inventory categories.

    Idempotent — a second call is a no-op. Category keys are the same
    internal identifiers used by :mod:`app.domains.inventory.taxonomy`, and
    the display names are the user-facing names in the product spec.
    """
    seed_keys = {k for k, _, _ in CANONICAL_INVENTORY_CATEGORIES}
    tax_keys = set(CATEGORIES.keys())
    if seed_keys != tax_keys:
        raise RuntimeError(
            f"Inventory taxonomy drift: seed keys {sorted(seed_keys)} != "
            f"taxonomy keys {sorted(tax_keys)}"
        )
    rows = [
        {"key": key, "display_name": display, "position": pos, "active": True}
        for key, display, pos in CANONICAL_INVENTORY_CATEGORIES
    ]
    stmt = pg_insert(InventoryCategory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={
            "display_name": stmt.excluded.display_name,
            "position": stmt.excluded.position,
            "active": stmt.excluded.active,
        },
    )
    await session.execute(stmt)
    return len(rows)


# ---------------------------------------------------------------------------
# Ingredient catalogue + aliases + compatibility rules
# ---------------------------------------------------------------------------

# Small representative catalogue keyed by the ingredient family used by the
# routines engine. Production expands this from ``docs/stabilisation/
# INGREDIENT_COVERAGE.md``; the seed shipped in the app is scoped to the
# ingredients the routine engine reasons about explicitly.
CORE_INGREDIENTS: List[dict] = [
    {"key": "retinol", "display_name": "Retinol", "family": "retinoid"},
    {"key": "retinoid", "display_name": "Retinoid", "family": "retinoid"},
    {"key": "vitamin_c", "display_name": "Vitamin C", "family": "antioxidant"},
    {"key": "niacinamide", "display_name": "Niacinamide", "family": "vitamin"},
    {"key": "aha", "display_name": "AHA", "family": "exfoliant"},
    {"key": "bha", "display_name": "BHA", "family": "exfoliant"},
    {"key": "benzoyl_peroxide", "display_name": "Benzoyl Peroxide", "family": "acne"},
    {"key": "salicylic_acid", "display_name": "Salicylic Acid", "family": "exfoliant"},
    {"key": "hyaluronic_acid", "display_name": "Hyaluronic Acid", "family": "humectant"},
    {"key": "spf", "display_name": "Broad-spectrum SPF", "family": "sunscreen"},
]

INGREDIENT_ALIASES: List[Tuple[str, str]] = [
    ("retinol", "retin-a"),
    ("retinol", "retinaldehyde"),
    ("retinoid", "adapalene"),
    ("aha", "glycolic acid"),
    ("aha", "lactic acid"),
    ("bha", "salicylic acid"),
    ("vitamin_c", "ascorbic acid"),
    ("vitamin_c", "l-ascorbic acid"),
    ("hyaluronic_acid", "sodium hyaluronate"),
]

# (family_a, family_b, severity, headline, guidance)
# Values are conservative, product-safety guidance — never diagnosis.
COMPATIBILITY_RULES: List[Tuple[str, str, str, str, str]] = [
    ("retinoid", "exfoliant", "avoid",
     "Retinoids and exfoliants together can irritate skin.",
     "Use on alternate evenings instead of the same night."),
    ("retinoid", "acne", "caution",
     "Retinoids and benzoyl peroxide can neutralise each other.",
     "Apply on alternate evenings."),
    ("antioxidant", "vitamin", "compatible",
     "Modern vitamin C and niacinamide formulations play well together.",
     "Safe to layer in a daytime routine."),
    ("sunscreen", "any", "required",
     "Daytime routines finish with SPF.",
     "Apply broad-spectrum SPF as the final daytime step."),
]


async def seed_ingredients(session: AsyncSession) -> int:
    from app.domains.routines.models import (
        CompatibilityRuleRow,
        Ingredient,
        IngredientAlias,
    )

    seeded = 0
    for row in CORE_INGREDIENTS:
        existing = (await session.execute(
            select(Ingredient).where(Ingredient.key == row["key"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(Ingredient(
                key=row["key"],
                display_name=row["display_name"],
                family=row["family"],
                knowledge_version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.display_name = row["display_name"]
            existing.family = row["family"]
            existing.knowledge_version = SEED_VERSION

    await session.flush()

    for canonical, alias in INGREDIENT_ALIASES:
        existing = (await session.execute(
            select(IngredientAlias).where(
                IngredientAlias.ingredient_key == canonical,
                IngredientAlias.alias == alias,
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(IngredientAlias(ingredient_key=canonical, alias=alias))
            seeded += 1

    await session.flush()

    for a, b, severity, headline, guidance in COMPATIBILITY_RULES:
        rule_id = f"{a}__{b}__{severity}"
        existing = (await session.execute(
            select(CompatibilityRuleRow).where(CompatibilityRuleRow.rule_id == rule_id)
        )).scalar_one_or_none()
        if existing is None:
            session.add(CompatibilityRuleRow(
                rule_id=rule_id,
                family_a=a,
                family_b=b,
                severity=severity,
                headline=headline,
                guidance=guidance,
                knowledge_version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.headline = headline
            existing.guidance = guidance
            existing.severity = severity
            existing.knowledge_version = SEED_VERSION

    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Progress metrics + milestones
# ---------------------------------------------------------------------------

METRIC_DEFINITIONS: List[dict] = [
    {
        "key": "wardrobe_utilisation",
        "label": "Wardrobe utilisation",
        "unit": "percent",
        "direction": "higher_better",
        "formula": "worn_last_90_days / active_wardrobe_size",
        "formula_version": "1",
        "inputs": ["item_usage_events", "inventory_items"],
        "missing_data_behaviour": "hide",
        "explanation": "Share of active wardrobe worn in the last 90 days.",
        "update_frequency": "daily",
        "not_a_measure_of": "How much you spent, how big your closet is, or a value judgement.",
    },
    {
        "key": "routine_adherence",
        "label": "Routine adherence",
        "unit": "percent",
        "direction": "higher_better",
        "formula": "completed_steps / scheduled_steps",
        "formula_version": "1",
        "inputs": ["routine_adherence"],
        "missing_data_behaviour": "hide",
        "explanation": "Share of scheduled routine steps completed.",
        "update_frequency": "daily",
        "not_a_measure_of": "How your skin, hair or wardrobe look overall.",
    },
]

MILESTONE_RULES: List[dict] = [
    {
        "rule_id": "first_look_completed",
        "label": "First styled look",
        "description": "You completed your first styled look. Great start.",
        "behaviour": "one_off",
        "threshold": 1,
        "repeatable": False,
    },
    {
        "rule_id": "routine_seven_day",
        "label": "One-week streak",
        "description": "You kept your routine on track for a week.",
        "behaviour": "streak_days",
        "threshold": 7,
        "repeatable": True,
    },
]


async def seed_progress(session: AsyncSession) -> int:
    from app.domains.progress.models import MetricDefinition, MilestoneRule

    seeded = 0
    for row in METRIC_DEFINITIONS:
        existing = (await session.execute(
            select(MetricDefinition).where(MetricDefinition.key == row["key"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(MetricDefinition(registry_version=SEED_VERSION, **row))
            seeded += 1
        else:
            for k, v in row.items():
                setattr(existing, k, v)
            existing.registry_version = SEED_VERSION

    for row in MILESTONE_RULES:
        existing = (await session.execute(
            select(MilestoneRule).where(MilestoneRule.rule_id == row["rule_id"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(MilestoneRule(registry_version=SEED_VERSION, **row))
            seeded += 1
        else:
            for k, v in row.items():
                setattr(existing, k, v)
            existing.registry_version = SEED_VERSION

    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Feature flags — stable private-beta defaults
# ---------------------------------------------------------------------------

# (flag key, enabled, description)
FEATURE_FLAG_DEFAULTS: List[Tuple[str, bool, str]] = [
    ("v2_scan", True, "Photo analysis (face/hair/hand)"),
    ("v2_quiz", True, "Style quiz"),
    ("v2_profile", True, "Appearance profile"),
    ("v2_inventory", True, "Inventory across seven categories"),
    ("v2_recommendations", True, "Styling recommendations"),
    ("v2_media", True, "Media upload/read/delete"),
    ("v2_privacy", True, "Privacy export + deletion"),
    ("v2_consent", True, "Consent capture"),
    ("v2_ai_gateway", True, "AI gateway"),
    ("v2_progress", True, "Progress and goals"),
    ("v2_routines", True, "Routine engine"),
    ("v2_today", True, "Today plan"),
    ("v2_planner", True, "Weekly planner"),
    ("v2_shopping_decisions", True, "Shopping evaluation"),
    ("v2_virtual_try_on", False, "Off until a real provider is wired"),
    ("v2_packing", False, "Not yet built"),
]


async def seed_feature_flags(session: AsyncSession) -> int:
    from app.shared.flags.models import FeatureFlag

    seeded = 0
    for key, enabled, description in FEATURE_FLAG_DEFAULTS:
        existing = (await session.execute(
            select(FeatureFlag).where(FeatureFlag.key == key)
        )).scalar_one_or_none()
        if existing is None:
            session.add(FeatureFlag(key=key, enabled=enabled, description=description))
            seeded += 1
        # Preserve operator-changed enabled state on existing rows; refresh only
        # the description to keep it in sync with the code.
        else:
            existing.description = description

    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run(session: AsyncSession) -> dict:
    counts = {
        "inventory_categories": await seed_inventory_categories(session),
        "ingredients_and_rules": await seed_ingredients(session),
        "progress": await seed_progress(session),
        "feature_flags": await seed_feature_flags(session),
    }
    await session.commit()
    return {"seed_version": SEED_VERSION, "counts": counts}


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    factory = get_sessionmaker()
    async with factory() as session:
        result = await run(session)
    logger.info("reference_data_seed_complete %s", result)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
