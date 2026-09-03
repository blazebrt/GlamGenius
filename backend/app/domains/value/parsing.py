"""Reading money and pack size off what a person confirmed, deterministically.

The model transcribes; this parses. That division is the whole safety story
here. An AI that returned a number would be deciding what a pack costs; an AI
that copies the clause it can see leaves the decision to code that fails closed
in every ambiguous case, and can be argued about line by line.

Both parsers refuse far more than they accept, on purpose. A wrong MRP is worse
than no MRP: the honest answer is that we do not have a usable observation, and
that answer costs a shopper nothing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: Longer than any genuine MRP clause on a pack. A bound on the input, not a
#: bound on what a product may cost — see :data:`_MAX_RUPEES` for why there is
#: no market-price assumption anywhere in this module.
MAX_MRP_TEXT_LENGTH = 200
MAX_QUANTITY_TEXT_LENGTH = 120

#: The declaration a pack must actually carry. Without one of these words the
#: number beside a rupee sign could be an offer price, a shelf price or a
#: competitor comparison, and we would be publishing it as an MRP.
_MRP_MARKER = re.compile(
    r"(?<![a-z])(?:mrp|m\.r\.p\.?|maximum\s+retail\s+price)(?![a-z])",
)

#: An amount that is explicitly rupees. A bare number is never money here.
_RUPEE_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)",
)

#: What a range looks like just after the amount we matched: "₹100-120",
#: "₹100 / 120". Either is two prices, and two prices is not an observation.
_RANGE_TAIL = re.compile(r"^\s*[-–—/]\s*\d")

#: Technical ceiling only. It exists so a malformed transcription cannot
#: produce an absurd Decimal, not because we believe food costs less than this:
#: a bulk pack that genuinely costs a lot must not be rejected for being
#: expensive, so this is set far above any plausible retail pack.
_MAX_RUPEES = Decimal("10000000")


def _normalise(text: str) -> str:
    """NFKC, collapse whitespace, casefold. The same three steps, always."""
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def parse_mrp_rupees(mrp_text: object) -> Decimal | None:
    """The rupee amount a pack declared as its MRP, or ``None``.

    ``None`` is returned for everything doubtful, and the list of doubtful
    things is long: no MRP declaration, no rupee marker, a range, two amounts,
    zero, a negative, more than two decimal places, or anything unparseable.

    Returns an exact :class:`~decimal.Decimal`. Money never touches a float in
    this codebase — binary floating point cannot represent 0.10, and a price
    that drifts by a paise per arithmetic step is a price we cannot defend.
    """
    if not isinstance(mrp_text, str):
        return None
    if len(mrp_text) > MAX_MRP_TEXT_LENGTH:
        return None
    text = _normalise(mrp_text)
    if not text:
        return None

    marker = _MRP_MARKER.search(text)
    if marker is None:
        # "₹120", "Offer ₹99", "Selling price ₹99" — a number beside a rupee
        # sign is not a declared maximum retail price.
        return None

    tail = text[marker.end():]
    matches = list(_RUPEE_AMOUNT.finditer(tail))
    if len(matches) != 1:
        # Nothing to read, or two amounts and no way to know which is the MRP.
        return None
    match = matches[0]
    if _RANGE_TAIL.match(tail[match.end():]):
        return None

    digits = match.group(1).replace(",", "")
    if "." in digits and len(digits.split(".", 1)[1]) > 2:
        # Rupees have two decimal places. A third means we misread something.
        return None
    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > _MAX_RUPEES:
        return None
    return amount


#: What each unit is, and how many base units it holds. Mass and volume stay
#: apart: converting between them needs a density that no pack prints.
_UNITS: dict[str, tuple[str, str, Decimal]] = {
    "g": ("mass", "g", Decimal(1)),
    "gm": ("mass", "g", Decimal(1)),
    "gms": ("mass", "g", Decimal(1)),
    "gram": ("mass", "g", Decimal(1)),
    "grams": ("mass", "g", Decimal(1)),
    "kg": ("mass", "g", Decimal(1000)),
    "ml": ("volume", "ml", Decimal(1)),
    "mls": ("volume", "ml", Decimal(1)),
    "l": ("volume", "ml", Decimal(1000)),
    "ltr": ("volume", "ml", Decimal(1000)),
    "ltrs": ("volume", "ml", Decimal(1000)),
    "litre": ("volume", "ml", Decimal(1000)),
    "litres": ("volume", "ml", Decimal(1000)),
    "liter": ("volume", "ml", Decimal(1000)),
    "liters": ("volume", "ml", Decimal(1000)),
}

#: Deliberately anchored at both ends. A pattern that merely *searches* would
#: happily read "500 g" out of "100 g + 20 g free" and out of "approx 500 g",
#: and both of those are packs whose size we do not actually know.
_QUANTITY = re.compile(
    r"^(?:(\d+(?:\.\d+)?)\s*[x×*]\s*)?(\d+(?:\.\d+)?)\s*([a-z]+)$",
)


@dataclass(frozen=True)
class Quantity:
    """How much is in the pack, in one base unit, with its dimension kept.

    ``dimension`` is carried rather than inferred from ``base_unit`` at each
    call site because it is the thing the comparison actually gates on: grams
    are only ever compared with grams, millilitres only with millilitres.
    """

    dimension: str      # mass | volume
    base_amount: Decimal
    base_unit: str      # g | ml


def parse_quantity(net_quantity: object) -> Quantity | None:
    """The net quantity a pack declared, normalised to grams or millilitres.

    Conservative by construction: the whole string must be a quantity. Anything
    with a bonus, an approximation, a count of pieces, a serving claim or a
    second quantity is refused, because the denominator of a price has to be a
    number the pack actually states.

    Never inferred from a product name, a category or a serving size.
    """
    if not isinstance(net_quantity, str):
        return None
    if len(net_quantity) > MAX_QUANTITY_TEXT_LENGTH:
        return None
    match = _QUANTITY.match(_normalise(net_quantity))
    if match is None:
        return None
    count_text, amount_text, unit_text = match.groups()
    unit = _UNITS.get(unit_text)
    if unit is None:
        return None
    dimension, base_unit, factor = unit
    try:
        amount = Decimal(amount_text) * factor
        if count_text is not None:
            amount *= Decimal(count_text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    return Quantity(dimension=dimension, base_amount=amount, base_unit=base_unit)


__all__ = [
    "MAX_MRP_TEXT_LENGTH",
    "MAX_QUANTITY_TEXT_LENGTH",
    "Quantity",
    "parse_mrp_rupees",
    "parse_quantity",
]
