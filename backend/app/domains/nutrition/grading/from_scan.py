"""Building a gradeable product from what a barcode scan found.

The scan returns two halves that never touch on disk: our record, and the Open
Food Facts copy behind the ODbL wall. They are paired in memory for the length
of one response, which is exactly what this does — and then the pair is thrown
away with the response, same as everywhere else.

Missing values stay missing. A panel that did not declare sugar produces
``None``, which step 5 turns into NOT_ENOUGH_INFORMATION rather than a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domains.nutrition.grading.engine import ProductInput

#: Open Food Facts nutriment keys, in the order we prefer them.
_NUTRIMENT_KEYS: dict[str, tuple[str, ...]] = {
    # ``energy_100g`` has historically been supplied in different units.  Do
    # not silently put it in a kcal field: use the explicit kcal value, or an
    # explicit kJ value with the documented conversion below.
    "protein_g": ("proteins_100g",),
    "total_fat_g": ("fat_100g",),
    "saturated_fat_g": ("saturated-fat_100g",),
    "trans_fat_g": ("trans-fat_100g",),
    "total_sugar_g": ("sugars_100g",),
    "fibre_g": ("fiber_100g", "fibre_100g"),
    "sodium_g": ("sodium_100g",),
    "salt_g": ("salt_100g",),
}

_DRINK_HINTS = ("beverage", "drink", "juice", "soda", "cola", "water", "milk", "lassi")

_QUANTITY_VALUE = re.compile(
    r"(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)"
    r"\s*(?P<unit>[a-zA-Z]+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedQuantity:
    value: Decimal | None = None
    invalid: bool = False


def _quantity(value: Any, *, kind: str, default_unit: str) -> _ParsedQuantity:
    """Canonicalise an entire declared value, never a numeric prefix.

    The key establishes the unit only when the value itself is unitless.
    Explicit compatible units take precedence over that default. An absent
    value and a present value that cannot be interpreted are distinct states.
    """
    if value is None:
        return _ParsedQuantity()
    if isinstance(value, bool):
        return _ParsedQuantity(invalid=True)
    if isinstance(value, str):
        match = _QUANTITY_VALUE.fullmatch(value.strip())
        if match is None:
            return _ParsedQuantity(invalid=True)
        number = match.group("number").replace(",", "")
        unit = (match.group("unit") or default_unit).lower()
    else:
        number = str(value)
        unit = default_unit
    try:
        amount = Decimal(number)
    except (InvalidOperation, TypeError, ValueError):
        return _ParsedQuantity(invalid=True)
    if not amount.is_finite():
        return _ParsedQuantity(invalid=True)
    if kind == "mass":
        if unit == "mg":
            amount /= Decimal("1000")
        elif unit != "g":
            return _ParsedQuantity(invalid=True)
    elif kind == "energy":
        if unit == "kj":
            amount /= Decimal("4.184")
        elif unit != "kcal":
            return _ParsedQuantity(invalid=True)
    else:
        raise ValueError("unknown nutrition quantity kind")
    return _ParsedQuantity(value=amount)


def _read_quantity(
    values: dict[str, Any], keys: tuple[str, ...], *, kind: str,
    default_units: tuple[str, ...],
) -> _ParsedQuantity:
    """Reject an invalid provided alias even when another alias is valid."""
    selected: Decimal | None = None
    invalid = False
    for key, unit in zip(keys, default_units, strict=True):
        if key not in values:
            continue
        parsed = _quantity(values[key], kind=kind, default_unit=unit)
        invalid |= parsed.invalid
        if selected is None and parsed.value is not None:
            selected = parsed.value
    return _ParsedQuantity(value=None if invalid else selected, invalid=invalid)

def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _energy_kcal(nutriments: dict[str, Any]) -> Decimal | None:
    return _read_quantity(
        nutriments, ("energy-kcal_100g", "energy-kj_100g"),
        kind="energy", default_units=("kcal", "kj"),
    ).value


def split_ingredients(text: str | None) -> tuple[str, ...]:
    """Split a printed ingredient list into its parts.

    Deliberately simple: commas and semicolons outside brackets. Nested
    bracketed sub-ingredients stay attached to their parent, which is how the
    pack reads them and how the NOVA markers need to see them.
    """
    if not text or not text.strip():
        return ()
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        if char in ",;" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return tuple(part.strip() for part in parts if part.strip())


def declared_percentages(ingredients: tuple[str, ...]) -> dict[str, Decimal]:
    """Read "atta 30%" off the ingredient list, where the pack declares it."""
    found: dict[str, Decimal] = {}
    for raw in ingredients:
        match = re.search(r"([a-zA-Z][a-zA-Z \-]*?)\s*[\(\[]?\s*(\d+(?:\.\d+)?)\s*%", raw)
        if match:
            key = " ".join(match.group(1).lower().split())
            value = _decimal(match.group(2))
            if key and value is not None:
                found[key] = value
    return found


#: "75 g", "1 kg", "250 ml", "4 x 25 g" — how Indian packs state net quantity.
_QUANTITY_RE = re.compile(
    r"(?:(\d+(?:[.,]\d+)?)\s*[x\u00d7*]\s*)?"
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(kg|kgs|g|gm|gms|gram|grams|l|ltr|ltrs|litre|litres|liter|liters|ml|mls)\b",
    re.IGNORECASE,
)
#: Millilitres are counted as grams because the panel they are compared against
#: is per 100 ml for a drink, so the two cancel. It is not a density claim.
_TO_GRAMS: dict[str, Decimal] = {
    "kg": Decimal("1000"), "kgs": Decimal("1000"),
    "g": Decimal("1"), "gm": Decimal("1"), "gms": Decimal("1"),
    "gram": Decimal("1"), "grams": Decimal("1"),
    "l": Decimal("1000"), "ltr": Decimal("1000"), "ltrs": Decimal("1000"),
    "litre": Decimal("1000"), "litres": Decimal("1000"),
    "liter": Decimal("1000"), "liters": Decimal("1000"),
    "ml": Decimal("1"), "mls": Decimal("1"),
}


def pack_size_g(quantity: str | None) -> Decimal | None:
    """Grams in the pack, read off a stated net quantity.

    Without this the screen has nothing but the per-100 g panel and calling
    that "one packet" is wrong in both directions — several times over for a
    20 g sachet, and far under for a kilo bag.
    """
    if not quantity:
        return None
    match = _QUANTITY_RE.search(quantity)
    if match is None:
        return None
    count, amount, unit = match.groups()
    try:
        grams = Decimal(amount.replace(",", ".")) * _TO_GRAMS[unit.lower()]
        if count:
            grams *= Decimal(count.replace(",", "."))
    except (InvalidOperation, KeyError):
        return None
    return grams if grams > 0 else None


def basis_for(categories: str | None, name: str) -> str:
    haystack = f"{categories or ''} {name}".lower()
    return "drink" if any(hint in haystack for hint in _DRINK_HINTS) else "solid"


def build(
    *,
    barcode: str,
    name: str,
    off_half: dict[str, Any] | None,
    name_promises: str | None = None,
    marketed_to_children: bool = False,
) -> ProductInput:
    """Assemble one gradeable product from the joined scan result."""
    off = off_half or {}
    raw_nutriments = off.get("nutriments") or {}
    nutriments = raw_nutriments if isinstance(raw_nutriments, dict) else {}
    invalid_fields: set[str] = {"nutrition panel"} if raw_nutriments and not nutriments else set()
    values: dict[str, Decimal | None] = {}
    for field, keys in _NUTRIMENT_KEYS.items():
        parsed = _read_quantity(
            nutriments, keys, kind="mass", default_units=("g",) * len(keys),
        )
        values[field] = parsed.value
        if parsed.invalid:
            invalid_fields.add(field)
    energy = _read_quantity(
        nutriments, ("energy-kcal_100g", "energy-kj_100g"),
        kind="energy", default_units=("kcal", "kj"),
    )
    values["energy_kcal"] = energy.value
    if energy.invalid:
        invalid_fields.add("energy_kcal")
    ingredients = split_ingredients(off.get("ingredients_text"))
    percentages = declared_percentages(ingredients)
    promised = name_promises
    if promised is None:
        # A name that promises an ingredient the label also declares a
        # percentage for is the case step 4 exists to catch.
        promised = next((key for key in percentages if key in name.lower()), None)

    return ProductInput(
        name=name or off.get("product_name") or barcode,
        ingredients=ingredients,
        energy_kcal=values["energy_kcal"],
        protein_g=values["protein_g"],
        total_fat_g=values["total_fat_g"],
        saturated_fat_g=values["saturated_fat_g"],
        trans_fat_g=values["trans_fat_g"],
        total_sugar_g=values["total_sugar_g"],
        fibre_g=values["fibre_g"],
        sodium_g=values["sodium_g"],
        salt_g=values["salt_g"],
        basis=basis_for(off.get("categories"), name),
        categories=off.get("categories"),
        declared_percentages=percentages,
        name_promises=promised,
        marketed_to_children=marketed_to_children,
        has_ingredient_list=bool(ingredients),
        has_nutrition_panel=any(value is not None for value in values.values()),
        invalid_nutrition_fields=tuple(sorted(invalid_fields)),
    )


_CONFIRMED_NUTRIENT_KEYS: dict[str, tuple[str, ...]] = {
    "energy_kcal": ("energy_kcal",),
    "protein_g": ("protein_g",),
    "total_fat_g": ("total_fat_g",),
    "saturated_fat_g": ("saturated_fat_g",),
    "trans_fat_g": ("trans_fat_g",),
    "total_sugar_g": ("total_sugar_g", "sugars_g"),
    "added_sugar_g": ("added_sugar_g",),
    "fibre_g": ("fibre_g", "fiber_g"),
    "sodium_g": ("sodium_g",),
    "salt_g": ("salt_g",),
}


def build_confirmed_label(
    *,
    barcode: str,
    facts: dict[str, Any],
    name_promises: str | None = None,
    marketed_to_children: bool = False,
) -> ProductInput:
    """Build from Store-B facts confirmed against the physical pack.

    The confirmed-label schema is intentionally adapted explicitly instead of
    being disguised as an Open Food Facts payload. Missing values remain
    missing and no Store-A value is consulted or copied.
    """
    raw_nutrition = facts.get("nutrition_per_100g") or {}
    nutrition = raw_nutrition if isinstance(raw_nutrition, dict) else {}
    invalid_fields: set[str] = {"nutrition panel"} if raw_nutrition and not nutrition else set()
    values: dict[str, Decimal | None] = {}
    for field, keys in _CONFIRMED_NUTRIENT_KEYS.items():
        parsed = _read_quantity(
            nutrition, keys, kind="energy" if field == "energy_kcal" else "mass",
            default_units=(("kcal",) if field == "energy_kcal" else ("g",) * len(keys)),
        )
        values[field] = parsed.value
        if parsed.invalid:
            invalid_fields.add(field)

    name = str(facts.get("product_name") or barcode)
    ingredients = split_ingredients(facts.get("ingredients_text"))
    percentages = declared_percentages(ingredients)
    promised = name_promises
    if promised is None:
        promised = next((key for key in percentages if key in name.lower()), None)

    return ProductInput(
        name=name,
        ingredients=ingredients,
        energy_kcal=values["energy_kcal"],
        protein_g=values["protein_g"],
        total_fat_g=values["total_fat_g"],
        saturated_fat_g=values["saturated_fat_g"],
        trans_fat_g=values["trans_fat_g"],
        total_sugar_g=values["total_sugar_g"],
        added_sugar_g=values["added_sugar_g"],
        fibre_g=values["fibre_g"],
        sodium_g=values["sodium_g"],
        salt_g=values["salt_g"],
        basis={"per_100g": "solid", "per_100ml": "drink"}.get(facts.get("nutrition_basis"), "unknown"),
        declared_percentages=percentages,
        name_promises=promised,
        marketed_to_children=marketed_to_children,
        has_ingredient_list=bool(ingredients),
        has_nutrition_panel=any(value is not None for value in values.values()),
        invalid_nutrition_fields=tuple(sorted(invalid_fields)),
    )


__all__ = [
    "basis_for", "build", "build_confirmed_label", "declared_percentages", "pack_size_g",
    "split_ingredients",
]
