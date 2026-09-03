"""What may be said about money, and how the arithmetic is done.

The claim this milestone makes is deliberately small: *a confirmed pack label
recently stated this MRP, and this is what that works out to per 100 g.* Every
rule below exists to stop it growing into something larger — a value judgement,
a saving, a recommendation, or a claim about what a shop will charge today.

Two boundaries are worth stating outright.

**MRP is not a selling price.** It is the maximum a pack may legally be sold
for, printed on the pack. We have no evidence at all about discounting, so no
surface may call it a price, a deal, or what somebody will pay.

**Quality and money never combine.** There is no number here that mixes a grade
with rupees. The Constitution rejects a single composite score averaging
incompatible things, and a letter divided by a price is exactly that.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

#: Bumped when any rule in this module changes meaning, so an answer can be read
#: back against the policy that produced it.
PACK_MRP_VALUE_POLICY_VERSION = "pack-mrp-value-v1"

#: How long a confirmed pack observation may support a public MRP comparison.
#:
#: This is *our* comparison policy, not a claim that a printed MRP legally
#: expires. Packs are repriced and reprinted, and repeating a two-month-old
#: observation as though it still described the shelf would be asserting
#: something we cannot know.
#:
#: Deliberately its own number, not the Open Food Facts cache window. The two
#: are the same length today and answer different questions — one is "how old is
#: our copy of somebody's database", the other is "how old is our reading of a
#: physical pack" — so they get separate constants that can move apart.
MRP_OBSERVATION_MAX_AGE_DAYS = 30
MRP_OBSERVATION_MAX_AGE = timedelta(days=MRP_OBSERVATION_MAX_AGE_DAYS)

#: The two states the envelope can be in. Same vocabulary as the alternative.
STATUS_AVAILABLE = "available"
STATUS_NOT_ENOUGH_INFORMATION = "not_enough_information"

#: Why the envelope reads the way it does. A closed set, resolved to words by
#: the app's string file — no prose crosses this boundary.
REASON_AVAILABLE = "comparison_available"
REASON_NO_COMPARABLE_ALTERNATIVE = "no_comparable_alternative"
REASON_CURRENT_MRP_UNAVAILABLE = "current_mrp_unavailable"
REASON_CANDIDATE_MRP_UNAVAILABLE = "candidate_mrp_unavailable"
REASON_CURRENT_QUANTITY_UNAVAILABLE = "current_quantity_unavailable"
REASON_CANDIDATE_QUANTITY_UNAVAILABLE = "candidate_quantity_unavailable"
REASON_CURRENT_STALE = "current_mrp_observation_stale"
REASON_CANDIDATE_STALE = "candidate_mrp_observation_stale"
REASON_QUANTITY_BASIS_INCOMPATIBLE = "quantity_basis_incompatible"

#: Where an observation came from. One value, because there is one source.
SOURCE_CONFIRMED_PACK_LABEL = "confirmed_pack_label"

#: What the arithmetic says, and nothing more. Not a winner, not a verdict, not
#: advice — three words describing which of two numbers is larger.
RELATIONSHIP_LOWER = "candidate_lower_mrp_per_100"
RELATIONSHIP_SAME = "same_mrp_per_100"
RELATIONSHIP_HIGHER = "candidate_higher_mrp_per_100"

#: Which physical dimension each scientific basis requires of a pack size.
#: Grams are compared with grams and millilitres with millilitres; there is no
#: conversion between them and there will not be one, because it needs a density
#: nobody prints.
DIMENSION_FOR_BASIS: dict[str, str] = {"per_100g": "mass", "per_100ml": "volume"}

#: One paise. Every public money string is quantised to this, once, at the
#: boundary — the arithmetic above it stays exact.
_MONEY = Decimal("0.01")


def dimension_for_basis(basis: object) -> str | None:
    """``per_100g`` -> ``mass``. Anything unrecognised is ``None`` and fails closed."""
    return DIMENSION_FOR_BASIS.get(basis) if isinstance(basis, str) else None


def observation_is_fresh(observed_at: datetime | None, *, now: datetime | None = None) -> bool:
    """Is this pack observation recent enough to support a public comparison?

    The boundary is closed at the young end and open at the old end: strictly
    less than the window is fresh, exactly the window is already stale. Stating
    it once here is what makes it testable rather than accidental.

    An observation with no server timestamp is not fresh. We date these from the
    database's own clock, never a phone's, so a missing date means something is
    wrong rather than something is recent.
    """
    if observed_at is None:
        return False
    moment = now or datetime.now(UTC)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - observed_at) < MRP_OBSERVATION_MAX_AGE


def mrp_per_100(mrp_rupees: Decimal, base_amount: Decimal) -> Decimal | None:
    """Rupees per 100 g or per 100 ml, exactly.

    Kept as an unrounded :class:`~decimal.Decimal`. Rounding happens once, at
    the presentation boundary, so a difference is computed from the same exact
    numbers the two sides were computed from rather than from two already-
    rounded strings.
    """
    if base_amount <= 0:
        return None
    return mrp_rupees * Decimal(100) / base_amount


def money_string(amount: Decimal) -> str:
    """The one public representation of money: a decimal string, two places.

    A string rather than a JSON number because binary floating point cannot
    hold 0.10, and a client that parses ``24.0000000001`` and renders ₹24.00 has
    been lucky rather than correct. The frontend formats this; it never does
    arithmetic on it.
    """
    return str(amount.quantize(_MONEY, rounding=ROUND_HALF_UP))


def quantity_string(amount: Decimal) -> str:
    """A pack size without trailing noise: ``500``, ``1500``, ``1.5``."""
    normalised = amount.normalize()
    # Decimal normalises 500 to 5E+2; expand it back to something a person reads.
    if normalised == normalised.to_integral_value():
        return str(normalised.quantize(Decimal(1)))
    return str(normalised)


def relationship(candidate_per_100: Decimal, current_per_100: Decimal) -> str:
    """Which of the two per-100 figures is larger. Arithmetic, not a judgement."""
    if candidate_per_100 < current_per_100:
        return RELATIONSHIP_LOWER
    if candidate_per_100 > current_per_100:
        return RELATIONSHIP_HIGHER
    return RELATIONSHIP_SAME


def difference(candidate_per_100: Decimal, current_per_100: Decimal) -> Decimal:
    """Candidate minus current, in that order, defined once and for all.

    Negative means the candidate's MRP per 100 is the lower of the two; positive
    means it is higher. Fixing the direction here is what stops the backend and
    the app disagreeing about which way the sign points.
    """
    return candidate_per_100 - current_per_100


__all__ = [
    "DIMENSION_FOR_BASIS",
    "MRP_OBSERVATION_MAX_AGE",
    "MRP_OBSERVATION_MAX_AGE_DAYS",
    "PACK_MRP_VALUE_POLICY_VERSION",
    "REASON_AVAILABLE",
    "REASON_CANDIDATE_MRP_UNAVAILABLE",
    "REASON_CANDIDATE_QUANTITY_UNAVAILABLE",
    "REASON_CANDIDATE_STALE",
    "REASON_CURRENT_MRP_UNAVAILABLE",
    "REASON_CURRENT_QUANTITY_UNAVAILABLE",
    "REASON_CURRENT_STALE",
    "REASON_NO_COMPARABLE_ALTERNATIVE",
    "REASON_QUANTITY_BASIS_INCOMPATIBLE",
    "RELATIONSHIP_HIGHER",
    "RELATIONSHIP_LOWER",
    "RELATIONSHIP_SAME",
    "SOURCE_CONFIRMED_PACK_LABEL",
    "STATUS_AVAILABLE",
    "STATUS_NOT_ENOUGH_INFORMATION",
    "difference",
    "dimension_for_basis",
    "mrp_per_100",
    "money_string",
    "observation_is_fresh",
    "quantity_string",
    "relationship",
]
