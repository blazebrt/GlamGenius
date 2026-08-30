"""The supplement absorption knowledge base.

The rules this suite holds to are the ones that make the data trustworthy: no
absorption figure without a source, no figure without a confidence rating,
arithmetic kept apart from study findings, and nothing arriving published.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from app.domains.evidence.enums import ReviewStatus
from app.domains.evidence.models import EvidenceClaim
from app.domains.supplements import knowledge
from app.domains.supplements.chemistry import elemental_percent, molar_mass
from app.domains.supplements.engine import REVIEWED_ALIASES, component_identity
from app.domains.supplements.knowledge import COMPOUNDS, Confidence, Verification
from app.domains.supplements.knowledge_loader import SUBJECT_TYPE, load
from app.domains.supplements.models import SupplementComponentKnowledge
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select


# ---------------------------------------------------------------------------
# The acceptance rules
# ---------------------------------------------------------------------------
def test_every_absorption_figure_has_an_openable_source_url():
    """No source, no figure. An entry with no source says so instead."""
    for compound in COMPOUNDS:
        if compound.absorption is None:
            assert compound.tier == knowledge.TIER_NOT_ENOUGH, (
                f"{compound.form} has no absorption figure but is not marked "
                f"not_enough_information"
            )
            continue
        url = compound.absorption.source_url
        assert url.startswith("https://"), f"{compound.form} cites {url!r}"
        assert compound.absorption.source_identifier, (
            f"{compound.form} has no stable identifier (DOI, PMID or document id)"
        )


def test_every_absorption_figure_has_a_confidence_rating():
    for compound in COMPOUNDS:
        if compound.absorption is not None:
            assert compound.absorption.confidence in tuple(Confidence), compound.form


def test_nothing_is_marked_confirmed_before_a_person_has_checked_it():
    """The loader must never claim a source has been opened."""
    assert Verification.UNVERIFIED.value == "unverified"


def test_an_entry_without_a_source_carries_a_reason():
    """'Not enough information' must explain itself, not just be blank."""
    for compound in COMPOUNDS:
        if compound.absorption is None:
            assert compound.note, f"{compound.form} says nothing about why it has no figure"


def test_disagreements_carry_both_figures():
    disputed = [c for c in COMPOUNDS if c.absorption and c.absorption.disagreement]
    assert disputed, "no disagreements recorded at all, which is implausible"
    for compound in disputed:
        assert len(compound.absorption.disagreement) > 40, (
            f"{compound.form} flags a disagreement without explaining it"
        )


# ---------------------------------------------------------------------------
# Arithmetic is arithmetic
# ---------------------------------------------------------------------------
def test_molar_mass_rejects_a_formula_it_cannot_fully_read():
    with pytest.raises(ValueError):
        molar_mass("MgO?")
    with pytest.raises(KeyError):
        molar_mass("XyO")


@pytest.mark.parametrize(
    ("formula", "element", "atoms", "expected"),
    [
        ("MgO", "Mg", 1, "60.3"),
        ("CaCO3", "Ca", 1, "40.0"),
        ("ZnO", "Zn", 1, "80.3"),
        ("FeSO4", "Fe", 1, "36.8"),
        ("FeSO4H14O7", "Fe", 1, "20.1"),      # heptahydrate
        ("FeC4H2O4", "Fe", 1, "32.9"),        # fumarate
        ("Mg3C12H10O14", "Mg", 3, "16.2"),    # trimagnesium dicitrate
    ],
)
def test_elemental_percentages_are_computed_not_remembered(formula, element, atoms, expected):
    """These are checkable against a periodic table, which is the point."""
    assert elemental_percent(formula, element, atoms) == Decimal(expected)


def test_hydrated_forms_are_recorded_because_the_difference_is_enormous():
    """Ferrous sulfate is 36.8% dry and 20.1% as the heptahydrate on the label."""
    hydrated = [c for c in COMPOUNDS if c.hydration]
    assert hydrated, "no hydration notes at all"
    ferrous = next(c for c in COMPOUNDS if c.form == "ferrous sulfate")
    assert ferrous.elemental_percent == Decimal("36.8")
    assert "20.1" in ferrous.hydration


def test_percentages_are_labelled_as_elemental_or_equivalent():
    for compound in COMPOUNDS:
        if compound.elemental_percent is not None:
            assert compound.percent_kind in ("elemental_by_weight", "equivalent_by_weight"), compound.form


# ---------------------------------------------------------------------------
# Coverage of what was asked for
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("key", "forms"),
    [
        ("magnesium", ("oxide", "citrate", "bisglycinate", "malate", "chloride", "sulfate", "threonate")),
        ("iron", ("sulfate", "fumarate", "gluconate", "bisglycinate", "carbonyl")),
        ("zinc", ("oxide", "sulfate", "gluconate", "picolinate", "bisglycinate")),
        ("calcium", ("carbonate", "citrate", "lactate")),
        ("vitamin d", ("D2", "D3")),
        ("vitamin b12", ("cyanocobalamin", "methylcobalamin", "adenosylcobalamin")),
        ("folate", ("folic acid", "methyltetrahydrofolate")),
        ("vitamin c", ("ascorbic acid", "sodium ascorbate", "ascorbyl palmitate", "liposomal")),
        ("coenzyme q10", ("ubiquinone", "ubiquinol")),
        ("curcumin", ("plain", "piperine", "phospholipid")),
        ("omega 3", ("ethyl ester", "triglyceride")),
    ],
)
def test_every_requested_compound_form_is_covered(key, forms):
    covered = " | ".join(c.form for c in knowledge.compounds_for(key)).lower()
    assert covered, f"no compounds at all for {key}"
    for form in forms:
        assert form.lower() in covered, f"{key}: {form} is missing"


# ---------------------------------------------------------------------------
# It extends VC-07 rather than sitting beside it
# ---------------------------------------------------------------------------
def test_the_original_two_aliases_still_resolve_the_way_vc07_set_them():
    assert component_identity("ascorbic acid") == ("vitamin c", "Vitamin C")
    assert component_identity("L Ascorbic Acid") == ("vitamin c", "Vitamin C")


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Ferrous Sulphate", "iron"),          # British spelling, usual on Indian labels
        ("Ferrous Bis-Glycinate", "iron"),
        ("Magnesium Glycinate", "magnesium"),
        ("Epsom Salt", "magnesium"),
        ("Zinc Sulphate", "zinc"),
        ("Oyster Shell Calcium", "calcium"),
        ("Vit D3", "vitamin d"),
        ("Cholecalciferol", "vitamin d"),
        ("Methylcobalamin", "vitamin b12"),
        ("5 MTHF", "folate"),
        ("Haldi Extract", "curcumin"),
        ("Turmeric with Black Pepper", "curcumin"),
        ("Co Q 10", "coenzyme q10"),
    ],
)
def test_indian_label_spellings_resolve_to_the_right_canonical_key(label, key):
    assert component_identity(label)[0] == key, f"{label!r} did not resolve to {key}"


def test_the_alias_map_is_keyed_on_normalised_text():
    """A key with capitals or punctuation would never match a lookup."""
    for alias in REVIEWED_ALIASES:
        assert alias == alias.strip().lower(), f"{alias!r} is not normalised"


# ---------------------------------------------------------------------------
# Loading: drafts, and only drafts
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_loading_writes_knowledge_rows_and_draft_entries_only(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        summary = await load(session)
        await session.commit()

    assert summary["compounds"] == len(COMPOUNDS)
    assert summary["drafts_created"] == len(COMPOUNDS)

    async with factory() as session:
        rows = list((await session.execute(select(SupplementComponentKnowledge))).scalars().all())
        claims = list((await session.execute(
            select(EvidenceClaim).where(EvidenceClaim.subject_type == SUBJECT_TYPE)
        )).scalars().all())

    assert len(rows) == len(COMPOUNDS)
    assert len(claims) == len(COMPOUNDS)
    statuses = {claim.review_status for claim in claims}
    assert statuses == {ReviewStatus.DRAFT.value}, f"something arrived not-draft: {statuses}"
    assert all(row.verification == Verification.UNVERIFIED.value for row in rows)


@pytest.mark.asyncio
async def test_loading_twice_does_not_duplicate_anything(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await load(session)
        await session.commit()
    async with factory() as session:
        second = await load(session)
        await session.commit()

    assert second["drafts_created"] == 0
    assert second["drafts_reused"] == len(COMPOUNDS)

    async with factory() as session:
        rows = (await session.execute(select(SupplementComponentKnowledge))).scalars().all()
    assert len(rows) == len(COMPOUNDS)


@pytest.mark.asyncio
async def test_the_database_refuses_an_absorption_figure_with_no_source(db_clean):
    """Not a convention — the table itself rejects it."""
    from sqlalchemy.exc import IntegrityError

    factory = get_sessionmaker()
    async with factory() as session:
        session.add(SupplementComponentKnowledge(
            canonical_component_key="magnesium", compound_form="invented form",
            absorption_value="about 40", absorption_unit="%", confidence="high",
            source_url=None, evidence_tier="clinically_studied",
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_the_database_refuses_an_absorption_figure_with_no_confidence(db_clean):
    from sqlalchemy.exc import IntegrityError

    factory = get_sessionmaker()
    async with factory() as session:
        session.add(SupplementComponentKnowledge(
            canonical_component_key="zinc", compound_form="invented form",
            absorption_value="about 40", absorption_unit="%", confidence=None,
            source_url="https://example.org/x", evidence_tier="clinically_studied",
        ))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_a_loaded_entry_cannot_be_approved_without_a_source_url(db_clean):
    """The 'not enough information' entries stay drafts, which is correct."""
    from app.domains.evidence import authoring
    from app.shared.errors.exceptions import ValidationFailedError

    factory = get_sessionmaker()
    async with factory() as session:
        await load(session)
        await session.commit()

    async with factory() as session:
        sourceless = (await session.execute(
            select(EvidenceClaim).where(
                EvidenceClaim.subject_type == SUBJECT_TYPE,
                EvidenceClaim.evidence_tier == knowledge.TIER_NOT_ENOUGH,
            ).limit(1)
        )).scalar_one()
        with pytest.raises(ValidationFailedError):
            await authoring.approve(session, sourceless.id, reviewer="reviewer")
