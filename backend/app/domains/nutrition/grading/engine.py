"""The grading engine: six gates, in order, none of them averaging.

    Step 0  Culinary ingredient      -> NOT_GRADED, always first
    Step 1  Processing (NOVA)        -> the ceiling
    Step 2  Nutrient bands           -> penalties and lifts, per 100 g
    Step 3  Additives                -> further ceilings
    Step 4  Named-ingredient integrity -> further ceilings
    Step 5  Confidence               -> NOT_ENOUGH_INFORMATION overrides all

Every step appends to a trace. Every entry names the rule that fired and the
source behind it, so a grade can be read backwards from the letter to the line
on the pack that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domains.nutrition.food_reference import (
    ADDITIVES,
    CULINARY_INGREDIENTS,
    FSSAI_ADDITIVES,
    FSSAI_TRANSFAT,
    THRESHOLDS,
    TIER_BLACK,
    TIER_RED,
    CulinaryIngredient,
)
from app.domains.nutrition.grading import nova
from app.domains.nutrition.grading.rules import (
    ADDED_SUGAR_ENERGY_DEMERIT_PCT,
    ADDED_SUGAR_ENERGY_SEVERE_PCT,
    BAND_HIGH,
    BAND_LOW,
    BAND_MEDIUM,
    BAND_UNKNOWN,
    CHILD_COLOUR_CEILING,
    FIBRE_POSITIVE_MIN,
    FSSAI_SUGAR_ENERGY_SOURCE,
    KCAL_PER_G_SUGAR,
    NAMED_INGREDIENT_SOURCE,
    NOVA_CEILINGS,
    POSITIVES_FOR_LIFT,
    PROTEIN_POSITIVE_MIN,
    RED_TIER_CEILING,
    REFINED_GRAIN_CEILING,
    REFINED_GRAIN_SOURCE,
    REFINED_GRAINS,
    SEVERE_MULTIPLE,
    SEVERE_REQUIRES_FOOD_BELOW_PCT,
    WHOLE_FOOD_POSITIVE_MIN_PCT,
    Band,
    Grade,
    GradeOutcome,
    drop,
    named_ingredient_rule,
    worse_of,
)

FOOD_GRADE_ENGINE_VERSION = "food-grade-v1"

#: FSSAI limit on industrial trans fat: not more than 2% by mass of total oils
#: and fats, in force from January 2022.
TRANS_FAT_LIMIT_PCT_OF_FAT = Decimal("2")

# Fully hydrogenated vegetable oil is not the same textual claim as partially
# hydrogenated industrial trans fat.  Only the label terms that establish the
# latter can trigger the automatic rule.
PHO_MARKERS = ("partially hydrogenated", "vanaspati")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductInput:
    """One packaged product, as its label reads.

    Nutrition is per 100 g for a solid and per 100 ml for a drink. There is no
    per-serving path: a serving size is chosen by the manufacturer and is the
    easiest number on a pack to make flattering.
    """

    name: str
    ingredients: tuple[str, ...] = ()
    #: Per 100 g / 100 ml. ``None`` means the panel did not declare it.
    energy_kcal: Decimal | None = None
    protein_g: Decimal | None = None
    total_fat_g: Decimal | None = None
    saturated_fat_g: Decimal | None = None
    trans_fat_g: Decimal | None = None
    total_sugar_g: Decimal | None = None
    added_sugar_g: Decimal | None = None
    fibre_g: Decimal | None = None
    sodium_g: Decimal | None = None
    salt_g: Decimal | None = None
    basis: str = "solid"                    # solid | drink
    #: Declared percentages, keyed by ingredient: {"atta": 30}
    declared_percentages: dict[str, Decimal] = field(default_factory=dict)
    #: The ingredient the product's own name promises, if any.
    name_promises: str | None = None
    #: Share of the product that is whole pulse, grain, nut or fruit.
    whole_food_pct: Decimal | None = None
    fermented: bool = False
    marketed_to_children: bool = False
    #: Set when the label carried no nutrition panel at all.
    has_nutrition_panel: bool = True
    has_ingredient_list: bool = True

    @property
    def salt_equivalent_g(self) -> Decimal | None:
        """Indian labels usually declare sodium. Salt is sodium x 2.5."""
        if self.salt_g is not None:
            return self.salt_g
        if self.sodium_g is not None:
            return self.sodium_g * Decimal("2.5")
        return None


@dataclass(frozen=True)
class TraceEntry:
    step: int
    step_name: str
    rule_id: str
    finding: str
    source: str
    #: What this step did to the grade, if anything.
    effect: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "step_name": self.step_name,
            "rule_id": self.rule_id,
            "finding": self.finding,
            "source": self.source,
            "effect": self.effect,
        }


@dataclass(frozen=True)
class GradeResult:
    engine_version: str
    outcome: GradeOutcome
    grade: Grade | None
    headline: str
    detail: str
    nova_group: int | None
    ceiling: Grade | None
    trace: tuple[TraceEntry, ...]
    bands: tuple[Band, ...] = ()
    quantity_guidance: str | None = None
    purity_note: str | None = None
    missing: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "outcome": self.outcome.value,
            "grade": self.grade.value if self.grade else None,
            "headline": self.headline,
            "detail": self.detail,
            "nova_group": self.nova_group,
            "ceiling": self.ceiling.value if self.ceiling else None,
            "quantity_guidance": self.quantity_guidance,
            "purity_note": self.purity_note,
            "missing": list(self.missing),
            "bands": [
                {
                    "nutrient": band.nutrient, "band": band.band,
                    "value": str(band.value) if band.value is not None else None,
                    "unit": band.unit, "attribution": band.attribution,
                    "penalised": band.penalised, "source": band.source.name,
                }
                for band in self.bands
            ],
            "trace": [entry.as_payload() for entry in self.trace],
        }


# ---------------------------------------------------------------------------
# Step 0 — culinary ingredients
# ---------------------------------------------------------------------------
#: What to check instead of a grade. A cooking fat's real question is whether
#: it is what it says it is, not what letter it earns.
PURITY_NOTES: dict[str, str] = {
    "ghee": "Check the pack states 100% milk fat and carries an FSSAI licence number. "
            "Ghee is adulterated more often than most cooking fats.",
    "butter": "Check the pack states milk fat and nothing else, and carries an FSSAI "
              "licence number.",
    "cooking oil": "Check the pack names one oil rather than a blend, states it is not "
                   "hydrogenated, and carries an FSSAI licence number and a packing date.",
    "vanaspati": "Check the declared trans fat. FSSAI limits industrial trans fat to 2% "
                 "of total oils and fats.",
    "salt": "Check the pack states iodised salt and carries an FSSAI licence number.",
    "sugar": "Nothing to check beyond the licence number; sugar is sugar.",
    "jaggery": "Check for a declared colour. Jaggery is sometimes brightened.",
    "honey": "Check the pack states 100% honey. Added syrup is the common adulteration.",
    "shakkar": "Check for a declared colour.",
    "misri": "Nothing to check beyond the licence number.",
    "vinegar": "Check whether the pack says synthetic or brewed; both are permitted and "
               "the label must say which.",
}


def _match_culinary(product: ProductInput) -> CulinaryIngredient | None:
    """Is this product a cooking ingredient rather than a food?"""
    name = nova.normalise(product.name)
    ingredients = [nova.normalise(row) for row in product.ingredients]
    single_ingredient = ingredients[0] if len(ingredients) == 1 else None
    best: tuple[int, CulinaryIngredient] | None = None
    for ingredient in CULINARY_INGREDIENTS:
        for alias in (*ingredient.aliases, ingredient.name):
            token = nova.normalise(alias)
            if not token:
                continue
            # A culinary ingredient has to be the product itself (or the sole
            # declared ingredient), not merely a word in a composite product
            # name such as "Honey Cookies".
            hit = name == token or (
                single_ingredient is not None
                and nova._contains(name, token)
                and nova._contains(single_ingredient, token)
            )
            if hit and (best is None or len(token) > best[0]):
                best = (len(token), ingredient)
    return best[1] if best else None


def _step0(product: ProductInput, trace: list[TraceEntry]) -> GradeResult | None:
    match = _match_culinary(product)
    if match is None:
        trace.append(TraceEntry(
            0, "Culinary ingredient check", "grade.step0.not_a_culinary_ingredient",
            "This is a food, not something used to cook with.",
            "GlamGenius culinary ingredient list",
        ))
        return None
    guidance = match.daily_guidance or "No published daily figure is carried here."
    return GradeResult(
        engine_version=FOOD_GRADE_ENGINE_VERSION,
        outcome=GradeOutcome.NOT_GRADED,
        grade=None,
        headline=f"{match.name} is not graded.",
        detail=match.why_never_graded,
        nova_group=nova.NOVA_CULINARY_INGREDIENT,
        ceiling=None,
        quantity_guidance=guidance,
        purity_note=PURITY_NOTES.get(match.key),
        trace=(*trace, TraceEntry(
            0, "Culinary ingredient check", "grade.step0.culinary_ingredient",
            f"{match.name} is a cooking ingredient.",
            match.guidance_source.name if match.guidance_source
            else "GlamGenius culinary ingredient list",
            effect="Returns NOT_GRADED. No letter is produced.",
        )),
    )


# ---------------------------------------------------------------------------
# Step 1 — processing
# ---------------------------------------------------------------------------
def _step1(product: ProductInput, trace: list[TraceEntry]) -> tuple[int, Grade]:
    result = nova.classify(list(product.ingredients))
    ceiling = NOVA_CEILINGS[result.group]
    effect = f"Ceiling {ceiling.value}."
    if result.group == nova.NOVA_ULTRA_PROCESSED:
        effect = "Ceiling C. Nothing in the nutrition panel can lift a group 4 product above C."
    trace.append(TraceEntry(
        1, "Processing gate (NOVA)", f"grade.step1.nova_{result.group}",
        f"NOVA group {result.group}. {result.reason}", result.source, effect=effect,
    ))
    return result.group, ceiling


def _refined_grain_cap(product: ProductInput, trace: list[TraceEntry]) -> Grade | None:
    """A refined flour carries no NOVA marker, so the gate has to see it here."""
    first = nova.normalise(product.ingredients[0]) if product.ingredients else ""
    name = nova.normalise(product.name)
    for grain in REFINED_GRAINS:
        token = nova.normalise(grain)
        if nova._contains(first, token) or nova._contains(name, token):
            trace.append(TraceEntry(
                1, "Processing gate (NOVA)", "grade.step1.refined_grain",
                f"The main ingredient is {grain}, a refined grain with the bran and germ removed.",
                REFINED_GRAIN_SOURCE,
                effect=f"Ceiling {REFINED_GRAIN_CEILING.value}.",
            ))
            return REFINED_GRAIN_CEILING
    return None


# ---------------------------------------------------------------------------
# Step 2 — nutrient bands
# ---------------------------------------------------------------------------
def _threshold(nutrient: str, basis: str):
    for row in THRESHOLDS:
        if row.nutrient == nutrient and row.basis == basis and row.high_min is not None:
            return row
    return None


def _band_for(value: Decimal | None, low_max: Decimal, high_min: Decimal) -> str:
    if value is None:
        return BAND_UNKNOWN
    if value <= low_max:
        return BAND_LOW
    if value > high_min:
        return BAND_HIGH
    return BAND_MEDIUM


def _saturated_fat_source(product: ProductInput) -> str:
    """Name where the saturated fat came from, not how much of it there is."""
    # Most specific first: a label that reads "edible vegetable oil (palm)"
    # should be reported as palm oil, which is the fact worth knowing.
    fats = (
        "partially hydrogenated", "hydrogenated", "vanaspati", "interesterified",
        "palmolein", "palm oil", "palm", "coconut oil", "milk fat", "butter",
        "ghee", "cream", "edible vegetable oil", "vegetable oil",
    )
    for index, raw in enumerate(product.ingredients, start=1):
        text = nova.normalise(raw)
        for fat in fats:
            if nova._contains(text, fat):
                ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(index, f"{index}th")
                return f"from {fat}, {ordinal} ingredient"
    return "from the fats in the ingredient list"


def _step2(
    product: ProductInput, nova_group: int, trace: list[TraceEntry],
) -> tuple[list[Band], int, list[str]]:
    """Returns (bands, penalty steps, positives found)."""
    bands: list[Band] = []
    penalty = 0
    basis = product.basis

    def _add(nutrient: str, value: Decimal | None, attribution: str | None = None,
             penalised: bool = True, note: str | None = None, charge: bool = True) -> int:
        """Band one nutrient. Returns the steps it costs; charges them unless told not to."""
        nonlocal penalty
        threshold = _threshold(nutrient, basis)
        if threshold is None:
            return 0
        band = _band_for(value, threshold.low_max, threshold.high_min)
        bands.append(Band(
            nutrient=nutrient, band=band, value=value, unit=threshold.unit,
            source=threshold.source, attribution=attribution, penalised=penalised, note=note,
        ))
        if band != BAND_HIGH or not penalised or value is None:
            return 0
        severe = value >= threshold.high_min * SEVERE_MULTIPLE
        steps = 2 if severe else 1
        where = f" ({attribution})" if attribution else ""
        if charge:
            penalty += steps
            trace.append(TraceEntry(
                2, "Nutrient bands", f"grade.step2.high_{nutrient.replace(' ', '_')}",
                f"High {nutrient}{where}."
                + (" More than twice the high threshold." if severe else ""),
                threshold.source.name,
                effect=f"Down {steps} step{'s' if steps > 1 else ''}.",
            ))
        return steps

    # Sugar is banded twice — as grams per 100 g and as a share of energy — and
    # they are two readings of the same sugar. Charging both would be double
    # jeopardy, and stacking penalties for one nutrient is how a gate system
    # quietly turns back into an average. The worse reading is charged once.
    sugar_band_steps = _add("total sugars", product.total_sugar_g, charge=False)
    _add("salt", product.salt_equivalent_g)
    # Saturated fat is penalised only at NOVA 3 and 4. At group 1 and 2 it comes
    # from whole foods — paneer, curd, milk, nuts — and penalising it there is
    # the mistake this engine exists to avoid.
    sat_penalised = nova_group in (nova.NOVA_PROCESSED, nova.NOVA_ULTRA_PROCESSED)
    _add(
        "saturated fat", product.saturated_fat_g,
        attribution=_saturated_fat_source(product) if sat_penalised else None,
        penalised=sat_penalised,
        note=None if sat_penalised else
        "Not penalised: at NOVA 1-2 saturated fat comes from the whole food itself.",
    )
    if not sat_penalised and product.saturated_fat_g is not None:
        trace.append(TraceEntry(
            2, "Nutrient bands", "grade.step2.saturated_fat_not_penalised",
            f"Saturated fat is {product.saturated_fat_g} g per 100 g and comes from the "
            "whole food itself.",
            "GlamGenius product policy",
            effect="No penalty. Saturated fat is only penalised at NOVA 3 and 4.",
        ))

    # Total fat is deliberately absent. See rules.py.
    share = _added_sugar_energy_share(product)
    share_steps = 1 if share is not None and share >= ADDED_SUGAR_ENERGY_DEMERIT_PCT else 0
    sugar_steps = max(sugar_band_steps, share_steps)
    if sugar_steps:
        penalty += sugar_steps
        if sugar_band_steps >= share_steps:
            finding = f"High total sugars, {product.total_sugar_g} g per 100 g."
            if sugar_band_steps == 2:
                finding += " More than twice the high threshold."
            source_name = _threshold("total sugars", basis).source.name
        else:
            finding = f"Added sugar supplies about {share:.0f}% of the energy in this product."
            source_name = FSSAI_SUGAR_ENERGY_SOURCE.name
        trace.append(TraceEntry(
            2, "Nutrient bands", "grade.step2.sugar",
            finding, source_name,
            effect=f"Down {sugar_steps} step{'s' if sugar_steps > 1 else ''}. "
                   "Sugar is charged once, on whichever reading is worse.",
        ))

    positives = _positives(product, trace)
    # Whether positives cancel a penalty is a step 2 fact about the food, so it
    # is decided here rather than after the later gates have run. The ceiling
    # still clamps the result; it just does not need to be known yet.
    if penalty and len(positives) >= POSITIVES_FOR_LIFT:
        penalty -= 1
        trace.append(TraceEntry(
            2, "Nutrient bands", "grade.step2.positives_cancel_one_penalty",
            "Positives cancelled one penalty: " + ", ".join(positives) + ".",
            "GlamGenius product policy",
            effect="Up 1 step, never past the ceiling and never against a severe finding.",
        ))
    return bands, penalty, positives


def _added_sugar_energy_share(product: ProductInput) -> Decimal | None:
    sugar = product.added_sugar_g if product.added_sugar_g is not None else product.total_sugar_g
    if sugar is None or not product.energy_kcal:
        return None
    return (sugar * KCAL_PER_G_SUGAR / product.energy_kcal) * Decimal("100")


def _positives(product: ProductInput, trace: list[TraceEntry]) -> list[str]:
    found: list[str] = []
    if product.fibre_g is not None and product.fibre_g >= FIBRE_POSITIVE_MIN:
        found.append(f"fibre {product.fibre_g} g")
    if product.protein_g is not None and product.protein_g >= PROTEIN_POSITIVE_MIN:
        found.append(f"protein {product.protein_g} g")
    if product.whole_food_pct is not None and product.whole_food_pct >= WHOLE_FOOD_POSITIVE_MIN_PCT:
        found.append(f"{product.whole_food_pct}% whole pulse, grain or nut")
    if product.fermented:
        found.append("fermented")
    if found:
        trace.append(TraceEntry(
            2, "Nutrient bands", "grade.step2.positives",
            "Positives: " + ", ".join(found) + ".",
            "GlamGenius product policy",
            effect=f"{POSITIVES_FOR_LIFT} or more can cancel one ordinary penalty, "
                   "never a severe one, and never lift past the ceiling.",
        ))
    return found


# ---------------------------------------------------------------------------
# Automatic E
# ---------------------------------------------------------------------------
def _automatic_e(product: ProductInput, trace: list[TraceEntry]) -> str | None:
    joined = nova.normalise(" ; ".join(product.ingredients))
    for marker in PHO_MARKERS:
        if nova._contains(joined, marker):
            trace.append(TraceEntry(
                2, "Nutrient bands", "grade.step2.partially_hydrogenated_oil",
                f"The ingredient list contains {marker} oil.",
                FSSAI_TRANSFAT.name, effect="Automatic E.",
            ))
            return f"The ingredient list contains {marker} oil."
    if product.trans_fat_g is not None:
        trace.append(TraceEntry(
            2, "Nutrient bands", "grade.step2.trans_fat_denominator_missing",
            "The label gives trans fat, but not the verified oils-and-fats denominator required "
            "for the regulatory calculation.",
            FSSAI_TRANSFAT.name, effect="Recorded as missing regulatory information.",
        ))
    return None


# ---------------------------------------------------------------------------
# Step 3 — additives
# ---------------------------------------------------------------------------
_SYNTHETIC_COLOURS = ("ins 102", "ins 110", "ins 122", "ins 129", "ins 133", "ins 143",
                      "tartrazine", "sunset yellow", "carmoisine", "allura red",
                      "brilliant blue", "synthetic colour", "artificial colour")


def _step3(product: ProductInput, trace: list[TraceEntry]) -> tuple[Grade | None, str | None]:
    joined = nova.normalise(" ; ".join(product.ingredients))
    ceiling: Grade | None = None
    automatic: str | None = None

    for additive in ADDITIVES:
        needles = [nova.normalise(additive.name)]
        if additive.ins:
            needles.extend([f"ins {additive.ins}", f"e{additive.ins}"])
        if not any(nova._contains(joined, needle) for needle in needles if needle):
            continue
        if additive.tier == TIER_BLACK:
            trace.append(TraceEntry(
                3, "Additives", "grade.step3.black_tier",
                f"{additive.name} is present. {additive.note or ''}".strip(),
                additive.source.name, effect="Automatic E.",
            ))
            automatic = f"{additive.name} is present."
        elif additive.tier == TIER_RED:
            ceiling = worse_of(ceiling or RED_TIER_CEILING, RED_TIER_CEILING)
            trace.append(TraceEntry(
                3, "Additives", "grade.step3.red_tier",
                f"{additive.name} is present. {additive.function}",
                additive.source.name, effect=f"Ceiling {RED_TIER_CEILING.value}.",
            ))

    if product.marketed_to_children:
        colours = [row for row in _SYNTHETIC_COLOURS if nova._contains(joined, row)]
        if colours:
            ceiling = worse_of(ceiling or CHILD_COLOUR_CEILING, CHILD_COLOUR_CEILING)
            trace.append(TraceEntry(
                3, "Additives", "grade.step3.child_marketed_synthetic_colour",
                f"A synthetic colour ({colours[0]}) in a product sold to children.",
                FSSAI_ADDITIVES.name, effect=f"Flagged. Ceiling {CHILD_COLOUR_CEILING.value}.",
            ))
    if ceiling is None and automatic is None:
        trace.append(TraceEntry(
            3, "Additives", "grade.step3.no_capping_additive",
            "No red-tier or black-tier additive on the label.",
            FSSAI_ADDITIVES.name,
        ))
    return ceiling, automatic


# ---------------------------------------------------------------------------
# Step 4 — named-ingredient integrity
# ---------------------------------------------------------------------------
def _step4(product: ProductInput, trace: list[TraceEntry]) -> Grade | None:
    promised = product.name_promises
    if not promised:
        trace.append(TraceEntry(
            4, "Named-ingredient integrity", "grade.step4.name_promises_nothing",
            "The product name does not promise a particular ingredient.",
            NAMED_INGREDIENT_SOURCE,
        ))
        return None
    declared = product.declared_percentages.get(promised)
    if declared is None:
        trace.append(TraceEntry(
            4, "Named-ingredient integrity", "grade.step4.percentage_not_declared",
            f"The name promises {promised} but the label does not declare how much.",
            NAMED_INGREDIENT_SOURCE,
            effect="Recorded as missing information.",
        ))
        return None
    rule = named_ingredient_rule(declared)
    trace.append(TraceEntry(
        4, "Named-ingredient integrity", "grade.step4.declared_percentage",
        f"The name promises {promised}; the label declares {declared}%. {rule.verdict}",
        NAMED_INGREDIENT_SOURCE,
        effect=f"Ceiling {rule.ceiling.value}." if rule.ceiling else "No ceiling.",
    ))
    return rule.ceiling


# ---------------------------------------------------------------------------
# Step 5 — confidence
# ---------------------------------------------------------------------------
def _step5(product: ProductInput, trace: list[TraceEntry]) -> tuple[str, ...]:
    missing: list[str] = []
    if not product.has_ingredient_list or not product.ingredients:
        missing.append("ingredient list")
    if not product.has_nutrition_panel or product.total_sugar_g is None and product.saturated_fat_g is None and product.salt_equivalent_g is None:
        missing.append("nutrition panel")
    if missing:
        trace.append(TraceEntry(
            5, "Confidence", "grade.step5.not_enough_information",
            "The label is missing its " + " and its ".join(missing) + ".",
            "GlamGenius product policy",
            effect="NOT_ENOUGH_INFORMATION. No grade is shown.",
        ))
    else:
        trace.append(TraceEntry(
            5, "Confidence", "grade.step5.sufficient",
            "The label carried both an ingredient list and a nutrition panel.",
            "GlamGenius product policy",
        ))
    return tuple(missing)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def grade_product(product: ProductInput) -> GradeResult:
    """Run the six gates in order and return one grade, or a reason there is none."""
    trace: list[TraceEntry] = []

    culinary = _step0(product, trace)
    if culinary is not None:
        return culinary

    nova_group, ceiling = _step1(product, trace)
    refined = _refined_grain_cap(product, trace)
    if refined is not None:
        ceiling = worse_of(ceiling, refined)

    bands, penalty, positives = _step2(product, nova_group, trace)
    severe_reason = _severe_added_sugar(product, trace)
    automatic = _automatic_e(product, trace)

    additive_ceiling, additive_automatic = _step3(product, trace)
    if additive_ceiling is not None:
        ceiling = worse_of(ceiling, additive_ceiling)
    automatic = automatic or additive_automatic

    named_ceiling = _step4(product, trace)
    if named_ceiling is not None:
        ceiling = worse_of(ceiling, named_ceiling)

    missing = _step5(product, trace)
    if missing:
        return GradeResult(
            engine_version=FOOD_GRADE_ENGINE_VERSION,
            outcome=GradeOutcome.NOT_ENOUGH_INFORMATION,
            grade=None,
            headline="Not enough information to grade this.",
            detail="The label is missing its " + " and its ".join(missing)
                   + ". We do not guess a grade.",
            nova_group=nova_group,
            ceiling=None,
            trace=tuple(trace),
            bands=tuple(bands),
            missing=missing,
        )

    if automatic or severe_reason:
        reason = automatic or severe_reason or ""
        return _result(
            product, Grade.E, ceiling, nova_group, bands, trace,
            detail=reason,
        )

    return _result(product, drop(ceiling, penalty), ceiling, nova_group, bands, trace)


def _severe_added_sugar(product: ProductInput, trace: list[TraceEntry]) -> str | None:
    """Sugar water with no food in it.

    The only route to E that comes from nutrition alone, and it needs both
    halves: added sugar dominating the energy *and* nothing much else present.
    A juice drink at 12% fruit is a poor product; a cola is not a food.
    """
    share = _added_sugar_energy_share(product)
    if share is None or share < ADDED_SUGAR_ENERGY_SEVERE_PCT:
        return None
    food = product.whole_food_pct
    if food is not None and food >= SEVERE_REQUIRES_FOOD_BELOW_PCT:
        return None
    trace.append(TraceEntry(
        2, "Nutrient bands", "grade.step2.added_sugar_dominates",
        f"Added sugar supplies about {share:.0f}% of the energy, and the product contains "
        f"{food if food is not None else 0}% whole food.",
        FSSAI_SUGAR_ENERGY_SOURCE.name,
        effect="Automatic E.",
    ))
    return (f"Added sugar supplies about {share:.0f}% of the energy in this product, "
            "and there is almost no food in it.")


_HEADLINES: dict[Grade, str] = {
    Grade.A: "Grade A. Fewer product flags.",
    Grade.B: "Grade B. Some processing flags.",
    Grade.C: "Grade C. Product facts need consideration.",
    Grade.D: "Grade D. Multiple product flags.",
    Grade.E: "Grade E. Strong product concern.",
}


def _result(
    product: ProductInput, grade: Grade, ceiling: Grade, nova_group: int,
    bands: list[Band], trace: list[TraceEntry], detail: str | None = None,
) -> GradeResult:
    reasons = [entry.finding for entry in trace if entry.effect and entry.step in (1, 2, 3, 4)]
    return GradeResult(
        engine_version=FOOD_GRADE_ENGINE_VERSION,
        outcome=GradeOutcome.GRADED,
        grade=grade,
        headline=_HEADLINES[grade],
        detail=detail or (reasons[0] if reasons else "Nothing on the label lowered the grade."),
        nova_group=nova_group,
        ceiling=ceiling,
        trace=tuple(trace),
        bands=tuple(bands),
    )


__all__ = [
    "FOOD_GRADE_ENGINE_VERSION",
    "GradeResult",
    "ProductInput",
    "TraceEntry",
    "grade_product",
]
