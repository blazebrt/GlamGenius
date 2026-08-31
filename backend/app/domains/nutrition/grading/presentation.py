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

from app.domains.nutrition.food_reference import ADDITIVES, FSA_FOP, FSSAI_ADDITIVES, FSSAI_LABELLING, FSSAI_TRANSFAT
from app.domains.nutrition.grading.engine import GradeResult, ProductInput
from app.domains.nutrition.grading.nova import normalise
from app.domains.nutrition.grading.rules import BAND_HIGH, Grade, GradeOutcome

#: Which colour each grade shows. Green take it, yellow think, red leave it.
BAND_FOR_GRADE: dict[str, str] = {
    Grade.A.value: "green", Grade.B.value: "green", Grade.C.value: "yellow",
    Grade.D.value: "red", Grade.E.value: "red",
}

#: The four components, in the order the Why screen lists them.
COMPONENT_KEYS = ("processing", "nutrients", "additives", "naming")

_SOURCES = {source.name: source for source in (FSA_FOP, FSSAI_ADDITIVES, FSSAI_LABELLING, FSSAI_TRANSFAT)}


def _source_url(source: str | None) -> str | None:
    known = _SOURCES.get(source or "")
    return known.url if known else None


def _source(source: str | None) -> dict[str, Any] | None:
    """A stable, display-safe source object; an unknown source is not invented."""
    if not source:
        return None
    known = _SOURCES.get(source)
    return {
        "name": source,
        "url": known.url if known else None,
        "publisher": known.name.split(".", 1)[0] if known else None,
        "version": known.identifier if known else None,
    }


def _taxonomy(product: ProductInput, result: GradeResult) -> dict[str, str]:
    """Return a customer taxonomy path, independent of the grading verdict."""
    text = f"{product.categories or ''} {product.name}".lower()
    if result.outcome == GradeOutcome.NOT_GRADED:
        if "ghee" in text:
            subcategory = "ghee"
        elif "salt" in text:
            subcategory = "salt"
        elif "oil" in text:
            subcategory = "cooking_oil"
        else:
            subcategory = "culinary_ingredient"
        return {"domain": "consumed", "category": "culinary_ingredient", "subcategory": subcategory}
    if "biscuit" in text or "cookie" in text:
        subcategory = "biscuit"
    elif "cereal" in text:
        subcategory = "cereal"
    elif product.basis == "drink":
        subcategory = "beverage"
    elif any(word in text for word in ("dal", "lentil", "pulse")):
        return {"domain": "consumed", "category": "whole_minimally_processed", "subcategory": "dal"}
    else:
        subcategory = "other_packaged_food"
    return {"domain": "consumed", "category": "packaged_food", "subcategory": subcategory}


def _status_for_tier(tier: str) -> tuple[str, str]:
    return {
        "black": ("not_permitted", "red"),
        "red": ("flagged", "red"),
        "amber": ("worth_caution", "yellow"),
        "green": ("worth_knowing", "green"),
        "plain": ("no_concern_found", "green"),
    }.get(tier, ("not_enough_information", "yellow"))


def _quantity(label: str, value: Any, unit: str = "g per 100") -> dict[str, Any] | None:
    if value is None:
        return None
    return {"label": label, "value": float(value), "unit": unit}


def _factor_rows(product: ProductInput, result: GradeResult) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate drawbacks from useful label facts without creating health claims."""
    lowers: list[dict[str, Any]] = []
    helps: list[dict[str, Any]] = []
    for entry in result.trace:
        effect = (entry.effect or "").lower()
        if effect and any(marker in effect for marker in ("down", "ceiling", "automatic e", "flagged")):
            explanation_key = (
                "lower_processing" if ".nova" in entry.rule_id else
                "lower_sugar" if "sugar" in entry.rule_id else
                "lower_salt" if "salt" in entry.rule_id else
                "lower_fat" if "fat" in entry.rule_id else
                "lower_additive" if "step3" in entry.rule_id else
                "lower_naming" if "step4" in entry.rule_id else "lower_label_fact"
            )
            lowers.append({
                "key": entry.rule_id, "status": "flagged", "band": "red",
                "quantity": None, "explanation": explanation_key,
                "rule": entry.rule_id, "sources": [_source(entry.source)] if _source(entry.source) else [],
            })
    # Preserve label facts as facts; none is turned into a dietary recommendation.
    for key, label, value in (
        ("protein", "protein", product.protein_g),
        ("fibre", "fibre", product.fibre_g),
    ):
        if value is not None:
            helps.append({
                "key": key, "status": "declared", "band": "green",
                "quantity": _quantity(label, value),
                "explanation": "declared_on_label", "rule": None, "sources": [],
            })
    if result.nova_group in {1, 2}:
        helps.append({
            "key": "processing", "status": "no_concern_found", "band": "green",
            "quantity": None, "explanation": "lower_processing_group", "rule": None, "sources": [],
        })
    return lowers, helps

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
        "source_url": _source_url(entry.source if entry else None),
        "sources": [_source(entry.source)] if entry and _source(entry.source) else [],
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
        "source_url": _source_url(entries[0].source if entries else None),
        "sources": [_source(entries[0].source)] if entries and _source(entries[0].source) else [],
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
        "source_url": _source_url(entries[0].source if entries else None),
        "sources": [_source(entries[0].source)] if entries and _source(entries[0].source) else [],
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
        "source_url": _source_url(entry.source if entry else None),
        "sources": [_source(entry.source)] if entry and _source(entry.source) else [],
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
        status, band = _status_for_tier(matched.tier if matched else "plain")
        rows.append({
            "name": raw,
            "tier": matched.tier if matched else "plain",
            "status": status,
            "band": band,
            "description": matched.function if matched else None,
            "why_flagged": matched.note if matched and matched.tier in {"amber", "red", "black"} else None,
            "source": matched.source.name if matched else None,
            "sources": [_source(matched.source.name)] if matched else [],
        })
    return rows


def present(product: ProductInput, result: GradeResult) -> dict[str, Any]:
    """Everything one verdict screen needs, and nothing it does not."""
    lowers, helps = _factor_rows(product, result)
    action = {
        Grade.A: "buy", Grade.B: "buy", Grade.C: "wait", Grade.D: "skip", Grade.E: "skip",
    }.get(result.grade, "wait")
    return {
        "engine_version": result.engine_version,
        "outcome": result.outcome.value,
        "grade": result.grade.value if result.grade else None,
        "band": BAND_FOR_GRADE.get(result.grade.value) if result.grade else "yellow",
        "product_name": product.name,
        "taxonomy": _taxonomy(product, result),
        "decision": {"action": action, "reason_key": (lowers[0]["key"] if lowers else "label_facts")},
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
        "lowers": lowers,
        "helps": helps,
        "ingredients": _ingredient_rows(product),
        "quantity_guidance": result.quantity_guidance,
        "purity_note": result.purity_note,
        "missing": list(result.missing),
    }


__all__ = ["BAND_FOR_GRADE", "COMPONENT_KEYS", "present"]
