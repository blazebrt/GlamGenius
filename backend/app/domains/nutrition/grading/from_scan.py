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
    "protein_g": ("proteins_100g",),
    "total_fat_g": ("fat_100g",),
    "saturated_fat_g": ("saturated-fat_100g",),
    "trans_fat_g": ("trans-fat_100g",),
    "total_sugar_g": ("sugars_100g",),
    "fibre_g": ("fiber_100g", "fibre_100g"),
    "sodium_g": ("sodium_100g",),
    "salt_g": ("salt_100g",),
}

#: The same nutrients as they come back from a label transcription, which uses
#: its own key names rather than Open Food Facts'. Several spellings each,
#: because the transcription copies the panel as printed and Indian packs are
#: not consistent about wording.
_LABEL_KEYS: dict[str, tuple[str, ...]] = {
    "protein_g": ("protein_g", "proteins_g", "protein"),
    "total_fat_g": ("total_fat_g", "fat_g", "total_fat"),
    "saturated_fat_g": ("saturated_fat_g", "saturates_g", "saturated_fat"),
    "trans_fat_g": ("trans_fat_g", "trans_fat"),
    "total_sugar_g": ("total_sugar_g", "sugars_g", "sugar_g", "total_sugars_g"),
    "added_sugar_g": ("added_sugar_g", "added_sugars_g"),
    "fibre_g": ("fibre_g", "fiber_g", "dietary_fibre_g", "dietary_fiber_g"),
    "sodium_g": ("sodium_g", "sodium"),
    "salt_g": ("salt_g", "salt"),
}
_LABEL_ENERGY_KCAL_KEYS: tuple[str, ...] = ("energy_kcal", "energy_kcal_100g", "energy")
_LABEL_ENERGY_KJ_KEYS: tuple[str, ...] = ("energy_kj", "energy_kj_100g")

_DRINK_HINTS = ("beverage", "drink", "juice", "soda", "cola", "water", "milk", "lassi")

#: Energy needs its own handling because Open Food Facts stores two different
#: units under similar names. ``energy-kcal_100g`` is kilocalories;
#: ``energy_100g`` is the generic figure and is kilojoules. Reading the generic
#: one as kcal makes every product look about 4.2x more energetic than it is,
#: which quietly shrinks sugar's share of energy and lets a product slip under
#: the gates that share feeds.
_ENERGY_KCAL_KEYS: tuple[str, ...] = ("energy-kcal_100g", "energy_kcal_100g")
_ENERGY_KJ_KEYS: tuple[str, ...] = ("energy-kj_100g", "energy_kj_100g", "energy_100g")
#: The thermochemical calorie, which is what food labelling uses.
KJ_PER_KCAL = Decimal("4.184")


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        pass
    # A transcription copies the panel as printed, so "22.5 g" and "1,050 kJ"
    # arrive as written. Take the leading number and leave the unit behind.
    match = re.search(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?", str(value))
    if match is None:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


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


def energy_kcal_from(nutriments: dict[str, Any]) -> Decimal | None:
    """Energy per 100 g in kilocalories, whichever way the record states it."""
    for key in _ENERGY_KCAL_KEYS:
        value = _decimal(nutriments.get(key))
        if value is not None:
            return value
    for key in _ENERGY_KJ_KEYS:
        value = _decimal(nutriments.get(key))
        if value is not None:
            return value / KJ_PER_KCAL
    return None


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


def _label_values(label: dict[str, Any]) -> dict[str, Decimal | None]:
    """Nutrition off a confirmed label transcription, in ProductInput's terms."""
    panel = label.get("nutrition_per_100g") or {}
    lowered = {str(key).strip().lower(): value for key, value in panel.items()}
    values: dict[str, Decimal | None] = {}
    for field, keys in _LABEL_KEYS.items():
        values[field] = next(
            (v for v in (_decimal(lowered.get(key)) for key in keys) if v is not None), None
        )
    energy = next(
        (v for v in (_decimal(lowered.get(key)) for key in _LABEL_ENERGY_KCAL_KEYS)
         if v is not None), None
    )
    if energy is None:
        kilojoules = next(
            (v for v in (_decimal(lowered.get(key)) for key in _LABEL_ENERGY_KJ_KEYS)
             if v is not None), None
        )
        energy = kilojoules / KJ_PER_KCAL if kilojoules is not None else None
    values["energy_kcal"] = energy
    return values


def build(
    *,
    barcode: str,
    name: str,
    off_half: dict[str, Any] | None,
    label_half: dict[str, Any] | None = None,
    name_promises: str | None = None,
    marketed_to_children: bool = False,
) -> ProductInput:
    """Assemble one gradeable product from the joined scan result.

    ``label_half`` is our own confirmed reading of the pack, and it fills the
    gaps rather than replacing anything: Open Food Facts wins where it has a
    value, because it is the wider record, and the label answers where their
    copy is silent or missing entirely. The two are read side by side here and
    are never written back together — the ODbL wall, same as everywhere else.
    """
    off = off_half or {}
    label = label_half or {}
    nutriments = off.get("nutriments") or {}
    values: dict[str, Decimal | None] = {}
    for field, keys in _NUTRIMENT_KEYS.items():
        values[field] = next(
            (v for v in (_decimal(nutriments.get(key)) for key in keys) if v is not None), None
        )
    values["energy_kcal"] = energy_kcal_from(nutriments)
    if label:
        for field, value in _label_values(label).items():
            if values.get(field) is None:
                values[field] = value
    ingredients = split_ingredients(
        off.get("ingredients_text") or label.get("ingredients_text")
    )
    percentages = declared_percentages(ingredients)
    promised = name_promises
    if promised is None:
        # A name that promises an ingredient the label also declares a
        # percentage for is the case step 4 exists to catch.
        promised = next((key for key in percentages if key in name.lower()), None)

    return ProductInput(
        name=name or off.get("product_name") or label.get("product_name") or barcode,
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
        added_sugar_g=values.get("added_sugar_g"),
        basis=basis_for(off.get("categories"), name),
        declared_percentages=percentages,
        name_promises=promised,
        marketed_to_children=marketed_to_children,
        has_ingredient_list=bool(ingredients),
        has_nutrition_panel=any(value is not None for value in values.values()),
    )


__all__ = [
    "basis_for", "build", "declared_percentages", "energy_kcal_from", "pack_size_g",
    "split_ingredients",
]
