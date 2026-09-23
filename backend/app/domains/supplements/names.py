"""The reviewed vocabulary a supplement label name is read against.

Constants only, and one normaliser. Every spelling in this file was written by
hand and reviewed with the change that added it; nothing here is generated,
folded in from another module, or learned. In particular nothing here comes
from ``knowledge.py``: that file says itself that none of its contents has been
checked, so its alias lists are authoring candidates for a reviewer, never
identity authority for a customer.

``identity.py`` composes these with the explicit exact-form spellings in
``forms.py`` into the one executable identity map.
"""
from __future__ import annotations

import re
import unicodedata


def normalize_component(value: str) -> str:
    """Case-folded, punctuation-free, single-spaced. Deterministic and Unicode-stable."""
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


#: The nutrients this product can name, and how it names them.
NUTRIENT_DISPLAY_NAMES: dict[str, str] = {
    "magnesium": "Magnesium",
    "iron": "Iron",
    "zinc": "Zinc",
    "calcium": "Calcium",
    "vitamin c": "Vitamin C",
    "vitamin d": "Vitamin D",
    "vitamin b12": "Vitamin B12",
    "folate": "Folate",
    "coenzyme q10": "Coenzyme Q10",
    "curcumin": "Curcumin",
    "omega 3": "Omega-3",
}

#: Bare nutrient spellings whose identity is inherently explicit. A label that
#: prints one of these names the nutrient and no compound form.
NUTRIENT_SPELLINGS: dict[str, str] = {
    "magnesium": "magnesium",
    "elemental magnesium": "magnesium",
    "iron": "iron",
    "elemental iron": "iron",
    "zinc": "zinc",
    "elemental zinc": "zinc",
    "calcium": "calcium",
    "elemental calcium": "calcium",
    "vitamin c": "vitamin c",
    "vit c": "vitamin c",
    "vitamin d": "vitamin d",
    "vit d": "vitamin d",
    "vitamin b12": "vitamin b12",
    "vit b12": "vitamin b12",
    "folate": "folate",
    "coenzyme q10": "coenzyme q10",
    "coq10": "coenzyme q10",
    "co q10": "coenzyme q10",
    "co q 10": "coenzyme q10",
    "curcumin": "curcumin",
    "omega 3": "omega 3",
    "omega 3 fatty acids": "omega 3",
}

#: Reviewed equivalent spellings. The two VC-07 entries: ascorbic acid is
#: vitamin C by definition, not by resemblance.
REVIEWED_EQUIVALENTS: dict[str, str] = {
    "ascorbic acid": "vitamin c",
    "l ascorbic acid": "vitamin c",
}


__all__ = [
    "NUTRIENT_DISPLAY_NAMES",
    "NUTRIENT_SPELLINGS",
    "REVIEWED_EQUIVALENTS",
    "normalize_component",
]
