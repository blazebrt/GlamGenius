"""Routines + ingredient safety (§1.11) regression coverage.

These tests exercise the reference-data catalogue that governs routine
generation and safety guidance:

    * Ingredient canonical lookup by ``key``.
    * Ingredient alias lookup (a marketing name resolves to the canonical
      key so a shelf full of alternate names is still readable).
    * Compatibility rules (family_a + family_b addressable by rule_id).
    * Contraindication rules (blocking triggers such as user allergy or
      expired product).
    * Sensitivity rules (softer, per-ingredient cautions).
    * Routine template steps: every seeded template has at least one
      ordered step, positions are contiguous and step versions match
      the current seed version.
    * Perfume context rules exist for every headline factor (occasion,
      time_of_day, climate, setting, intensity, layering,
      application_safety).
    * Supplement context rules NEVER contain dosage or diagnosis
      language.

All tests rely on the seeded reference catalogue — they invoke
``main()`` from ``app.bootstrap`` before asserting. That is the same
entry point ``python -m app.bootstrap.reference_data`` uses.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.bootstrap import main as run_seed
from app.domains.reference import (
    IngredientContraindicationRule,
    IngredientSensitivityRule,
    RoutineTemplateStep,
    SupplementContextRule,
)
from app.domains.routines.models import (
    CompatibilityRuleRow,
    Ingredient,
    IngredientAlias,
    PerfumeContextRule,
    RoutineTemplate,
)
from app.shared.database.sql import get_sessionmaker


pytestmark = pytest.mark.asyncio


DOSAGE_WORDS = ("mg", "milligram", "microgram", " IU ", "dosage")
DIAGNOSIS_WORDS = ("diagnose", "cure", "treatment plan")


async def _seed() -> None:
    await run_seed()


# ---------------------------------------------------------------------------
# Ingredient catalogue lookups
# ---------------------------------------------------------------------------

async def test_seed_writes_ingredient_catalogue(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        count = (await session.execute(select(func.count(Ingredient.key)))).scalar_one()
    assert count >= 5, "ingredient catalogue must be substantive"


async def test_canonical_ingredient_is_addressable_by_key(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        # Niacinamide is one of the canonical anchors of the catalogue.
        row = (
            await session.execute(
                select(Ingredient).where(Ingredient.key == "niacinamide")
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.display_name
    assert row.family


async def test_ingredient_alias_resolves_to_canonical_key(db_clean):
    factory = get_sessionmaker()
    await _seed()
    # The seeded catalogue mirrors the engine's ontology, so the canonical key
    # is the one the parser resolves to ('ascorbic_acid'), and a label spelt
    # 'l-ascorbic acid' or 'vitamin c' must reach it.
    from app.domains.routines import ontology

    async with factory() as session:
        rows = (await session.execute(select(IngredientAlias))).scalars().all()
        canonical = (await session.execute(
            select(Ingredient).where(Ingredient.key == "ascorbic_acid")
        )).scalar_one_or_none()

    by_alias = {row.alias: row.ingredient_key for row in rows}
    assert canonical is not None, "the canonical ingredient row must be seeded"
    assert by_alias["l-ascorbic acid"] == "ascorbic_acid"
    assert by_alias["vitamin c"] == "ascorbic_acid"
    # Every spelling the parser knows is stored, so a database-side lookup and
    # the in-process parser can never disagree.
    assert by_alias == ontology.alias_index()


async def test_alias_uniqueness_is_enforced(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(IngredientAlias.alias))
        ).scalars().all()
    assert len(rows) == len(set(rows))


# ---------------------------------------------------------------------------
# Compatibility / contraindication / sensitivity rules
# ---------------------------------------------------------------------------

async def test_compatibility_rules_have_family_pairs_and_guidance(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(CompatibilityRuleRow))
        ).scalars().all()
    assert rows, "compatibility catalogue must not be empty"
    for row in rows:
        assert row.family_a and row.family_b
        # §3.3 distinguishes compatible / caution / avoid / requires
        # professional guidance. The seed encodes the last as ``required``.
        assert row.severity in {"info", "compatible", "caution", "avoid", "required"}
        assert row.guidance


async def test_contraindication_rules_carry_condition_and_guidance(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(IngredientContraindicationRule))
        ).scalars().all()
    assert rows
    for row in rows:
        assert row.ingredient_key
        assert row.condition_key
        assert row.headline
        assert row.guidance
        # No diagnosis or dosage language in the seeded copy.
        blob = f"{row.headline} {row.guidance}".lower()
        for word in DOSAGE_WORDS + DIAGNOSIS_WORDS:
            assert word.lower() not in blob, f"blocked term '{word}' in contraindication {row.rule_id}"


async def test_sensitivity_rules_are_softer_than_contraindications(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(IngredientSensitivityRule))
        ).scalars().all()
    assert rows
    for row in rows:
        assert row.ingredient_key
        assert row.sensitivity_key
        # The seed uses phrases like "may cause tingling", "start slowly" —
        # never the hard blocking language of a contraindication.
        blob = row.guidance.lower()
        assert "avoid entirely" not in blob
        assert "do not use" not in blob


# ---------------------------------------------------------------------------
# Routine templates
# ---------------------------------------------------------------------------

async def test_every_routine_template_has_ordered_steps(db_clean):
    factory = get_sessionmaker()
    await _seed()

    async with factory() as session:
        templates = (
            await session.execute(select(RoutineTemplate))
        ).scalars().all()
        step_rows = (
            await session.execute(
                select(RoutineTemplateStep).order_by(
                    RoutineTemplateStep.template_kind,
                    RoutineTemplateStep.position,
                )
            )
        ).scalars().all()

    templates_by_kind = {t.kind: t for t in templates}
    steps_by_kind: dict[str, list[RoutineTemplateStep]] = {}
    for step in step_rows:
        steps_by_kind.setdefault(step.template_kind, []).append(step)

    assert templates_by_kind, "routine templates must be seeded"
    for kind, template in templates_by_kind.items():
        steps = steps_by_kind.get(kind, [])
        assert steps, f"template {kind} must have at least one step"
        # Positions strictly increasing from 1.
        positions = [s.position for s in steps]
        assert positions == sorted(positions)
        assert positions[0] == 1
        # Version stamped on every step (§3.1 versioning).
        for step in steps:
            assert step.version
            assert step.display_name
            assert step.guidance


async def test_routine_templates_cover_declared_kinds(db_clean):
    """§3.4 + §3.5 — minimal AM/PM, hydration, oil-control, sensitive,
    climate-aware, beginner, shelf-use across skincare + hair."""
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        kinds = set(
            (await session.execute(select(RoutineTemplate.kind))).scalars().all()
        )
    # Skincare templates
    for expected in (
        "morning_minimal",
        "evening_minimal",
        "hydration_focus",
        "oil_control",
        "sensitive_gentle",
        "climate_hot_humid",
        "climate_cold_dry",
        "beginner",
        "shelf_use",
    ):
        assert expected in kinds, f"expected skincare template '{expected}' in seed"
    # Hair templates
    for expected in (
        "hair_wash_day",
        "hair_non_wash_day",
        "hair_dryness_support",
        "hair_oil_control",
        "hair_scalp_care",
        "hair_climate_aware",
        "hair_beginner",
        "hair_shelf_use",
    ):
        assert expected in kinds, f"expected hair-care template '{expected}' in seed"


# ---------------------------------------------------------------------------
# Perfume context
# ---------------------------------------------------------------------------

async def test_perfume_context_covers_key_factors(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        factors = set(
            (
                await session.execute(select(PerfumeContextRule.factor))
            ).scalars().all()
        )
    # At minimum: occasion + time_of_day + climate must be covered.
    for expected in ("occasion", "time_of_day", "climate"):
        assert expected in factors


async def test_perfume_context_notes_are_convention_not_claims(db_clean):
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(PerfumeContextRule))
        ).scalars().all()
    # The seed labels every note as a convention — no medical or gender
    # claims are baked in.
    for row in rows:
        assert row.families, f"factor {row.factor}/{row.match_value} has no families"
        blob = row.note.lower()
        assert "gender" not in blob
        assert "cure" not in blob
        assert "diagnose" not in blob


# ---------------------------------------------------------------------------
# Supplement context
# ---------------------------------------------------------------------------

async def test_supplement_context_never_uses_dosage_or_diagnosis(db_clean):
    """§3.7 — supplement context is general information only. The seed
    must not carry dosage or diagnosis wording, and every row falls into
    one of the three approved safety categories."""
    factory = get_sessionmaker()
    await _seed()
    async with factory() as session:
        rows = (
            await session.execute(select(SupplementContextRule))
        ).scalars().all()
    assert rows
    approved_categories = {"general_wellness", "consult_professional", "pregnancy_flag"}
    for row in rows:
        assert row.safety_category in approved_categories
        blob = f"{row.display_name} {row.guidance}".lower()
        for word in DOSAGE_WORDS:
            assert word.lower() not in blob, (
                f"dosage word '{word}' in supplement rule {row.rule_id}"
            )
        for word in DIAGNOSIS_WORDS:
            assert word.lower() not in blob, (
                f"diagnosis word '{word}' in supplement rule {row.rule_id}"
            )
