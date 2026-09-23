"""The reviewed component identity authority for supplement label facts.

One map decides which nutrient a printed name denotes, and it is composed only
of reviewed, hand-written spellings:

1. bare nutrient spellings whose identity is inherently explicit
   (``names.NUTRIENT_SPELLINGS``);
2. reviewed equivalent spellings (``names.REVIEWED_EQUIVALENTS`` — the two VC-07
   ascorbic-acid entries);
3. the explicit exact-form spellings in ``forms.EXACT_FORMS``, each of which
   names a compound of exactly one nutrient.

Anything else keeps its own normalised literal spelling as its identity. It can
overlap only with a product that prints the same words; it is never guessed
into another nutrient. No fuzzy match, no generated synonym, and nothing from
``knowledge.py``: its alias lists are authoring candidates for a reviewer and
do not execute here.

Legacy rows
-----------
Rows written before this authority carry a ``canonical_component_key`` chosen
by the old, broader alias set — "Triglyceride" stored as ``omega 3``, "Haldi
extract" as ``curcumin``. Those keys are not rewritten. Every customer-facing
decision instead revalidates the row: the printed ``raw_name`` goes through the
current authority, and the stored key is used only when the two agree. When
they disagree the stored grouping is not trusted and not repaired; the row has
no usable identity, so it drives no overlap, no form and no knowledge, and an
operator-only reason is logged.
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.supplements.forms import EXACT_FORMS
from app.domains.supplements.names import (
    NUTRIENT_DISPLAY_NAMES,
    NUTRIENT_SPELLINGS,
    REVIEWED_EQUIVALENTS,
    normalize_component,
)

logger = logging.getLogger(__name__)

IDENTITY_VERSION = "step-13-identity-v2"

#: Stable operator-only reason for a stored key the current authority does not give.
STORED_IDENTITY_NOT_REVIEWED = "stored_identity_disagrees_with_reviewed_authority"


def _compose() -> dict[str, str]:
    composed: dict[str, str] = {}
    sources: tuple[tuple[str, dict[str, str]], ...] = (
        ("nutrient spelling", NUTRIENT_SPELLINGS),
        ("reviewed equivalent", REVIEWED_EQUIVALENTS),
        ("exact form", {spelling: form.canonical_component_key for spelling, form in EXACT_FORMS.items()}),
    )
    for label, source in sources:
        for spelling, key in source.items():
            if key not in NUTRIENT_DISPLAY_NAMES:
                raise ValueError(f"{label} {spelling!r} names an unknown nutrient {key!r}")
            if composed.get(spelling, key) != key:
                raise ValueError(f"{spelling!r} resolves to two nutrients")
            composed[spelling] = key
    return composed


#: Normalised printed spelling -> nutrient key. The only executable identity map.
REVIEWED_IDENTITIES: dict[str, str] = _compose()


def component_identity(value: str) -> tuple[str, str]:
    """``(identity key, display name)`` for a printed name.

    A reviewed spelling gives its nutrient key and display name. Anything else
    gives its own normalised spelling and the name as printed.
    """
    normalized = normalize_component(value)
    key = REVIEWED_IDENTITIES.get(normalized)
    if key is not None:
        return key, NUTRIENT_DISPLAY_NAMES[key]
    return normalized, value.strip() or normalized


def effective_key(fact: Any) -> str | None:
    """The identity a customer-facing decision may use for this fact, or None.

    Revalidated from the printed ``raw_name`` every time. The stored
    ``canonical_component_key`` is trusted only when it agrees with the current
    reviewed authority; otherwise None, and nothing is repaired.
    """
    current, _display = component_identity(fact.raw_name or "")
    if not current:
        return None
    stored = fact.canonical_component_key
    if stored is not None and stored != current:
        logger.warning(
            "supplement_identity_not_reviewed",
            extra={"reason": STORED_IDENTITY_NOT_REVIEWED, "fact_id": str(fact.id), "identity_version": IDENTITY_VERSION},
        )
        return None
    return current


__all__ = [
    "IDENTITY_VERSION",
    "NUTRIENT_DISPLAY_NAMES",
    "REVIEWED_IDENTITIES",
    "STORED_IDENTITY_NOT_REVIEWED",
    "component_identity",
    "effective_key",
    "normalize_component",
]
