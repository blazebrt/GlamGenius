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
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryCategory
from app.domains.inventory.taxonomy import CATEGORIES
from app.shared.database.sql import get_sessionmaker

logger = logging.getLogger(__name__)


SEED_VERSION = "2026.02.16"


# ---------------------------------------------------------------------------
# Inventory taxonomy — the seven canonical categories
# ---------------------------------------------------------------------------

# (internal key, display name, position)
CANONICAL_INVENTORY_CATEGORIES: list[tuple[str, str, int]] = [
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

async def seed_ingredients(session: AsyncSession) -> int:
    """Mirror the engine's ingredient ontology into the database.

    ``app.domains.routines.ontology`` is what the label parser and the rules
    engine actually read. Seeding a separate hand-written subset produced a
    catalogue that disagreed with the engine — an alias the parser resolves was
    absent from ``ingredient_aliases``, and ``GET /ingredients/{key}`` answered
    for keys the seeded table had never heard of. Reading the ontology keeps
    the seeded catalogue, its aliases and its compatibility rules in step with
    the code that uses them.
    """
    from app.domains.routines import ontology
    from app.domains.routines.models import (
        CompatibilityRuleRow,
        Ingredient,
        IngredientAlias,
    )

    seeded = 0
    for row in ontology.INGREDIENTS:
        existing = (await session.execute(
            select(Ingredient).where(Ingredient.key == row.key)
        )).scalar_one_or_none()
        if existing is None:
            session.add(Ingredient(
                key=row.key,
                display_name=row.display_name,
                inci_name=row.inci_name,
                family=row.family,
                summary=row.summary,
                common_use=row.common_use,
                knowledge_version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.display_name = row.display_name
            existing.inci_name = row.inci_name
            existing.family = row.family
            existing.summary = row.summary
            existing.common_use = row.common_use
            existing.knowledge_version = SEED_VERSION

    await session.flush()

    # Every spelling the parser resolves — display name, INCI name, the key
    # itself and each declared alias. ``alias`` is globally unique (one
    # spelling cannot mean two ingredients), so the lookup is by alias alone.
    for alias, canonical in ontology.alias_index().items():
        existing = (await session.execute(
            select(IngredientAlias).where(IngredientAlias.alias == alias)
        )).scalar_one_or_none()
        if existing is None:
            session.add(IngredientAlias(ingredient_key=canonical, alias=alias))
            seeded += 1
        else:
            existing.ingredient_key = canonical

    await session.flush()

    for rule in ontology.COMPATIBILITY_RULES:
        existing = (await session.execute(
            select(CompatibilityRuleRow).where(CompatibilityRuleRow.rule_id == rule.rule_id)
        )).scalar_one_or_none()
        if existing is None:
            session.add(CompatibilityRuleRow(
                rule_id=rule.rule_id,
                family_a=rule.family_a,
                family_b=rule.family_b,
                severity=rule.severity,
                headline=rule.headline,
                guidance=rule.guidance,
                evidence_note=rule.evidence_note,
                knowledge_version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.family_a = rule.family_a
            existing.family_b = rule.family_b
            existing.severity = rule.severity
            existing.headline = rule.headline
            existing.guidance = rule.guidance
            existing.evidence_note = rule.evidence_note
            existing.knowledge_version = SEED_VERSION

    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Progress metrics + milestones
# ---------------------------------------------------------------------------

def _metric_definitions() -> list[dict]:
    """The metric catalogue, taken from the domain registry.

    ``app.domains.progress.registry`` is where a metric is defined, computed
    and versioned. Restating those rows here by hand is how the seed ends up
    shipping a metric the engine cannot compute — and it did: the catalogue
    held two rows, one of which named a metric that no longer existed. Reading
    the registry means the seeded catalogue is complete and correct by
    construction, including each metric's formula version, inputs,
    missing-data behaviour, minimum data requirement and direction.
    """
    from app.domains.progress.registry import METRICS

    return [
        {
            "key": metric.key,
            "label": metric.label,
            "unit": metric.unit,
            "direction": metric.direction,
            "formula": metric.formula,
            "formula_version": metric.formula_version,
            "inputs": list(metric.inputs),
            "missing_data_behaviour": metric.missing_data_behaviour,
            "explanation": metric.explanation,
            "update_frequency": metric.update_frequency,
            "not_a_measure_of": metric.not_a_measure_of,
        }
        for metric in METRICS
    ]


# Every seeded milestone rule must be reachable: ``progress.service`` only
# records behaviours listed in ``milestones.REWARDABLE_BEHAVIOURS``, so a rule
# naming anything else could never be awarded. The list below is therefore kept
# in lockstep with ``app.domains.progress.milestones.RULES`` — that module owns
# the wording rules (no engagement rewards, no childish language) and validates
# them at import time.
MILESTONE_RULES: list[dict] = [
    {
        "rule_id": "milestone.used_five_low_use",
        "label": "Five unused products back in use",
        "description": "You used five products that had been sitting untouched.",
        "behaviour": "used_low_use_product",
        "threshold": 5,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.routine_ten_days",
        "label": "Ten days of your routine",
        "description": "You completed at least one routine step on ten separate days.",
        "behaviour": "completed_routine",
        "threshold": 10,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.routine_thirty_days",
        "label": "Thirty days of your routine",
        "description": "You completed at least one routine step on thirty separate days.",
        "behaviour": "completed_routine",
        "threshold": 30,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.ten_distinct_outfits",
        "label": "Ten different outfits worn",
        "description": "You wore ten distinct combinations from what you own.",
        "behaviour": "wore_diverse_outfit",
        "threshold": 10,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.prepared_three_occasions",
        "label": "Three occasions planned ahead",
        "description": "You had an outfit ready before the day for three occasions.",
        "behaviour": "prepared_for_occasion",
        "threshold": 3,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.avoided_duplicate",
        "label": "A duplicate purchase avoided",
        "description": "You checked before buying and already owned something that did the job.",
        "behaviour": "avoided_duplicate_purchase",
        "threshold": 1,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.no_buy_thirty",
        "label": "Thirty days into your no-buy",
        "description": "Thirty days since you started a no-buy period, with nothing added.",
        "behaviour": "no_buy_maintained",
        "threshold": 30,
        "repeatable": False,
    },
    {
        "rule_id": "milestone.no_buy_ninety",
        "label": "Ninety days into your no-buy",
        "description": "Ninety days since you started a no-buy period, with nothing added.",
        "behaviour": "no_buy_maintained",
        "threshold": 90,
        "repeatable": False,
    },
    {
        "rule_id": "milestone.finished_before_expiry",
        "label": "A product finished before its date",
        "description": "You used something up rather than letting it go past its date.",
        "behaviour": "used_product_before_expiry",
        "threshold": 1,
        "repeatable": True,
    },
    {
        "rule_id": "milestone.catalogued_twenty",
        "label": "Twenty items catalogued",
        "description": "Twenty things recorded, which is enough for the suggestions to get useful.",
        "behaviour": "organised_inventory",
        "threshold": 20,
        "repeatable": False,
    },
    {
        "rule_id": "milestone.catalogued_fifty",
        "label": "Fifty items catalogued",
        "description": "Fifty things recorded.",
        "behaviour": "organised_inventory",
        "threshold": 50,
        "repeatable": False,
    },
    {
        "rule_id": "milestone.reached_a_goal",
        "label": "A goal you set yourself, reached",
        "description": "You reached a target you set for yourself.",
        "behaviour": "reached_own_goal",
        "threshold": 1,
        "repeatable": True,
    },
]


def _assert_milestone_rules_are_reachable() -> None:
    """Refuse to seed a milestone rule that could never be awarded.

    ``progress.service.record_behaviour`` rejects any behaviour outside
    ``milestones.REWARDABLE_BEHAVIOURS``, so a rule naming something else is
    dead data — and a rule naming a ``NEVER_REWARDABLE`` behaviour would be an
    engagement reward, which this product does not ship.
    """
    from app.domains.progress import milestones as milestone_registry

    seeded_ids = {row["rule_id"] for row in MILESTONE_RULES}
    registry_ids = {rule.rule_id for rule in milestone_registry.RULES}
    if seeded_ids != registry_ids:
        raise AssertionError(
            "Seeded milestone rules have drifted from "
            f"progress.milestones.RULES: {seeded_ids ^ registry_ids}"
        )
    for row in MILESTONE_RULES:
        if row["behaviour"] not in milestone_registry.REWARDABLE_BEHAVIOURS:
            raise AssertionError(
                f"Milestone rule '{row['rule_id']}' names behaviour "
                f"'{row['behaviour']}', which can never be recorded."
            )


async def seed_progress(session: AsyncSession) -> int:
    from app.domains.progress.models import MetricDefinition, MilestoneRule

    _assert_milestone_rules_are_reachable()

    seeded = 0
    for row in _metric_definitions():
        existing = (await session.execute(
            select(MetricDefinition).where(
                MetricDefinition.key == row["key"],
                MetricDefinition.formula_version == row["formula_version"],
            )
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
def _feature_flag_defaults() -> list[tuple[str, bool, str]]:
    """The private-beta flag set, taken from the flag registry.

    ``app.shared.flags.service`` owns the list of flags the code understands
    (``KNOWN_FLAGS``) and what each should be in the private beta
    (``STABLE_BETA_DEFAULTS``). A hand-maintained copy here drifted: it seeded
    ``v2_virtual_try_on`` — a key nothing reads — and omitted three flags the
    code knows about, so those rows never appeared in the database at all.
    """
    from app.shared.flags.service import KNOWN_FLAGS, STABLE_BETA_DEFAULTS

    return [
        (key, bool(STABLE_BETA_DEFAULTS.get(key, False)), description)
        for key, description in KNOWN_FLAGS.items()
    ]


async def seed_feature_flags(session: AsyncSession) -> int:
    from app.shared.flags.models import FeatureFlag

    seeded = 0
    for key, enabled, description in _feature_flag_defaults():
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
# Inventory subtypes — per-category attribute schema
# ---------------------------------------------------------------------------

# One row per (category, subtype). ``required``/``optional`` are lists of
# attribute keys the item form is allowed to collect. Kept intentionally
# small; production loads a broader catalogue but the seed here is enough
# for the routine engine and the shopping evaluation to reason about.
INVENTORY_SUBTYPES: list[dict] = [
    # Wardrobe
    {"cat": "wardrobe", "key": "top", "name": "Top",
     "req": ["colour"], "opt": ["fabric", "fit", "occasion", "season"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "wardrobe", "key": "bottom", "name": "Bottom",
     "req": ["colour"], "opt": ["fabric", "fit", "occasion", "season"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "wardrobe", "key": "outerwear", "name": "Outerwear",
     "req": ["colour"], "opt": ["fabric", "fit", "occasion", "season", "warmth"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "wardrobe", "key": "dress", "name": "Dress",
     "req": ["colour"], "opt": ["fabric", "fit", "occasion", "season"],
     "expiry": False, "condition": True, "usage": True},
    # Shoes
    {"cat": "shoes", "key": "everyday", "name": "Everyday",
     "req": ["colour"], "opt": ["material", "occasion", "size"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "shoes", "key": "formal", "name": "Formal",
     "req": ["colour"], "opt": ["material", "size"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "shoes", "key": "athletic", "name": "Athletic",
     "req": ["colour"], "opt": ["material", "size"],
     "expiry": False, "condition": True, "usage": True},
    # Accessories
    {"cat": "accessories", "key": "bag", "name": "Bag",
     "req": ["colour"], "opt": ["material", "occasion"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "accessories", "key": "jewellery", "name": "Jewellery",
     "req": [], "opt": ["material", "colour", "occasion"],
     "expiry": False, "condition": True, "usage": True},
    {"cat": "accessories", "key": "eyewear", "name": "Eyewear",
     "req": [], "opt": ["colour", "prescription"],
     "expiry": False, "condition": True, "usage": True},
    # Beauty shelf
    {"cat": "beauty", "key": "cleanser", "name": "Cleanser",
     "req": ["brand"], "opt": ["skin_type", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "beauty", "key": "moisturiser", "name": "Moisturiser",
     "req": ["brand"], "opt": ["skin_type", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "beauty", "key": "serum", "name": "Serum",
     "req": ["brand"], "opt": ["ingredients", "concentration_note"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "beauty", "key": "sunscreen", "name": "Sunscreen",
     "req": ["brand"], "opt": ["spf", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    # Hair shelf
    {"cat": "hair", "key": "shampoo", "name": "Shampoo",
     "req": ["brand"], "opt": ["hair_type", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "hair", "key": "conditioner", "name": "Conditioner",
     "req": ["brand"], "opt": ["hair_type", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "hair", "key": "treatment", "name": "Treatment",
     "req": ["brand"], "opt": ["hair_type", "ingredients"],
     "expiry": True, "condition": False, "usage": True},
    # Perfumes
    {"cat": "perfumes", "key": "eau_de_parfum", "name": "Eau de Parfum",
     "req": ["brand"], "opt": ["family", "occasion", "climate"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "perfumes", "key": "eau_de_toilette", "name": "Eau de Toilette",
     "req": ["brand"], "opt": ["family", "occasion", "climate"],
     "expiry": True, "condition": False, "usage": True},
    # Supplements
    {"cat": "supplements", "key": "vitamin", "name": "Vitamin",
     "req": ["brand"], "opt": ["ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "supplements", "key": "mineral", "name": "Mineral",
     "req": ["brand"], "opt": ["ingredients"],
     "expiry": True, "condition": False, "usage": True},
    {"cat": "supplements", "key": "other", "name": "Other",
     "req": ["brand"], "opt": ["ingredients"],
     "expiry": True, "condition": False, "usage": True},
]


async def seed_inventory_subtypes(session: AsyncSession) -> int:
    from app.domains.reference import InventorySubtypeDefinition

    seeded = 0
    for row in INVENTORY_SUBTYPES:
        existing = (await session.execute(
            select(InventorySubtypeDefinition).where(
                InventorySubtypeDefinition.category_key == row["cat"],
                InventorySubtypeDefinition.subtype_key == row["key"],
            )
        )).scalar_one_or_none()
        req = ",".join(row["req"])
        opt = ",".join(row["opt"])
        if existing is None:
            session.add(InventorySubtypeDefinition(
                category_key=row["cat"],
                subtype_key=row["key"],
                display_name=row["name"],
                required_attributes=req,
                optional_attributes=opt,
                supports_expiry=row["expiry"],
                supports_condition=row["condition"],
                supports_usage=row["usage"],
                version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.display_name = row["name"]
            existing.required_attributes = req
            existing.optional_attributes = opt
            existing.supports_expiry = row["expiry"]
            existing.supports_condition = row["condition"]
            existing.supports_usage = row["usage"]
            existing.version = SEED_VERSION
    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Routine templates + ordered steps (skincare + hair-care)
# ---------------------------------------------------------------------------

# The existing ``routine_templates`` table (in routines/models.py) holds
# ``kind``, ``label``, ``frequency`` and ``slots`` (JSONB). The new
# ``routine_template_steps`` table adds the ordered display list a UI can
# render. Every template + step is versioned so an operator can trace which
# release wrote which guidance.

# (kind, label, frequency, slots)
ROUTINE_TEMPLATE_DEFS: list[tuple[str, str, str, list[str]]] = [
    ("morning_minimal", "Minimal Morning", "daily",
     ["cleanser", "moisturiser", "spf"]),
    ("evening_minimal", "Minimal Evening", "daily",
     ["cleanser", "moisturiser"]),
    ("hydration_focus", "Hydration Focus", "daily",
     ["cleanser", "hydrating_serum", "moisturiser"]),
    ("oil_control", "Oil Control", "daily",
     ["cleanser", "niacinamide_serum", "moisturiser"]),
    ("sensitive_gentle", "Sensitive Skin", "daily",
     ["gentle_cleanser", "moisturiser"]),
    ("climate_hot_humid", "Hot & Humid", "daily",
     ["cleanser", "lightweight_moisturiser", "spf"]),
    ("climate_cold_dry", "Cold & Dry", "daily",
     ["cleanser", "rich_moisturiser", "occlusive"]),
    ("beginner", "Beginner Basics", "daily",
     ["cleanser", "moisturiser", "spf"]),
    ("shelf_use", "Use What You Own", "daily",
     ["cleanser", "serum", "moisturiser"]),
    # Hair care
    ("hair_wash_day", "Wash Day", "twice_weekly",
     ["shampoo", "conditioner", "leave_in"]),
    ("hair_non_wash_day", "Non-wash Day", "daily",
     ["refresh", "style"]),
    ("hair_dryness_support", "Dryness Support", "twice_weekly",
     ["gentle_shampoo", "deep_conditioner"]),
    ("hair_oil_control", "Hair Oil Control", "three_times_weekly",
     ["clarifying_shampoo", "lightweight_conditioner"]),
    ("hair_scalp_care", "Scalp Care", "weekly",
     ["scalp_treatment", "gentle_shampoo", "conditioner"]),
    ("hair_climate_aware", "Climate-aware Hair", "twice_weekly",
     ["gentle_shampoo", "conditioner", "leave_in"]),
    ("hair_beginner", "Hair Beginner", "twice_weekly",
     ["shampoo", "conditioner"]),
    ("hair_shelf_use", "Use What You Own (Hair)", "twice_weekly",
     ["shampoo", "conditioner"]),
]

# Steps rendered by the app. Each (template_kind, position, step_key, name, guidance, optional).
ROUTINE_STEP_DEFS: list[tuple[str, int, str, str, str, bool]] = [
    # Minimal morning
    ("morning_minimal", 1, "cleanser", "Gentle cleanse",
     "Lather a small amount of gentle cleanser and rinse. Skip if you cleansed within the last hour.", False),
    ("morning_minimal", 2, "moisturiser", "Moisturise",
     "Apply a light moisturiser to damp skin.", False),
    ("morning_minimal", 3, "spf", "Broad-spectrum SPF",
     "Finish with broad-spectrum SPF. Daytime routines always finish with SPF.", False),
    # Minimal evening
    ("evening_minimal", 1, "cleanser", "Cleanse",
     "Remove the day gently. A second cleanse is optional if you wore SPF or makeup.", False),
    ("evening_minimal", 2, "moisturiser", "Moisturise",
     "Apply a moisturiser suited to your skin type.", False),
    # Sensitive
    ("sensitive_gentle", 1, "gentle_cleanser", "Gentle cleanse",
     "Use a fragrance-free cleanser and lukewarm water. Pat dry — no rubbing.", False),
    ("sensitive_gentle", 2, "moisturiser", "Moisturise",
     "Apply while skin is still damp. Fragrance-free is a safer default.", False),
    # Climate hot & humid
    ("climate_hot_humid", 1, "cleanser", "Cleanse",
     "A gel or foam cleanser keeps things comfortable in humidity.", False),
    ("climate_hot_humid", 2, "lightweight_moisturiser", "Light moisturiser",
     "Reach for a gel or lotion; skip anything that feels heavy.", False),
    ("climate_hot_humid", 3, "spf", "Broad-spectrum SPF",
     "Even in humidity, finish with SPF. Reapply if you're outside.", False),
    # Hair wash day
    ("hair_wash_day", 1, "shampoo", "Shampoo",
     "Focus on the scalp. Rinse thoroughly.", False),
    ("hair_wash_day", 2, "conditioner", "Conditioner",
     "Focus on mid-lengths and ends. Rinse after a couple of minutes.", False),
    ("hair_wash_day", 3, "leave_in", "Leave-in",
     "Distribute a small amount through damp lengths and ends.", True),
    # Beginner
    ("beginner", 1, "cleanser", "Cleanse",
     "One gentle wash, morning and evening.", False),
    ("beginner", 2, "moisturiser", "Moisturise",
     "Apply while skin is damp to lock in hydration.", False),
    ("beginner", 3, "spf", "Broad-spectrum SPF",
     "Add SPF during the day — this is the single highest-impact step.", False),
    # Hydration focus
    ("hydration_focus", 1, "cleanser", "Cream cleanse",
     "A cream or milk cleanser preserves the barrier while it removes the day.", False),
    ("hydration_focus", 2, "hydrating_serum", "Hydrating serum",
     "Layer a humectant serum (hyaluronic acid or glycerin) on damp skin.", False),
    ("hydration_focus", 3, "moisturiser", "Moisturise",
     "Seal it in with a barrier-supportive moisturiser.", False),
    ("hydration_focus", 4, "spf", "Broad-spectrum SPF",
     "Daytime always finishes with SPF.", False),
    # Oil control
    ("oil_control", 1, "cleanser", "Gel cleanse",
     "A gel or foaming cleanser suits skin that reads oily by mid-day.", False),
    ("oil_control", 2, "bha_serum", "BHA serum",
     "A gentle salicylic acid serum a few evenings a week helps pores stay clear.", True),
    ("oil_control", 3, "light_moisturiser", "Light moisturiser",
     "Reach for a fluid or gel moisturiser — nothing heavy.", False),
    ("oil_control", 4, "spf", "Broad-spectrum SPF",
     "Even oily skin needs SPF — pick a matte or gel-textured one.", False),
    # Climate cold & dry
    ("climate_cold_dry", 1, "cream_cleanser", "Cream cleanse",
     "A cream or oil cleanser is kinder to the barrier in cold air.", False),
    ("climate_cold_dry", 2, "hydrating_layer", "Hydrating layer",
     "A humectant serum draws moisture in before your cream seals it.", False),
    ("climate_cold_dry", 3, "rich_moisturiser", "Rich moisturiser",
     "Reach for something occlusive — a balm on drier areas is fine.", False),
    ("climate_cold_dry", 4, "spf", "Broad-spectrum SPF",
     "Sun still matters in winter — a hydrating SPF finishes the routine.", False),
    # Shelf-use (based on already-owned products)
    ("shelf_use", 1, "cleanser", "Cleanse with what you own",
     "Use the gentlest cleanser on your shelf — the one you reach for on tired days.", False),
    ("shelf_use", 2, "actives_if_owned", "Actives you already have",
     "If your shelf includes a serum, apply it now. Skip if you have none.", True),
    ("shelf_use", 3, "moisturiser", "Moisturise",
     "Whichever moisturiser you already own — pick the one that feels comfortable.", False),
    ("shelf_use", 4, "spf", "SPF you own",
     "Any SPF you already own works better than none. Reapply outdoors.", False),
    # Hair non-wash day
    ("hair_non_wash_day", 1, "dry_refresh", "Dry refresh",
     "A quick brush and, if useful, a spritz of water or refresher spray at the roots.", False),
    ("hair_non_wash_day", 2, "leave_in", "Leave-in on lengths",
     "A small amount of leave-in on mid-lengths and ends keeps hair happy between washes.", True),
    # Hair dryness support
    ("hair_dryness_support", 1, "pre_wash_oil", "Pre-wash oil",
     "Warm a small amount of oil through lengths and ends 20 minutes before shampoo.", True),
    ("hair_dryness_support", 2, "hydrating_shampoo", "Hydrating shampoo",
     "A moisture-focused shampoo — focus on the scalp and let the run-through clean lengths.", False),
    ("hair_dryness_support", 3, "deep_conditioner", "Deep conditioner",
     "Apply mid-lengths to ends, comb through, leave for a few minutes, then rinse.", False),
    ("hair_dryness_support", 4, "leave_in", "Leave-in on damp hair",
     "Distribute leave-in through damp lengths before styling.", False),
    # Hair oil control
    ("hair_oil_control", 1, "clarifying_shampoo", "Clarifying shampoo",
     "A clarifying wash once a week resets an oily scalp — daily-use shampoo the rest of the time.", True),
    ("hair_oil_control", 2, "light_conditioner", "Light conditioner",
     "Condition mid-lengths and ends only. Skip the scalp.", False),
    ("hair_oil_control", 3, "no_heavy_oil", "Skip heavy oils near the scalp",
     "Reserve rich oils for the ends only; the scalp does its own oiling.", False),
    # Hair scalp care
    ("hair_scalp_care", 1, "scalp_massage", "Scalp massage",
     "A few minutes of massage before shampoo supports circulation.", True),
    ("hair_scalp_care", 2, "gentle_shampoo", "Gentle shampoo",
     "Use a fragrance-light shampoo; work into the scalp only, not the lengths.", False),
    ("hair_scalp_care", 3, "scalp_serum", "Scalp serum",
     "A leave-on scalp serum on non-wash days if your shelf includes one.", True),
    # Hair climate aware
    ("hair_climate_aware", 1, "wash_frequency_adjust", "Adjust wash frequency to climate",
     "Wash more often in humidity, less in cold and dry weather.", False),
    ("hair_climate_aware", 2, "humidity_serum", "Anti-humidity serum",
     "In humid weather, a light smoothing serum keeps frizz down.", True),
    ("hair_climate_aware", 3, "winter_oil", "Winter nourishment",
     "In cold weather, add a nourishing mask or richer conditioner once a week.", True),
    # Hair beginner
    ("hair_beginner", 1, "shampoo", "Shampoo",
     "Wash the scalp with a small amount of shampoo and rinse thoroughly.", False),
    ("hair_beginner", 2, "conditioner", "Conditioner on lengths",
     "Condition from mid-length to ends and rinse after a couple of minutes.", False),
    ("hair_beginner", 3, "brush", "Detangle gently",
     "Detangle damp hair from the ends upward with a wide-tooth comb.", False),
    # Hair shelf-use
    ("hair_shelf_use", 1, "shampoo_you_own", "Shampoo you already own",
     "Use the shampoo that suits your scalp best from what you already have.", False),
    ("hair_shelf_use", 2, "conditioner_you_own", "Conditioner you already own",
     "Whatever conditioner is on your shelf — focus on mid-lengths and ends.", False),
    ("hair_shelf_use", 3, "leave_in_or_oil", "Leave-in or hair oil",
     "If you own a leave-in or hair oil, finish with a small amount on the ends.", True),
]


async def seed_routine_templates(session: AsyncSession) -> int:
    """Upsert the existing ``routine_templates`` rows and the new step rows.

    The template table already exists (Package A schema). This seed keeps
    it aligned with the shipped templates and adds the ordered steps table
    that gives the UI a versioned rendering surface.
    """
    from app.domains.reference import RoutineTemplateStep
    from app.domains.routines.models import RoutineTemplate

    seeded = 0
    for kind, label, frequency, slots in ROUTINE_TEMPLATE_DEFS:
        existing = (await session.execute(
            select(RoutineTemplate).where(RoutineTemplate.kind == kind)
        )).scalar_one_or_none()
        if existing is None:
            session.add(RoutineTemplate(
                kind=kind,
                label=label,
                frequency=frequency,
                slots=slots,
                knowledge_version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.label = label
            existing.frequency = frequency
            existing.slots = slots
            existing.knowledge_version = SEED_VERSION

    await session.flush()

    for kind, pos, step_key, name, guidance, optional in ROUTINE_STEP_DEFS:
        existing = (await session.execute(
            select(RoutineTemplateStep).where(
                RoutineTemplateStep.template_kind == kind,
                RoutineTemplateStep.position == pos,
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(RoutineTemplateStep(
                template_kind=kind, position=pos, step_key=step_key,
                display_name=name, guidance=guidance, optional=optional,
                version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.step_key = step_key
            existing.display_name = name
            existing.guidance = guidance
            existing.optional = optional
            existing.version = SEED_VERSION

    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Perfume context — occasions, time-of-day, climate, layering
# ---------------------------------------------------------------------------

# The existing ``perfume_context_rules`` table (in routines/models.py) has
# ``rule_id``, ``factor``, ``match_value``, ``families``, ``note``.

PERFUME_CONTEXT_DEFS: list[dict] = [
    # Occasions
    {"rule_id": "occ_work", "factor": "occasion", "match_value": "work",
     "families": ["fresh", "clean", "citrus"],
     "note": "A restrained, fresh scent tends to sit well in a shared workspace."},
    {"rule_id": "occ_casual", "factor": "occasion", "match_value": "casual_day",
     "families": ["fresh", "green", "citrus"],
     "note": "Light and easygoing suits a daytime, low-formality moment."},
    {"rule_id": "occ_evening", "factor": "occasion", "match_value": "evening",
     "families": ["woody", "amber", "oriental"],
     "note": "A warmer, richer family adds presence for the evening."},
    {"rule_id": "occ_celebration", "factor": "occasion", "match_value": "celebration",
     "families": ["floral", "oriental", "amber"],
     "note": "A memorable, festive family suits a celebration."},
    {"rule_id": "occ_formal", "factor": "occasion", "match_value": "formal",
     "families": ["woody", "clean", "chypre"],
     "note": "Refined and elegant families suit a formal setting."},
    # Time of day
    {"rule_id": "tod_morning", "factor": "time_of_day", "match_value": "morning",
     "families": ["citrus", "fresh", "green"],
     "note": "Brighter families feel appropriate in the first half of the day."},
    {"rule_id": "tod_evening", "factor": "time_of_day", "match_value": "evening",
     "families": ["woody", "amber", "oriental"],
     "note": "Warmer families tend to shine in the evening."},
    # Climate
    {"rule_id": "clim_hot", "factor": "climate", "match_value": "hot_humid",
     "families": ["citrus", "fresh", "aquatic"],
     "note": "Lighter families sit more comfortably in heat and humidity."},
    {"rule_id": "clim_cold", "factor": "climate", "match_value": "cold_dry",
     "families": ["woody", "amber", "gourmand"],
     "note": "Denser families project better in cold air."},
    # Intensity
    {"rule_id": "int_close", "factor": "intensity", "match_value": "close_contact",
     "families": ["fresh", "clean", "citrus"],
     "note": "Close-contact settings suit a subtle, wearable trail."},
    {"rule_id": "int_outdoor", "factor": "intensity", "match_value": "outdoor",
     "families": ["woody", "aromatic"],
     "note": "Outdoor projection welcomes a slightly stronger scent."},
    # Layering + application safety (advisory)
    {"rule_id": "lay_note", "factor": "layering", "match_value": "same_family",
     "families": ["compatible"],
     "note": "Layering scents in the same family tends to reinforce, not clash."},
    {"rule_id": "app_safety", "factor": "application_safety", "match_value": "skin",
     "families": ["caution"],
     "note": "Perfume goes on pulse points; keep it away from eyes and broken skin."},
]


async def seed_perfume_context(session: AsyncSession) -> int:
    from app.domains.routines.models import PerfumeContextRule

    seeded = 0
    for row in PERFUME_CONTEXT_DEFS:
        existing = (await session.execute(
            select(PerfumeContextRule).where(
                PerfumeContextRule.rule_id == row["rule_id"]
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(PerfumeContextRule(
                rule_id=row["rule_id"],
                factor=row["factor"],
                match_value=row["match_value"],
                families=row["families"],
                note=row["note"],
            ))
            seeded += 1
        else:
            existing.factor = row["factor"]
            existing.match_value = row["match_value"]
            existing.families = row["families"]
            existing.note = row["note"]
    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Supplement / food appearance context — general wellness only
# ---------------------------------------------------------------------------

# Safety category is one of:
#   - ``general_wellness`` — safe general context, no professional gate.
#   - ``consult_professional`` — mention that professional guidance helps.
#   - ``pregnancy_flag`` — a clear flag to check with a professional.
# Never diagnosis, dosage, or medication substitution.

SUPPLEMENT_CONTEXT_DEFS: list[dict] = [
    {"key": "hydration", "name": "Hydration",
     "guidance": "Even hydration through the day is one of the simplest levers for how skin and hair feel. Water is boring advice; it also happens to be true.",
     "safety": "general_wellness"},
    {"key": "protein_general", "name": "Protein (general context)",
     "guidance": "Regular protein intake supports hair strength and recovery. Reach for foods that fit your routine rather than a specific number.",
     "safety": "general_wellness"},
    {"key": "micronutrients", "name": "Micronutrient awareness",
     "guidance": "Variety across meals is more valuable than any single hero ingredient. If you're considering a supplement, a professional can help make it worth your time.",
     "safety": "consult_professional"},
    {"key": "sleep_recovery", "name": "Sleep and recovery",
     "guidance": "A consistent sleep window supports skin, hair and mood. Small nudges beat big overhauls.",
     "safety": "general_wellness"},
    {"key": "routine_consistency", "name": "Routine consistency",
     "guidance": "Consistency compounds. Two steps every day beat five steps sometimes.",
     "safety": "general_wellness"},
    {"key": "pregnancy_general", "name": "Pregnancy context",
     "guidance": "If you're pregnant or trying, some ingredients call for a professional's input before you start or continue them.",
     "safety": "pregnancy_flag"},
    {"key": "safety_disclaimer", "name": "General safety disclaimer",
     "guidance": "This app shares general appearance context, not medical guidance. For anything that needs a professional, please talk to one.",
     "safety": "consult_professional"},
]


async def seed_supplement_context(session: AsyncSession) -> int:
    from app.domains.reference import SupplementContextRule

    seeded = 0
    for row in SUPPLEMENT_CONTEXT_DEFS:
        rule_id = f"supp_{row['key']}"
        existing = (await session.execute(
            select(SupplementContextRule).where(
                SupplementContextRule.rule_id == rule_id
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(SupplementContextRule(
                rule_id=rule_id,
                context_key=row["key"],
                display_name=row["name"],
                guidance=row["guidance"],
                safety_category=row["safety"],
                version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.display_name = row["name"]
            existing.guidance = row["guidance"]
            existing.safety_category = row["safety"]
            existing.version = SEED_VERSION
    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Ingredient contraindications + sensitivities
# ---------------------------------------------------------------------------

# Contraindications are stronger than compatibility rules: "do not use this
# ingredient in this condition." Sensitivity rules are the softer counterpart.

CONTRAINDICATION_DEFS: list[dict] = [
    {"ing": "retinol", "cond": "pregnancy",
     "headline": "Retinol is generally avoided during pregnancy.",
     "guidance": "During pregnancy or if trying, most professionals recommend pausing retinoids. A dermatologist or midwife can suggest alternatives."},
    {"ing": "retinoid", "cond": "pregnancy",
     "headline": "Prescription retinoids are avoided during pregnancy.",
     "guidance": "Please pause and speak with the professional who prescribed it."},
    {"ing": "aha", "cond": "compromised_skin_barrier",
     "headline": "Skip AHAs when the barrier is inflamed.",
     "guidance": "Give sensitised skin a break — a gentler routine helps more than active ingredients here."},
    {"ing": "bha", "cond": "compromised_skin_barrier",
     "headline": "Skip BHAs when the barrier is inflamed.",
     "guidance": "Focus on cleanse-moisturise-SPF while your skin recovers."},
    {"ing": "benzoyl_peroxide", "cond": "pregnancy",
     "headline": "Benzoyl peroxide during pregnancy calls for professional input.",
     "guidance": "Ask a dermatologist or GP before continuing."},
]


async def seed_contraindications(session: AsyncSession) -> int:
    from app.domains.reference import IngredientContraindicationRule

    seeded = 0
    for row in CONTRAINDICATION_DEFS:
        rule_id = f"{row['ing']}__{row['cond']}"
        existing = (await session.execute(
            select(IngredientContraindicationRule).where(
                IngredientContraindicationRule.rule_id == rule_id
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(IngredientContraindicationRule(
                rule_id=rule_id,
                ingredient_key=row["ing"],
                condition_key=row["cond"],
                headline=row["headline"],
                guidance=row["guidance"],
                version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.headline = row["headline"]
            existing.guidance = row["guidance"]
            existing.version = SEED_VERSION
    await session.flush()
    return seeded


SENSITIVITY_DEFS: list[dict] = [
    {"ing": "retinol", "sens": "sensitive_skin",
     "headline": "Retinol on sensitive skin: start slow.",
     "guidance": "Twice a week for two weeks, then evaluate. Buffer with moisturiser if needed."},
    {"ing": "vitamin_c", "sens": "sensitive_skin",
     "headline": "Vitamin C on sensitive skin: choose the formulation carefully.",
     "guidance": "Lower concentrations and pH-buffered formulations are usually kinder."},
    {"ing": "aha", "sens": "sensitive_skin",
     "headline": "AHAs on sensitive skin: keep frequency low.",
     "guidance": "Once or twice a week is often enough. Always finish daytime routines with SPF."},
    {"ing": "spf", "sens": "physical_only_preference",
     "headline": "Physical SPF is a good choice for reactive skin.",
     "guidance": "Zinc or titanium formulas can suit sensitive skin better than some chemical filters."},
]


async def seed_sensitivities(session: AsyncSession) -> int:
    from app.domains.reference import IngredientSensitivityRule

    seeded = 0
    for row in SENSITIVITY_DEFS:
        rule_id = f"{row['ing']}__{row['sens']}"
        existing = (await session.execute(
            select(IngredientSensitivityRule).where(
                IngredientSensitivityRule.rule_id == rule_id
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(IngredientSensitivityRule(
                rule_id=rule_id,
                ingredient_key=row["ing"],
                sensitivity_key=row["sens"],
                headline=row["headline"],
                guidance=row["guidance"],
                version=SEED_VERSION,
            ))
            seeded += 1
        else:
            existing.headline = row["headline"]
            existing.guidance = row["guidance"]
            existing.version = SEED_VERSION
    await session.flush()
    return seeded


# ---------------------------------------------------------------------------
# Seed-version audit trail
# ---------------------------------------------------------------------------


async def record_seed_version(session: AsyncSession, counts: dict) -> None:
    """Write one audit row per seed domain for this run."""
    from datetime import datetime

    from app.domains.reference import SeedVersionRecord

    now = datetime.now(UTC)
    for domain, count in counts.items():
        existing = (await session.execute(
            select(SeedVersionRecord).where(
                SeedVersionRecord.seed_domain == domain,
                SeedVersionRecord.seed_version == SEED_VERSION,
            )
        )).scalar_one_or_none()
        if existing is None:
            session.add(SeedVersionRecord(
                seed_domain=domain,
                seed_version=SEED_VERSION,
                applied_at=now,
                rows_written=count,
            ))
        else:
            existing.applied_at = now
            existing.rows_written = count
    await session.flush()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run(session: AsyncSession) -> dict:
    counts = {
        "inventory_categories": await seed_inventory_categories(session),
        "inventory_subtypes": await seed_inventory_subtypes(session),
        "ingredients_and_rules": await seed_ingredients(session),
        "ingredient_contraindications": await seed_contraindications(session),
        "ingredient_sensitivities": await seed_sensitivities(session),
        "routine_templates": await seed_routine_templates(session),
        "perfume_context": await seed_perfume_context(session),
        "supplement_context": await seed_supplement_context(session),
        "progress": await seed_progress(session),
        "feature_flags": await seed_feature_flags(session),
    }
    await record_seed_version(session, counts)
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
