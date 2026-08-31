"""The grade ladder, the ceilings, and the tables the gates read.

Two decisions here shape every result, and both are deliberate departures from
the European scheme this replaces.

**Total fat is never penalised on its own.** Penalising it is what makes
Nutri-Score call ghee red and rank a low-fat biscuit above paneer. Fat is
penalised here only as saturated fat, and only where step 2 says it counts.

**Nothing averages.** A grade starts at a ceiling and can only be held or
lowered. A good number can cancel one ordinary penalty on a product that
contains real food; it can never lift anything past its ceiling, and it never
touches a severe finding.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domains.nutrition.food_reference import (
    FSA_FOP,
    FSSAI_LABELLING,
    ICMR_NIN_2024,
    Source,
)


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class GradeOutcome(StrEnum):
    """What kind of answer this is. Only ``GRADED`` carries a letter."""

    GRADED = "graded"
    #: A cooking ingredient. A letter would misrepresent it.
    NOT_GRADED = "not_graded"
    #: The label did not carry what a grade needs. We say so, and show nothing.
    NOT_ENOUGH_INFORMATION = "not_enough_information"


#: Worst last. Every cap and every drop moves along this list.
GRADE_ORDER: tuple[Grade, ...] = (Grade.A, Grade.B, Grade.C, Grade.D, Grade.E)


def worse_of(left: Grade, right: Grade) -> Grade:
    """The lower of two grades. A ceiling is applied with this."""
    return left if GRADE_ORDER.index(left) >= GRADE_ORDER.index(right) else right


def drop(grade: Grade, steps: int) -> Grade:
    """Move down the ladder, stopping at E."""
    return GRADE_ORDER[min(GRADE_ORDER.index(grade) + max(steps, 0), len(GRADE_ORDER) - 1)]


def lift(grade: Grade, steps: int, ceiling: Grade) -> Grade:
    """Move up the ladder, never past the ceiling."""
    lifted = GRADE_ORDER[max(GRADE_ORDER.index(grade) - max(steps, 0), 0)]
    return worse_of(lifted, ceiling)


# ---------------------------------------------------------------------------
# Step 1 ceilings
# ---------------------------------------------------------------------------
#: What each NOVA group is allowed to reach at best. Group 4 cannot exceed C
#: whatever its nutrition panel says — that is the whole point of the gate.
NOVA_CEILINGS: dict[int, Grade] = {
    1: Grade.A,
    2: Grade.A,
    3: Grade.B,
    4: Grade.C,
}


# ---------------------------------------------------------------------------
# Step 2 bands
# ---------------------------------------------------------------------------
BAND_LOW = "low"
BAND_MEDIUM = "medium"
BAND_HIGH = "high"
BAND_UNKNOWN = "unknown"

#: A nutrient at or above this multiple of its high threshold is severe, and
#: costs two steps rather than one. Fried namkeen at 14 g saturated fat is not
#: "a bit over" the 5 g line, and a rule that treats it the same as 5.5 g is
#: not reading the pack.
SEVERE_MULTIPLE = Decimal("2")

#: Added sugar as a share of total energy. The first figure is the FSSAI
#: front-of-pack trigger this product already records; the second is ours, and
#: is labelled as ours wherever it is reported.
ADDED_SUGAR_ENERGY_DEMERIT_PCT = Decimal("30")
ADDED_SUGAR_ENERGY_SEVERE_PCT = Decimal("50")
#: A product can only be called "sugar with nothing in it" when there is
#: genuinely no food in it.
SEVERE_REQUIRES_FOOD_BELOW_PCT = Decimal("10")

#: Energy per gram, for turning grams of sugar into a share of energy.
KCAL_PER_G_SUGAR = Decimal("4")

#: Ingredient wordings that mean a sugar was added to this product.
#:
#: Indian panels usually declare only total sugar, so this list is what makes
#: the total usable: it separates a biscuit that lists sugar second from plain
#: milk or a pure juice, where every gram of sugar came with the food itself.
#: Lactose is deliberately absent — it is milk's own sugar, not an addition.
ADDED_SUGAR_INGREDIENTS: tuple[str, ...] = (
    "sugar", "cane sugar", "refined sugar", "brown sugar", "caster sugar",
    "invert sugar", "invert syrup", "invert sugar syrup",
    "glucose", "liquid glucose", "glucose syrup", "dextrose", "dextrin",
    "fructose", "high fructose corn syrup", "hfcs", "corn syrup",
    "maltose", "high maltose corn syrup", "maltodextrin", "malto dextrin",
    "golden syrup", "malt extract", "malt syrup", "molasses",
    "honey", "jaggery", "gur", "shakkar", "misri", "khandsari",
    "date syrup", "fruit juice concentrate", "juice concentrate",
    "sucrose", "syrup", "treacle", "caramel syrup",
)


@dataclass(frozen=True)
class Band:
    nutrient: str
    band: str
    value: Decimal | None
    unit: str
    source: Source
    #: Where it came from, named rather than numbered — "from palm oil, 3rd
    #: ingredient" says more than "6.5 g".
    attribution: str | None = None
    penalised: bool = True
    note: str | None = None


# ---------------------------------------------------------------------------
# Refined grain
# ---------------------------------------------------------------------------
#: A refined flour has had the bran and germ removed. It is not ultra-processed
#: and carries no marker, so NOVA cannot see it — maida's ingredient list is one
#: line long. Without this cap maida grades A, which is the same failure in the
#: opposite direction from calling ghee red.
#:
#: Deliberately a short, named list rather than a rule about "refined". Poha,
#: atta, suji and dal are not on it.
REFINED_GRAINS: tuple[str, ...] = (
    "maida", "refined wheat flour", "refined flour", "refined atta",
    "wheat flour (maida)", "refined rice flour", "corn starch", "maize starch",
    "corn flour", "refined corn flour",
)
REFINED_GRAIN_CEILING = Grade.C
#: The published guidance behind the refined-grain ceiling.
#:
#: A ``Source`` rather than a sentence, because this rule lowers a grade and a
#: negative claim has to be openable: the customer sees the ceiling, taps the
#: source, and reads the guidance we are relying on. The interpretation — that
#: a refined grain as the main ingredient caps the grade — is ours, and is
#: named as ours in the rule's own explanation.
REFINED_GRAIN_SOURCE = ICMR_NIN_2024


# ---------------------------------------------------------------------------
# Step 3 additive caps
# ---------------------------------------------------------------------------
RED_TIER_CEILING = Grade.D
BLACK_TIER_GRADE = Grade.E
#: A synthetic colour in something sold to children caps the grade, and is
#: named on the result whether or not it was the binding constraint.
CHILD_COLOUR_CEILING = Grade.D


# ---------------------------------------------------------------------------
# Step 4 named-ingredient integrity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NamedIngredientRule:
    floor_pct: Decimal
    ceiling: Grade | None
    verdict: str


#: Read top down; the first rule whose floor the declared percentage meets wins.
NAMED_INGREDIENT_RULES: tuple[NamedIngredientRule, ...] = (
    NamedIngredientRule(Decimal("50"), None,
                        "The product is mostly what its name says. No penalty."),
    NamedIngredientRule(Decimal("25"), None,
                        "Less than half of this is what the name promises."),
    NamedIngredientRule(Decimal("10"), Grade.C,
                        "The named ingredient is a minor part of this product."),
    NamedIngredientRule(Decimal("0"), Grade.D,
                        "The name promises an ingredient that is barely in it."),
)

#: The regulation that requires an emphasised ingredient's percentage to be
#: declared. A ``Source`` for the same reason as above: step 4 lowers grades.
NAMED_INGREDIENT_SOURCE = FSSAI_LABELLING


def named_ingredient_rule(declared_pct: Decimal) -> NamedIngredientRule:
    for rule in NAMED_INGREDIENT_RULES:
        if declared_pct >= rule.floor_pct:
            return rule
    return NAMED_INGREDIENT_RULES[-1]


# ---------------------------------------------------------------------------
# Positives
# ---------------------------------------------------------------------------
#: Per 100 g. A product must clear these to earn a lift.
FIBRE_POSITIVE_MIN = Decimal("6")
PROTEIN_POSITIVE_MIN = Decimal("8")
WHOLE_FOOD_POSITIVE_MIN_PCT = Decimal("50")
#: How many positives are needed before one ordinary penalty is cancelled.
POSITIVES_FOR_LIFT = 2

POSITIVE_SOURCE = (
    "GlamGenius product policy. Fibre and protein floors are our own and are "
    "reported as ours; they are not taken from a published banding."
)

FSA_SOURCE = FSA_FOP
FSSAI_SUGAR_ENERGY_SOURCE = FSSAI_LABELLING


__all__ = [
    "ADDED_SUGAR_ENERGY_DEMERIT_PCT",
    "ADDED_SUGAR_ENERGY_SEVERE_PCT",
    "ADDED_SUGAR_INGREDIENTS",
    "BAND_HIGH",
    "BAND_LOW",
    "BAND_MEDIUM",
    "BAND_UNKNOWN",
    "BLACK_TIER_GRADE",
    "CHILD_COLOUR_CEILING",
    "FIBRE_POSITIVE_MIN",
    "GRADE_ORDER",
    "NAMED_INGREDIENT_RULES",
    "NAMED_INGREDIENT_SOURCE",
    "NOVA_CEILINGS",
    "POSITIVES_FOR_LIFT",
    "PROTEIN_POSITIVE_MIN",
    "REFINED_GRAINS",
    "REFINED_GRAIN_CEILING",
    "RED_TIER_CEILING",
    "SEVERE_MULTIPLE",
    "SEVERE_REQUIRES_FOOD_BELOW_PCT",
    "WHOLE_FOOD_POSITIVE_MIN_PCT",
    "Band",
    "Grade",
    "GradeOutcome",
    "NamedIngredientRule",
    "drop",
    "lift",
    "named_ingredient_rule",
    "worse_of",
]
