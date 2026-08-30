"""Turning a graded product into what one screen needs.

The engine answers "what grade and why". This answers "what does the screen
put where". Keeping them apart matters: the engine's trace is a complete audit
record and would overwhelm a phone, while the screen needs exactly four
components, each with one colour and one plain sentence.

No user-facing English lives here. The app owns its own words — this returns
keys, bands, numbers and sources, and the string file in the app decides how
they read.
"""
from __future__ import annotations

from typing import Any

from app.domains.nutrition.food_reference import ADDITIVES
from app.domains.nutrition.grading.engine import GradeResult, ProductInput
from app.domains.nutrition.grading.nova import normalise
from app.domains.nutrition.grading.rules import BAND_HIGH, Grade

#: Which colour each grade shows. Green take it, yellow think, red leave it.
BAND_FOR_GRADE: dict[str, str] = {
    Grade.A.value: "green", Grade.B.value: "green", Grade.C.value: "yellow",
    Grade.D.value: "red", Grade.E.value: "red",
}

#: The four components, in the order the Why screen lists them.
COMPONENT_KEYS = ("processing", "nutrients", "additives", "naming")

def _processing_component(result: GradeResult) -> dict[str, Any]:
    group = result.nova_group or 1
    band = {1: "green", 2: "green", 3: "yellow", 4: "red"}[group]
    entry = next(
        (row for row in result.trace if row.rule_id.startswith("grade.step1.nova_")), None
    )
    return {
        "key": "processing",
        "band": band,
        "state": f"nova{group}",
        "rule": entry.effect if entry and entry.effect else None,
        "finding": entry.finding if entry else None,
        "source": entry.source if entry else None,
    }


def _nutrient_component(result: GradeResult) -> dict[str, Any]:
    highs = [band for band in result.bands if band.band == BAND_HIGH and band.penalised]
    exempt = [band for band in result.bands if band.band == BAND_HIGH and not band.penalised]
    entries = [row for row in result.trace if row.rule_id.startswith("grade.step2.")]
    return {
        "key": "nutrients",
        "band": "red" if len(highs) > 1 else ("yellow" if highs else "green"),
        "state": "high" if highs else ("exempt" if exempt else "clear"),
        "high": [
            {"nutrient": band.nutrient, "attribution": band.attribution} for band in highs
        ],
        "exempt": [band.nutrient for band in exempt],
        "rule": entries[0].effect if entries else None,
        "finding": entries[0].finding if entries else None,
        "source": entries[0].source if entries else None,
    }


def _additive_component(product: ProductInput, result: GradeResult) -> dict[str, Any]:
    entries = [row for row in result.trace if row.rule_id.startswith("grade.step3.")]
    flagged = [row for row in entries if row.rule_id != "grade.step3.no_capping_additive"]
    return {
        "key": "additives",
        "band": "red" if any("black" in row.rule_id for row in flagged) else (
            "yellow" if flagged else "green"
        ),
        "state": "black" if any("black" in row.rule_id for row in flagged) else (
            "red" if any("red_tier" in row.rule_id for row in flagged) else (
                "child_colour" if any("child" in row.rule_id for row in flagged) else "none"
            )
        ),
        "rule": flagged[0].effect if flagged else (entries[0].effect if entries else None),
        "finding": flagged[0].finding if flagged else (entries[0].finding if entries else None),
        "source": entries[0].source if entries else None,
    }


def _naming_component(product: ProductInput, result: GradeResult) -> dict[str, Any]:
    entry = next((row for row in result.trace if row.rule_id.startswith("grade.step4.")), None)
    promised = product.name_promises
    declared = product.declared_percentages.get(promised) if promised else None
    if promised is None:
        state, band = "not_promised", "green"
    elif declared is None:
        state, band = "not_declared", "yellow"
    elif declared >= 50:
        state, band = "good", "green"
    elif declared >= 25:
        state, band = "note", "yellow"
    else:
        state, band = "low", "red"
    return {
        "key": "naming",
        "band": band,
        "state": state,
        "ingredient": promised,
        "declared_percent": float(declared) if declared is not None else None,
        "rule": entry.effect if entry else None,
        "finding": entry.finding if entry else None,
        "source": entry.source if entry else None,
    }


def _ingredient_rows(product: ProductInput) -> list[dict[str, Any]]:
    """Every ingredient on the pack, with its tier and what it does.

    Free, always, and in the order the pack prints them — the first is the most
    of it, which is the single most useful thing about an ingredient list and
    the thing almost nobody is told.
    """
    rows: list[dict[str, Any]] = []
    for raw in product.ingredients:
        text = normalise(raw)
        matched = None
        for additive in ADDITIVES:
            needles = [normalise(additive.name)]
            if additive.ins:
                needles.append(f"ins {additive.ins}")
            if any(needle and needle in text for needle in needles):
                matched = additive
                break
        rows.append({
            "name": raw,
            "tier": matched.tier if matched else "plain",
            "description": matched.function if matched else None,
            "source": matched.source.name if matched else None,
        })
    return rows


def present(product: ProductInput, result: GradeResult) -> dict[str, Any]:
    """Everything one verdict screen needs, and nothing it does not."""
    return {
        "engine_version": result.engine_version,
        "outcome": result.outcome.value,
        "grade": result.grade.value if result.grade else None,
        "band": BAND_FOR_GRADE.get(result.grade.value) if result.grade else "yellow",
        "product_name": product.name,
        "nutrition": {
            "total_sugar_g": float(product.total_sugar_g) if product.total_sugar_g is not None else None,
            "salt_g": float(product.salt_equivalent_g) if product.salt_equivalent_g is not None else None,
            "total_fat_g": float(product.total_fat_g) if product.total_fat_g is not None else None,
            "protein_g": float(product.protein_g) if product.protein_g is not None else None,
        },
        "components": [
            _processing_component(result),
            _nutrient_component(result),
            _additive_component(product, result),
            _naming_component(product, result),
        ],
        "ingredients": _ingredient_rows(product),
        "quantity_guidance": result.quantity_guidance,
        "purity_note": result.purity_note,
        "missing": list(result.missing),
    }


__all__ = ["BAND_FOR_GRADE", "COMPONENT_KEYS", "present"]
