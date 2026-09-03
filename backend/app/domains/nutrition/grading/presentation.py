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

from app.domains.nutrition.food_reference import ADDITIVES, Additive, Source
from app.domains.nutrition.grading.engine import GradeResult, ProductInput, TraceEntry
from app.domains.nutrition.grading.nova import normalise
from app.domains.nutrition.grading.production_rules import (
    FOOD_RULE_VERSION,
    STATUS_CANDIDATE,
    ProductionRuleset,
    candidate_ruleset,
)
from app.domains.nutrition.grading.rules import BAND_HIGH, Grade, GradeOutcome

#: Which colour each grade shows. Green is BUY, yellow is WAIT, red is SKIP.
BAND_FOR_GRADE: dict[str, str] = {
    Grade.A.value: "green", Grade.B.value: "green", Grade.C.value: "yellow",
    Grade.D.value: "red", Grade.E.value: "red",
}

#: The canonical purchase action for each letter. Named here, once, because it
#: is the customer-facing decision and more than one surface now needs it — a
#: second copy of this ladder somewhere else is how a card ends up disagreeing
#: with the verdict it sits under.
ACTION_FOR_GRADE: dict[Grade, str] = {
    Grade.A: "buy", Grade.B: "buy", Grade.C: "wait", Grade.D: "skip", Grade.E: "skip",
}


def action_for(result: GradeResult) -> str | None:
    """The canonical buy/wait/skip for a graded result, or ``None``.

    ``None`` is the honest answer for NOT_GRADED and NOT_ENOUGH_INFORMATION:
    neither has a letter, and neither has an action to take from one.
    """
    return ACTION_FOR_GRADE.get(result.grade) if result.grade else None


#: The four components, in the order the Why screen lists them.
COMPONENT_KEYS = ("processing", "nutrients", "additives", "naming")

# ---------------------------------------------------------------------------
# The status vocabulary
# ---------------------------------------------------------------------------
#: What a factor is, in the customer's terms. Deliberately a closed set: a
#: screen that renders an unrecognised status has no colour and no sentence for
#: it, and the fallback is always the most alarming reading.
#:
#: The distinctions carry weight. A nutrient that is high is a measured fact
#: about a legal product; an additive that is not permitted is a regulatory
#: breach. Rendering both as "flagged" tells somebody the same thing about a
#: sweet biscuit and about a pack that should not be on the shelf.
STATUS_HIGH = "high"
STATUS_MODERATE = "moderate"
STATUS_WORTH_KNOWING = "worth_knowing"
STATUS_WORTH_CAUTION = "worth_caution"
STATUS_FLAGGED = "flagged"
STATUS_NOT_PERMITTED = "not_permitted"
STATUS_NOT_ENOUGH_INFORMATION = "not_enough_information"
STATUS_DECLARED = "declared"
STATUS_NO_CONCERN_FOUND = "no_concern_found"

STATUSES: tuple[str, ...] = (
    STATUS_HIGH, STATUS_MODERATE, STATUS_WORTH_KNOWING, STATUS_WORTH_CAUTION,
    STATUS_FLAGGED, STATUS_NOT_PERMITTED, STATUS_NOT_ENOUGH_INFORMATION,
    STATUS_DECLARED, STATUS_NO_CONCERN_FOUND,
)

#: How loud each status is allowed to be. Not a severity ranking of harm — a
#: ranking of how much of the screen the row has earned.
_BAND_FOR_STATUS: dict[str, str] = {
    STATUS_NOT_PERMITTED: "red",
    STATUS_FLAGGED: "red",
    STATUS_HIGH: "red",
    STATUS_WORTH_CAUTION: "yellow",
    STATUS_MODERATE: "yellow",
    STATUS_NOT_ENOUGH_INFORMATION: "yellow",
    STATUS_WORTH_KNOWING: "green",
    STATUS_DECLARED: "green",
    STATUS_NO_CONCERN_FOUND: "green",
}

#: Order the lowering factors are listed in: what the food *is*, then what is
#: measured in it, then what was added, then what its name promised.
_FACTOR_ORDER: dict[str, int] = {
    "processing": 10, "refined_grain": 20,
    "sugar": 30, "salt": 31, "sodium": 32, "saturated_fat": 33, "total_fat": 34,
    "added_sugar_share": 35, "trans_fat": 36,
    "additive": 50, "naming": 70,
}

#: Band nutrient name -> (stable key, label key, explanation key, rule id).
#:
#: The rule id is named rather than derived from the nutrient, because sugar is
#: charged once across two readings — grams per 100 g and share of energy — and
#: the step that charges it is ``grade.step2.sugar`` whichever reading won.
#: Deriving the id would leave the sugar row pointing at a rule that never fired.
_NUTRIENT_FACTS: dict[str, tuple[str, str, str, str]] = {
    "total sugars": ("sugar", "sugar", "high_sugar", "grade.step2.sugar"),
    "salt": ("salt", "salt", "high_salt", "grade.step2.high_salt"),
    "sodium": ("sodium", "sodium", "high_sodium", "grade.step2.high_sodium"),
    "saturated fat": (
        "saturated_fat", "saturated_fat", "high_saturated_fat",
        "grade.step2.high_saturated_fat",
    ),
    "total fat": ("total_fat", "total_fat", "high_total_fat", "grade.step2.high_total_fat"),
}

#: An additive's tier, in the customer's terms.
_STATUS_FOR_TIER: dict[str, str] = {
    "black": STATUS_NOT_PERMITTED,
    "red": STATUS_FLAGGED,
    "amber": STATUS_WORTH_CAUTION,
    "green": STATUS_WORTH_KNOWING,
    "plain": STATUS_NO_CONCERN_FOUND,
}


def band_for_status(status: str) -> str:
    """The colour a status is allowed to paint. Unknown never reads as safe."""
    return _BAND_FOR_STATUS.get(status, "yellow")


def _source(source: Source | None) -> dict[str, Any] | None:
    """One source, as a screen and an error report need it.

    Built from the ``Source`` the rule actually used rather than looked up by
    name against a list kept by hand: a list like that silently loses whichever
    source nobody remembered to add, and the row it was meant to support then
    renders with nothing to open.
    """
    if source is None:
        return None
    return {
        "name": source.name,
        "url": source.url,
        "publisher": source.publisher or None,
        "identifier": source.identifier,
        "version": source.identifier,
    }


def _sources_for(entry: TraceEntry | None) -> list[dict[str, Any]]:
    payload = _source(entry.reference) if entry else None
    return [payload] if payload else []


def _basis_for_unit(unit: str) -> str:
    """"g per 100 ml" -> per_100_ml. The pack is never assumed."""
    return "per_100_ml" if "100 ml" in unit else "per_100_g"


def _unit_symbol(unit: str) -> str:
    """"g per 100 g" -> "g"."""
    return unit.split(" ", 1)[0] if unit else "g"


def _quantity(value: Any, unit: str) -> dict[str, Any] | None:
    """A measured amount, with the basis it was measured on stated.

    There is no pack-size path here on purpose. The panel states per 100 g or
    per 100 ml, and describing that as a packet would be inventing a number
    nobody printed.
    """
    if value is None:
        return None
    return {
        "value": float(value),
        "unit": _unit_symbol(unit),
        "basis": _basis_for_unit(unit),
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
    status = _STATUS_FOR_TIER.get(tier, STATUS_NOT_ENOUGH_INFORMATION)
    return status, band_for_status(status)


def _trace_by_rule(result: GradeResult) -> dict[str, TraceEntry]:
    return {entry.rule_id: entry for entry in result.trace}


def _matched_additive(text: str) -> Additive | None:
    normalised = normalise(text)
    for additive in ADDITIVES:
        needles = [normalise(additive.name)]
        if additive.ins:
            needles.append(f"ins {additive.ins}")
        if any(needle and needle in normalised for needle in needles):
            return additive
    return None


def _evidence(rule_id: str | None, ruleset: ProductionRuleset) -> dict[str, Any]:
    """Which lifecycle stage the rule behind this row actually reached.

    Always present, never implied. A row whose rule is still a candidate says
    so in the payload rather than looking identical to a published one.
    """
    found = ruleset.for_rule(rule_id) if rule_id else None
    if found is None:
        return {
            "status": STATUS_CANDIDATE, "rule_version": None,
            "evidence_claim_ids": [], "evidence_claim_version": None,
        }
    payload = found.as_payload()
    return {
        "status": payload["status"],
        "rule_version": payload["rule_version"],
        "evidence_claim_ids": payload["evidence_claim_ids"],
        "evidence_claim_version": payload["evidence_claim_version"],
    }


def _trace_payload(result: GradeResult, ruleset: ProductionRuleset) -> list[dict[str, Any]]:
    """Preserve exact lifecycle provenance beside every engine trace entry.

    The engine deliberately stays database-free.  The presentation boundary is
    where its stable rule identifier is joined to the resolved production
    ruleset, so an audit export can name both the rule version and the exact
    evidence claim(s) rather than only the candidate source citation.
    """
    rows: list[dict[str, Any]] = []
    for entry in result.trace:
        row = entry.as_payload()
        row["evidence"] = _evidence(entry.rule_id, ruleset)
        rows.append(row)
    return rows


def _factor(
    *,
    key: str,
    label: str,
    status: str,
    explanation: str,
    rule: str | None,
    ruleset: ProductionRuleset,
    quantity: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One row of "what lowers it" or "what helps", in full.

    Every field is filled from what actually fired. ``label`` is a string key
    the app resolves to words — never the internal rule id, which means nothing
    to anybody holding a biscuit.
    """
    row = {
        "key": key,
        "label": label,
        "quantity": quantity,
        "status": status,
        "band": band_for_status(status),
        "explanation": explanation,
        "rule": rule,
        "sources": sources or [],
        "evidence": _evidence(rule, ruleset),
        "order": _FACTOR_ORDER.get(key.split(":", 1)[0], 60),
    }
    if detail is not None:
        row["detail"] = detail
    return row


def _nutrient_factors(
    result: GradeResult, ruleset: ProductionRuleset, traced: dict[str, TraceEntry],
) -> list[dict[str, Any]]:
    """A row per nutrient that actually counted, carrying its measured value.

    The value is the one the band was decided on, not a re-read of the product:
    the row and the decision cannot drift apart because they are the same
    number.
    """
    rows: list[dict[str, Any]] = []
    for band in result.bands:
        # Only a nutrient that actually cost the grade something belongs under
        # "what lowers it". A middle reading lowered nothing, and listing it
        # here would put a row on the screen that the grade cannot account for.
        if band.band != BAND_HIGH or not band.penalised:
            continue
        known = _NUTRIENT_FACTS.get(band.nutrient)
        if known is None:
            continue
        key, label, explanation, rule_id = known
        entry = traced.get(rule_id)
        if entry is None:
            continue
        rows.append(_factor(
            key=key, label=label, status=STATUS_HIGH, explanation=explanation,
            rule=rule_id, ruleset=ruleset,
            quantity=_quantity(band.value, band.unit),
            sources=[_source(band.source)] if _source(band.source) else [],
            detail={"attribution": band.attribution} if band.attribution else None,
        ))
    return rows


def _processing_factor(
    result: GradeResult, ruleset: ProductionRuleset, traced: dict[str, TraceEntry],
) -> list[dict[str, Any]]:
    group = result.nova_group
    if group not in (3, 4):
        return []
    entry = traced.get(f"grade.step1.nova_{group}")
    status = STATUS_FLAGGED if group == 4 else STATUS_WORTH_KNOWING
    explanation = "highly_processed" if group == 4 else "processed"
    return [_factor(
        key="processing", label="processing", status=status, explanation=explanation,
        rule=entry.rule_id if entry else None, ruleset=ruleset,
        sources=_sources_for(entry),
        detail={"nova_group": group, "finding": entry.finding if entry else None},
    )]


def _additive_factors(
    product: ProductInput, ruleset: ProductionRuleset, traced: dict[str, TraceEntry],
) -> list[dict[str, Any]]:
    """A row per additive that earned one, named the way the pack names it.

    Built from the ingredient list rather than from the trace text, so the row
    carries the additive's own identity — name, INS number, what it does — and
    not a sentence about it.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in product.ingredients:
        additive = _matched_additive(raw)
        if additive is None or additive.tier not in ("amber", "red", "black"):
            continue
        if additive.name in seen:
            continue
        seen.add(additive.name)
        rule_id = {
            "black": "grade.step3.black_tier",
            "red": "grade.step3.red_tier",
            "amber": None,
        }[additive.tier]
        entry = traced.get(rule_id) if rule_id else None
        status = _STATUS_FOR_TIER[additive.tier]
        label = f"{additive.name} · INS {additive.ins}" if additive.ins else additive.name
        rows.append(_factor(
            key=f"additive:{additive.ins or normalise(additive.name)}",
            label=label, status=status, explanation=f"additive_{additive.tier}",
            rule=rule_id, ruleset=ruleset,
            sources=[_source(additive.source)] if _source(additive.source) else [],
            detail={
                "ins": additive.ins,
                "additive_name": additive.name,
                "function": additive.function,
                "why_flagged": additive.note,
                "authority_position": additive.source.name,
                "interpretation": entry.effect if entry else None,
                "disagreement": additive.disagreement,
                "confidence": additive.confidence.value
                if hasattr(additive.confidence, "value") else str(additive.confidence),
            },
        ))
    return rows


def _other_lowering_factors(
    product: ProductInput, result: GradeResult, ruleset: ProductionRuleset,
    traced: dict[str, TraceEntry],
) -> list[dict[str, Any]]:
    """The remaining rules that lower a grade, each with what it measured."""
    rows: list[dict[str, Any]] = []

    entry = traced.get("grade.step1.refined_grain")
    if entry is not None:
        rows.append(_factor(
            key="refined_grain", label="refined_grain", status=STATUS_WORTH_CAUTION,
            explanation="refined_grain_main_ingredient", rule=entry.rule_id,
            ruleset=ruleset, sources=_sources_for(entry),
            detail={"finding": entry.finding},
        ))

    entry = traced.get("grade.step2.partially_hydrogenated_oil")
    if entry is not None:
        rows.append(_factor(
            key="trans_fat", label="trans_fat", status=STATUS_NOT_PERMITTED,
            explanation="partially_hydrogenated_oil", rule=entry.rule_id,
            ruleset=ruleset,
            quantity=_quantity(product.trans_fat_g, "g per 100 g")
            if product.trans_fat_g is not None else None,
            sources=_sources_for(entry), detail={"finding": entry.finding},
        ))

    entry = traced.get("grade.step2.added_sugar_dominates")
    if entry is not None:
        rows.append(_factor(
            key="added_sugar_share", label="added_sugar_share", status=STATUS_HIGH,
            explanation="added_sugar_dominates_energy", rule=entry.rule_id,
            ruleset=ruleset, sources=_sources_for(entry),
            detail={"finding": entry.finding},
        ))

    for rule_id, status, explanation in (
        ("grade.step4.declared_percentage", STATUS_MODERATE, "named_ingredient_share"),
        ("grade.step4.percentage_not_declared", STATUS_NOT_ENOUGH_INFORMATION,
         "named_ingredient_not_declared"),
    ):
        entry = traced.get(rule_id)
        if entry is None or not entry.effect:
            continue
        promised = product.name_promises
        declared = product.declared_percentages.get(promised) if promised else None
        if rule_id.endswith("declared_percentage") and (declared is None or declared >= 50):
            continue
        rows.append(_factor(
            key="naming", label="named_ingredient", status=status,
            explanation=explanation, rule=entry.rule_id, ruleset=ruleset,
            quantity={"value": float(declared), "unit": "%", "basis": "of_product"}
            if declared is not None else None,
            sources=_sources_for(entry),
            detail={"ingredient": promised, "finding": entry.finding},
        ))
    return rows


def _factor_rows(
    product: ProductInput, result: GradeResult, ruleset: ProductionRuleset,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate drawbacks from useful label facts without creating health claims."""
    traced = _trace_by_rule(result)
    lowers: list[dict[str, Any]] = [
        *_nutrient_factors(result, ruleset, traced),
        *_processing_factor(result, ruleset, traced),
        *_additive_factors(product, ruleset, traced),
        *_other_lowering_factors(product, result, ruleset, traced),
    ]
    lowers.sort(key=lambda row: (row["order"], row["key"]))

    # Preserve label facts as facts; none is turned into a dietary recommendation.
    helps: list[dict[str, Any]] = []
    for key, label, value in (
        ("protein", "protein", product.protein_g),
        ("fibre", "fibre", product.fibre_g),
    ):
        if value is not None:
            helps.append(_factor(
                key=key, label=label, status=STATUS_DECLARED,
                explanation="declared_on_label", rule=None, ruleset=ruleset,
                quantity=_quantity(
                    value, "g per 100 ml" if product.basis == "drink" else "g per 100 g",
                ),
            ))
    if result.nova_group in {1, 2}:
        helps.append(_factor(
            key="processing", label="processing", status=STATUS_NO_CONCERN_FOUND,
            explanation="lower_processing_group", rule=None, ruleset=ruleset,
        ))
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
        "source": entry.source_name if entry else None,
        "source_url": (entry.reference.url if entry and entry.reference else None),
        "sources": _sources_for(entry),
    }


def _nutrient_component(result: GradeResult) -> dict[str, Any]:
    highs = [band for band in result.bands if band.band == BAND_HIGH and band.penalised]
    exempt = [band for band in result.bands if band.band == BAND_HIGH and not band.penalised]
    entries = [row for row in result.trace if row.rule_id.startswith("grade.step2.")]
    lead = entries[0] if entries else None
    return {
        "key": "nutrients",
        "band": "red" if len(highs) > 1 else ("yellow" if highs else "green"),
        "state": "high" if highs else ("exempt" if exempt else "clear"),
        "high": [
            {"nutrient": band.nutrient, "attribution": band.attribution} for band in highs
        ],
        "exempt": [band.nutrient for band in exempt],
        "rule": lead.effect if lead else None,
        "finding": lead.finding if lead else None,
        "source": lead.source_name if lead else None,
        "source_url": (lead.reference.url if lead and lead.reference else None),
        "sources": _sources_for(lead),
    }


def _additive_component(product: ProductInput, result: GradeResult) -> dict[str, Any]:
    entries = [row for row in result.trace if row.rule_id.startswith("grade.step3.")]
    flagged = [row for row in entries if row.rule_id != "grade.step3.no_capping_additive"]
    lead = flagged[0] if flagged else (entries[0] if entries else None)
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
        "rule": lead.effect if lead else None,
        "finding": lead.finding if lead else None,
        "source": lead.source_name if lead else None,
        "source_url": (lead.reference.url if lead and lead.reference else None),
        "sources": _sources_for(lead),
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
        "source": entry.source_name if entry else None,
        "source_url": (entry.reference.url if entry and entry.reference else None),
        "sources": _sources_for(entry),
    }


def _ingredient_rows(product: ProductInput) -> list[dict[str, Any]]:
    """Every ingredient on the pack, with its tier and what it does.

    Free, always, and in the order the pack prints them — the first is the most
    of it, which is the single most useful thing about an ingredient list and
    the thing almost nobody is told.

    Each row carries three independent things a person might want: the source
    to open, a fuller explanation, and a way to tell us the row is wrong. The
    row is written to be understood without reaching for any of them; ``detail``
    is what the deeper explanation shows, not where the basic one lives.
    """
    rows: list[dict[str, Any]] = []
    for raw in product.ingredients:
        matched = _matched_additive(raw)
        status, band = _status_for_tier(matched.tier if matched else "plain")
        flagged = bool(matched and matched.tier in {"amber", "red", "black"})
        source = _source(matched.source) if matched else None
        rows.append({
            "name": raw,
            "label": (
                f"{matched.name} · INS {matched.ins}"
                if matched and matched.ins else (matched.name if matched else raw)
            ),
            "tier": matched.tier if matched else "plain",
            "status": status,
            "band": band,
            # The one-line description stays on the row. A person who never
            # taps anything still learns what the thing is for.
            "description": matched.function if matched else None,
            "why_flagged": matched.note if flagged else None,
            "source": matched.source.name if matched else None,
            "sources": [source] if source else [],
            "detail": {
                "what_it_does": matched.function if matched else None,
                "why_flagged": matched.note if flagged else None,
                "rule": (
                    "grade.step3.black_tier" if matched and matched.tier == "black"
                    else "grade.step3.red_tier" if matched and matched.tier == "red"
                    else None
                ),
                "authority_position": matched.source.name if matched else None,
                "interpretation": matched.disagreement if matched else None,
                "evidence_status": (
                    matched.confidence.value
                    if matched and hasattr(matched.confidence, "value")
                    else (str(matched.confidence) if matched else None)
                ),
                "source": source,
            } if matched else None,
            "actions": {
                # Three separate things, deliberately: opening the authority,
                # asking for more, and telling us we are wrong are not the
                # same request and must not share a control.
                "source": bool(source and source["url"]),
                "explain": bool(matched),
                "report": True,
            },
        })
    return rows


def present(
    product: ProductInput,
    result: GradeResult,
    ruleset: ProductionRuleset | None = None,
) -> dict[str, Any]:
    """Everything one verdict screen needs, and nothing it does not.

    ``ruleset`` says which rules have completed the evidence lifecycle. Omitting
    it does not mean "assume published" — it falls back to the candidate
    ruleset, which marks every row as resting on an unreviewed constant. The
    production path passes the resolved one.
    """
    ruleset = ruleset if ruleset is not None else candidate_ruleset()
    negatives, positives = _factor_rows(product, result, ruleset)
    action = action_for(result)
    if negatives:
        reason_key = negatives[0]["key"]
    elif result.outcome.value == "graded":
        reason_key = "label_facts"
    elif result.outcome.value == "not_graded":
        reason_key = "not_graded"
    else:
        reason_key = "not_enough_information"
    return {
        "engine_version": result.engine_version,
        "outcome": result.outcome.value,
        "grade": result.grade.value if result.grade else None,
        "band": BAND_FOR_GRADE.get(result.grade.value) if result.grade else "yellow",
        "product_name": product.name,
        "taxonomy": _taxonomy(product, result),
        "decision": {
            "action": action,
            "reason_key": reason_key,
        },
        "evidence": {
            "ruleset_version": FOOD_RULE_VERSION,
            # Named, so nobody has to infer it from the rows: which of the
            # rules that actually fired are still resting on a candidate.
            "unpublished_rules": [
                row["rule"] for row in negatives
                if row["rule"] and row["evidence"]["status"] != "published"
            ],
        },
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
        # Product Result Contract V1. These canonical arrays are the one
        # presentation calculation path; legacy names below are aliases only.
        "result_contract_version": "v1",
        "negatives": negatives,
        "positives": positives,
        "lowers": negatives,
        "helps": positives,
        "ingredients": _ingredient_rows(product),
        # The place for a future deterministic alternative is explicit. This
        # milestone deliberately does not manufacture one from grading facts.
        "better_next_action": None,
        "trace": _trace_payload(result, ruleset),
        "quantity_guidance": result.quantity_guidance,
        "purity_note": result.purity_note,
        "missing": list(result.missing),
    }


__all__ = [
    "ACTION_FOR_GRADE", "BAND_FOR_GRADE", "COMPONENT_KEYS", "STATUSES", "action_for",
    "band_for_status", "present",
]
