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
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domains.nutrition.grading.engine import ProductInput

#: Open Food Facts nutriment keys, in the order we prefer them.
_NUTRIMENT_KEYS: dict[str, tuple[str, ...]] = {
    # ``energy_100g`` has historically been supplied in different units.  Do
    # not silently put it in a kcal field: use the explicit kcal value, or an
    # explicit kJ value with the documented conversion below.
    "energy_kcal": ("energy-kcal_100g",),
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


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _energy_kcal(nutriments: dict[str, Any]) -> Decimal | None:
    explicit_kcal = _decimal(nutriments.get("energy-kcal_100g"))
    if explicit_kcal is not None:
        return explicit_kcal
    kj = _decimal(nutriments.get("energy-kj_100g"))
    return (kj / Decimal("4.184")) if kj is not None else None


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
    nutriments = off.get("nutriments") or {}
    values: dict[str, Decimal | None] = {}
    for field, keys in _NUTRIMENT_KEYS.items():
        values[field] = next(
            (v for v in (_decimal(nutriments.get(key)) for key in keys) if v is not None), None
        )
    values["energy_kcal"] = _energy_kcal(nutriments)
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
        declared_percentages=percentages,
        name_promises=promised,
        marketed_to_children=marketed_to_children,
        has_ingredient_list=bool(ingredients),
        has_nutrition_panel=any(value is not None for value in values.values()),
    )


__all__ = ["basis_for", "build", "declared_percentages", "split_ingredients"]
