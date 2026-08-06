"""The metric definition registry.

Every number this product shows a user is defined here first, and a metric
cannot be defined without all six things the brief requires: a formula, a
formula version, its required inputs, what it does when those inputs are
missing, an explanation in plain English, and how often it updates.

That is enforced by the dataclass, not by convention — :class:`MetricDefinition`
has no defaults for any of them, so a metric with an undocumented formula does
not compile.

**There is no overall score, and there cannot be one.** Not "we chose not to
add one" — the shape of the data forbids it. Every metric declares the domain
it reads (``inputs``), and :func:`validate_registry` rejects any metric whose
inputs are other metrics. A composite of the thirteen below is therefore not
expressible, which is the only honest way to promise "no attractiveness score"
in a codebase that other people will keep editing.

Each metric also declares ``direction``. Some of these genuinely have a better
end (``product_expiry_risk`` is better low). Several deliberately do not:
``inventory_balance`` is ``neutral`` because there is no correct shape for
somebody's wardrobe, and reporting one would be a judgement dressed as a
measurement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

REGISTRY_VERSION = "phase7-v1"

# How a metric reads. ``ratio`` is 0-1 and renders as a percentage, ``count`` is
# a whole number, ``currency`` is rupees, ``days`` is a duration, ``scale_1_5``
# is the user's own 1-5 self-report.
UNIT_RATIO = "ratio"
UNIT_COUNT = "count"
UNIT_CURRENCY = "currency"
UNIT_DAYS = "days"
UNIT_SCALE = "scale_1_5"

# Whether a higher number is better, a lower one is, or neither.
DIRECTION_HIGHER = "higher_is_better"
DIRECTION_LOWER = "lower_is_better"
DIRECTION_NEUTRAL = "neutral"

# How often a metric is worth recomputing. Cheap ones are computed on read;
# ones that walk history are snapshotted.
FREQUENCY_ON_READ = "on_read"
FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"

# Domains a metric may read from. A metric may not read another metric — see
# ``validate_registry``. This tuple is the whole allowed vocabulary.
INPUT_DOMAINS: tuple[str, ...] = (
    "inventory", "usage", "looks", "occasions", "routines", "shopping",
    "goals", "self_report", "calendar", "expiry", "planning",
)

# What a metric does when it cannot be computed. Never "assume zero" — a zero
# looks like a real, bad score, and a person reading it has no way to tell the
# difference between "you have done nothing" and "we do not know".
MISSING_UNAVAILABLE = "report_unavailable"
MISSING_PARTIAL = "report_partial_with_named_gaps"


@dataclass(frozen=True)
class MetricDefinition:
    """One metric. Every field is required; none of them have defaults."""

    key: str
    label: str
    unit: str
    direction: str
    # The formula in words, precise enough to reimplement from. Shown to users
    # who tap "how is this worked out?", so it is written for a person.
    formula: str
    # Bumped whenever the arithmetic changes. Stored on every event, so an old
    # number is always readable against the formula that produced it.
    formula_version: str
    inputs: tuple[str, ...]
    missing_data_behaviour: str
    explanation: str
    update_frequency: str
    # What this metric deliberately does not mean. Present on every metric,
    # because the misreading is usually more harmful than the number.
    not_a_measure_of: str
    minimum_inputs: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "label": self.label, "unit": self.unit,
            "direction": self.direction, "formula": self.formula,
            "formula_version": self.formula_version, "inputs": list(self.inputs),
            "missing_data_behaviour": self.missing_data_behaviour,
            "explanation": self.explanation,
            "update_frequency": self.update_frequency,
            "not_a_measure_of": self.not_a_measure_of,
            "minimum_inputs": self.minimum_inputs,
        }


METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="wardrobe_readiness",
        label="Wardrobe Readiness",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Of the occasions you actually have in your life, the share for which a complete "
            "outfit can be built from clothes you own. Complete means every required slot for "
            "that occasion is filled — usually a top, a bottom and shoes."
        ),
        formula_version="v1",
        inputs=("inventory", "occasions"),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation=(
            "How often you can get dressed for the things you do without needing to buy anything. "
            "It counts only the occasions you have told us about or planned."
        ),
        update_frequency=FREQUENCY_ON_READ,
        not_a_measure_of="How good your clothes are, or how you look in them.",
        minimum_inputs=3,
    ),
    MetricDefinition(
        key="wardrobe_utilisation",
        label="Wardrobe Utilisation",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Items worn at least once in the last 90 days, divided by items you owned for the "
            "whole of that window. Anything added during the window is excluded, because a "
            "shirt bought yesterday has not had a chance to be worn."
        ),
        formula_version="v1",
        inputs=("inventory", "usage"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation=(
            "How much of what you own you actually use. Logging what you wear is what feeds this, "
            "so it reads low until you have been logging for a while."
        ),
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="Whether you own too much or too little.",
        minimum_inputs=5,
    ),
    MetricDefinition(
        key="outfit_variety",
        label="Outfit Variety",
        unit=UNIT_RATIO,
        direction=DIRECTION_NEUTRAL,
        formula=(
            "Distinct outfit combinations worn in the last 30 days divided by the number of days "
            "you logged an outfit. 1.0 means you never repeated a combination."
        ),
        formula_version="v1",
        inputs=("looks", "usage"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation=(
            "How much you mix things up. Shown because people ask, not because more is better — "
            "wearing a favourite combination often is a perfectly good way to dress."
        ),
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="Style, effort, or whether you are dressing well.",
        minimum_inputs=5,
    ),
    MetricDefinition(
        key="occasion_preparedness",
        label="Occasion Preparedness",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Of the occasions coming up in the next 30 days, the share that already have a "
            "planned outfit or a complete outfit available from what you own."
        ),
        formula_version="v1",
        inputs=("calendar", "planning", "inventory"),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation="Whether the things actually in your diary are covered.",
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="How important your commitments are.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="routine_consistency",
        label="Routine Consistency",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Days in the last 14 on which you completed at least one routine step, divided by 14. "
            "Completing more steps in a day does not raise it — this counts days, not effort."
        ),
        formula_version="v1",
        inputs=("routines",),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation=(
            "How regularly you are keeping to a routine. Counted as days, so a heavy day does not "
            "paper over a gap and a missed day is not a failure."
        ),
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="Whether your skin or hair is improving.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="product_expiry_risk",
        label="Product Expiry Risk",
        unit=UNIT_RATIO,
        direction=DIRECTION_LOWER,
        formula=(
            "Products already past their date, plus products expiring within 60 days that you "
            "have not used in the last 30, divided by all products with a date recorded. "
            "Products with no date recorded are excluded and reported separately."
        ),
        formula_version="v1",
        inputs=("inventory", "expiry", "usage"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation="How much of your shelf is heading for the bin unused.",
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="Whether a product is safe to use.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="value_to_recover",
        label="Value to Recover",
        unit=UNIT_CURRENCY,
        direction=DIRECTION_LOWER,
        formula=(
            "For each item with a price you entered: price × the share left × a condition factor "
            "× an inactivity factor × an expiry-proximity factor. Summed. Items with no price "
            "are excluded and counted separately rather than estimated."
        ),
        formula_version="v1",
        inputs=("inventory", "usage", "expiry"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation=(
            "A rough dependent on what you entered — an estimate of what is sitting unused that you "
            "could still get value from by using it. It is not a resale price and it is not money lost."
        ),
        update_frequency=FREQUENCY_WEEKLY,
        not_a_measure_of="Money you have wasted. Using these things is what brings the number down.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="purchase_efficiency",
        label="Purchase Efficiency",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Of items bought in the last 180 days, the share worn at least twice. Items bought in "
            "the last 30 days are excluded — they have not had a fair chance yet."
        ),
        formula_version="v1",
        inputs=("inventory", "usage", "shopping"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation="Whether the things you buy end up getting used.",
        update_frequency=FREQUENCY_WEEKLY,
        not_a_measure_of="Whether you spend too much.",
        minimum_inputs=3,
    ),
    MetricDefinition(
        key="inventory_balance",
        label="Inventory Balance",
        unit=UNIT_COUNT,
        direction=DIRECTION_NEUTRAL,
        formula=(
            "A count per category, and the number of categories with nothing in them. "
            "No target shape is applied and no single number is produced."
        ),
        formula_version="v1",
        inputs=("inventory",),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation=(
            "Where your inventory is concentrated. Deliberately not scored: there is no correct "
            "shape for a wardrobe, and pretending otherwise would be a judgement, not a measurement."
        ),
        update_frequency=FREQUENCY_ON_READ,
        not_a_measure_of="A well-balanced or badly-balanced wardrobe. There is no such target here.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="travel_readiness",
        label="Travel Readiness",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Of the packing essentials for a trip of the length you gave — outfits per day, shoes, "
            "and a toiletry set — the share you already own and that is clean and available."
        ),
        formula_version="v1",
        inputs=("inventory", "planning"),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation="Whether you could pack for a trip from what you own right now.",
        update_frequency=FREQUENCY_ON_READ,
        not_a_measure_of="Whether you are ready for any particular trip we do not know about.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="seasonal_readiness",
        label="Seasonal Readiness",
        unit=UNIT_RATIO,
        direction=DIRECTION_HIGHER,
        formula=(
            "Of the items you own that are tagged or inferred as suitable for the current Indian "
            "season, the share that are available to wear. Below three suitable items the metric "
            "reports unavailable rather than a misleadingly precise fraction."
        ),
        formula_version="v1",
        inputs=("inventory", "calendar"),
        missing_data_behaviour=MISSING_PARTIAL,
        explanation="Whether what you own suits the weather you are actually in.",
        update_frequency=FREQUENCY_WEEKLY,
        not_a_measure_of="Whether you need to buy seasonal clothes.",
        minimum_inputs=3,
    ),
    MetricDefinition(
        key="no_buy_progress",
        label="No-Buy Progress",
        unit=UNIT_DAYS,
        direction=DIRECTION_HIGHER,
        formula=(
            "Days since a no-buy goal started, counting only while no item has been added with a "
            "purchase date inside the period. Adding something resets the count and the reset is "
            "recorded with its date."
        ),
        formula_version="v1",
        inputs=("goals", "inventory", "shopping"),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation=(
            "How long you have kept to a no-buy period you set yourself. A reset is recorded plainly "
            "and without comment — it is information, not a failure."
        ),
        update_frequency=FREQUENCY_DAILY,
        not_a_measure_of="Self-control. It counts days, nothing more.",
        minimum_inputs=1,
    ),
    MetricDefinition(
        key="user_reported_confidence",
        label="How you said you felt",
        unit=UNIT_SCALE,
        direction=DIRECTION_NEUTRAL,
        formula=(
            "The average of the 1-5 ratings you recorded yourself over the window. Nothing is "
            "inferred and no photo, outfit or purchase affects it."
        ),
        formula_version="v1",
        inputs=("self_report",),
        missing_data_behaviour=MISSING_UNAVAILABLE,
        explanation=(
            "Your own words, averaged and shown back to you. The app never estimates this and never "
            "fills it in for you."
        ),
        update_frequency=FREQUENCY_ON_READ,
        not_a_measure_of="Anything the app observed. This is only ever what you typed.",
        minimum_inputs=2,
    ),
)

METRIC_BY_KEY: dict[str, MetricDefinition] = {row.key: row for row in METRICS}
METRIC_KEYS: tuple[str, ...] = tuple(row.key for row in METRICS)

# Words that would signal a metric had drifted into scoring a person rather than
# measuring their inventory or their own logged behaviour.
FORBIDDEN_METRIC_WORDS: tuple[str, ...] = (
    "attractive", "attractiveness", "beauty score", "overall score", "total score",
    "glow score", "looks score", "rating out of", "how good you look",
    "better looking", "grade", "rank you",
)


def validate_registry() -> None:
    """Assert the registry's own rules. Called at import.

    Two things are checked, and both are about the same promise. A metric may
    only read a *domain* — never another metric — so a composite score cannot be
    built out of these. And no metric may describe itself in words that would
    make it a judgement of a person.
    """
    seen = set()
    for metric in METRICS:
        if metric.key in seen:
            raise AssertionError(f"Duplicate metric key: {metric.key}")
        seen.add(metric.key)

        for source in metric.inputs:
            if source in METRIC_BY_KEY:
                raise AssertionError(
                    f"Metric '{metric.key}' reads metric '{source}'. Metrics read domains, never "
                    "other metrics — that rule is what makes an overall score impossible to build."
                )
            if source not in INPUT_DOMAINS:
                raise AssertionError(f"Metric '{metric.key}' names unknown input domain '{source}'.")

        if not metric.inputs:
            raise AssertionError(f"Metric '{metric.key}' declares no inputs.")

        text = f"{metric.key} {metric.label} {metric.formula} {metric.explanation}".lower()
        for word in FORBIDDEN_METRIC_WORDS:
            if word in text:
                raise AssertionError(f"Metric '{metric.key}' uses forbidden wording: {word!r}")


validate_registry()


def get(key: str) -> Optional[MetricDefinition]:
    return METRIC_BY_KEY.get(key)


def all_definitions() -> list[dict[str, object]]:
    return [row.as_dict() for row in METRICS]
