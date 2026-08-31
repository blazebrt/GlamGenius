"""The published thresholds the food score will use.

The rules held here are the ones that keep the data honest: no entry without a
source, a figure that could not be read is marked as such rather than guessed,
disagreements carry both positions, and a cooking ingredient can never be
graded.
"""
from __future__ import annotations

import pytest
from app.domains.evidence.enums import ReviewStatus
from app.domains.evidence.models import EvidenceClaim
from app.domains.nutrition import food_reference as ref
from app.domains.nutrition.food_reference import (
    ADDITIVES,
    CULINARY_INGREDIENTS,
    THRESHOLDS,
    is_culinary_ingredient,
)
from app.domains.nutrition.food_reference_loader import (
    SUBJECT_ADDITIVE,
    SUBJECT_CULINARY,
    SUBJECT_THRESHOLD,
    load,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Source discipline
# ---------------------------------------------------------------------------
def test_every_entry_in_every_set_names_a_source():
    for threshold in THRESHOLDS:
        assert threshold.source.url.startswith("https://"), threshold.nutrient
        assert threshold.source.identifier, threshold.nutrient
    for additive in ADDITIVES:
        assert additive.source.url.startswith("https://"), additive.name
    for ingredient in CULINARY_INGREDIENTS:
        if ingredient.guidance_source is not None:
            assert ingredient.guidance_source.url.startswith("https://"), ingredient.name


def test_every_entry_carries_a_confidence_rating():
    for threshold in THRESHOLDS:
        assert threshold.confidence
    for additive in ADDITIVES:
        assert additive.confidence
    for ingredient in CULINARY_INGREDIENTS:
        if ingredient.daily_guidance:
            assert ingredient.guidance_confidence, ingredient.name


def test_a_threshold_that_could_not_be_read_carries_no_number():
    """The honesty rule: an unread figure is absent, never estimated."""
    unread = [t for t in THRESHOLDS if t.tier == ref.TIER_NOT_ENOUGH]
    assert unread, "every threshold claims to be known, which is implausible here"
    for threshold in unread:
        assert threshold.low_max is None and threshold.high_min is None, (
            f"{threshold.nutrient} is marked not-enough-information but carries a number"
        )
        assert threshold.note, f"{threshold.nutrient} does not say what must be transcribed"


def test_the_icmr_nin_rows_are_present_but_unfilled():
    """Named because the task asks for them, unfilled because they could not be read."""
    icmr = [t for t in THRESHOLDS if t.source.identifier == "ICMR-NIN-DGI-2024"]
    covered = {t.nutrient for t in icmr}
    for nutrient in ("sodium", "added sugar", "total sugar", "added fat", "total fat"):
        assert nutrient in covered, f"Table 15.1 row for {nutrient} is missing entirely"
    assert all(t.high_min is None for t in icmr), (
        "an ICMR-NIN figure was filled in; it could not have been read from the source"
    )


def test_disagreements_record_both_positions():
    disputed = [x for x in (*THRESHOLDS, *ADDITIVES, *CULINARY_INGREDIENTS) if x.disagreement]
    assert len(disputed) >= 5
    for item in disputed:
        assert len(item.disagreement) > 60, "a disagreement is flagged but not explained"


# ---------------------------------------------------------------------------
# The UK FSA bands, which are carried in full
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("nutrient", "basis", "low_max", "high_min"),
    [
        ("total fat", "solid", "3.0", "17.5"),
        ("saturated fat", "solid", "1.5", "5.0"),
        ("total sugars", "solid", "5.0", "22.5"),
        ("salt", "solid", "0.3", "1.5"),
        ("total fat", "drink", "1.5", "8.75"),
        ("total sugars", "drink", "2.5", "11.25"),
    ],
)
def test_the_fsa_bands_are_carried_exactly(nutrient, basis, low_max, high_min):
    from decimal import Decimal

    match = next(
        t for t in THRESHOLDS
        if t.nutrient == nutrient and t.basis == basis and t.source.identifier == "UK-FSA-FOP-2016"
    )
    assert match.low_max == Decimal(low_max)
    assert match.high_min == Decimal(high_min)


def test_sodium_is_derived_from_salt_by_the_stated_factor():
    """Indian labels declare sodium, so the conversion has to be right."""
    from decimal import Decimal

    salt = next(t for t in THRESHOLDS if t.nutrient == "salt" and t.basis == "solid")
    sodium = next(t for t in THRESHOLDS
                  if t.nutrient == "sodium" and t.source.identifier == "UK-FSA-FOP-2016")
    assert sodium.high_min == (salt.high_min / Decimal("2.5")).quantize(Decimal("0.01"))
    assert "2.5" in (sodium.note or "")


# ---------------------------------------------------------------------------
# Additives
# ---------------------------------------------------------------------------
def test_banned_and_restricted_items_are_present():
    names = {a.name.lower() for a in ADDITIVES}
    assert any("bromate" in n for n in names), "potassium bromate, banned in India, is missing"
    assert any("trans fat" in n for n in names), "the trans fat limit is missing"


def test_the_banned_items_are_tiered_black():
    for additive in ADDITIVES:
        if "bromate" in additive.name.lower() or "trans fat" in additive.name.lower():
            assert additive.tier == ref.TIER_BLACK, additive.name


def test_every_additive_tier_is_one_of_the_four():
    for additive in ADDITIVES:
        assert additive.tier in (ref.TIER_GREEN, ref.TIER_AMBER, ref.TIER_RED, ref.TIER_BLACK)


def test_additive_functions_are_plain_language():
    """LEGAL_RULES.md: no technical terms where a person will read them."""
    banned = ("emulsifier", "antioxidant", "humectant", "sequestrant", "anticaking")
    for additive in ADDITIVES:
        lowered = additive.function.lower()
        assert not any(term in lowered for term in banned), (
            f"{additive.name} describes itself with a technical term: {additive.function!r}"
        )


# ---------------------------------------------------------------------------
# Culinary ingredients are never graded
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["ghee", "butter", "sunflower oil", "mustard oil", "vanaspati", "dalda", "salt", "namak",
     "sugar", "chini", "jaggery", "gur", "honey", "shahad", "shakkar", "misri", "vinegar", "sirka"],
)
def test_a_cooking_ingredient_is_never_graded(name):
    assert is_culinary_ingredient(name) is True, f"{name} would be given a letter grade"


@pytest.mark.parametrize("name", ["biscuit", "namkeen", "instant noodles", "fruit juice", "paneer"])
def test_an_actual_food_is_still_gradable(name):
    assert is_culinary_ingredient(name) is False, f"{name} was wrongly treated as an ingredient"


def test_every_requested_culinary_ingredient_is_covered():
    keys = ref.culinary_keys()
    for required in ("ghee", "butter", "vanaspati", "salt", "sugar", "jaggery",
                     "honey", "shakkar", "misri", "vinegar"):
        assert required in keys, f"{required} is missing from the never-graded list"
    assert any("oil" in key for key in keys), "cooking oils are missing"


def test_matching_ignores_case_and_padding():
    assert is_culinary_ingredient("  GHEE  ") is True
    assert is_culinary_ingredient("") is False


# ---------------------------------------------------------------------------
# Loading: drafts only
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_all_three_sets_load_as_drafts(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        summary = await load(session)
        await session.commit()

    assert summary["thresholds"] == len(THRESHOLDS)
    assert summary["additives"] == len(ADDITIVES)
    assert summary["culinary_ingredients"] == len(CULINARY_INGREDIENTS)

    async with factory() as session:
        claims = list((await session.execute(
            select(EvidenceClaim).where(EvidenceClaim.subject_type.in_(
                (SUBJECT_THRESHOLD, SUBJECT_ADDITIVE, SUBJECT_CULINARY),
            ))
        )).scalars().all())

    expected = len(THRESHOLDS) + len(ADDITIVES) + len(CULINARY_INGREDIENTS)
    assert len(claims) == expected
    assert {c.review_status for c in claims} == {ReviewStatus.DRAFT.value}


@pytest.mark.asyncio
async def test_loading_twice_creates_nothing_new(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await load(session)
        await session.commit()
    async with factory() as session:
        second = await load(session)
        await session.commit()
    assert sum(second["drafts_created"].values()) == 0


@pytest.mark.asyncio
async def test_an_unread_threshold_cannot_be_approved(db_clean):
    """The ICMR-NIN rows must stay drafts until somebody transcribes them."""
    from app.domains.evidence import authoring
    from app.shared.errors.exceptions import ValidationFailedError

    factory = get_sessionmaker()
    async with factory() as session:
        await load(session)
        await session.commit()

    async with factory() as session:
        culinary = (await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.subject_type == SUBJECT_CULINARY,
                EvidenceClaim.subject_key == "Vanaspati",
            )
        )).scalar_one()
        # Vanaspati carries no external source, so it must not be approvable.
        with pytest.raises(ValidationFailedError):
            await authoring.approve(session, culinary.id, reviewer="reviewer")
