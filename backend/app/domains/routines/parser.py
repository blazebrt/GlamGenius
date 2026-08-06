"""Reading an ingredient list off a product.

Labels are messy. The same ingredient appears as ``Sodium Hyaluronate``, ``HA``,
``hyaluronic acid`` or ``Hyaluronan`` depending on who wrote the box, so
matching on one spelling silently sees an empty shelf and the whole rules engine
goes quiet.

Two inputs are supported and they are treated differently:

* ``ingredients_text`` — the INCI list copied off the packet. Position matters
  a little (things near the front are present in larger amounts), so it is kept.
* ``active_ingredients`` — the short list a user typed themselves. Higher
  confidence, because a person chose it.

Everything the parser produces carries a **confidence** and a
**needs_confirmation** flag. Anything read off a photo at low confidence is a
draft the user confirms — the same boundary Phase 3 drew around extracted
inventory.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.domains.routines.ontology import INGREDIENT_BY_KEY, alias_index

# Below this, a match is reported but must be confirmed before it drives a
# warning. A wrong ingredient produces a wrong warning, which is worse than
# no warning.
CONFIRMATION_THRESHOLD = 0.6

SOURCE_USER = "user_declared"
SOURCE_LABEL = "label_text"
SOURCE_EXTRACTED = "photo_extracted"

_SOURCE_CONFIDENCE = {
    SOURCE_USER: 1.0,
    SOURCE_LABEL: 0.85,
    SOURCE_EXTRACTED: 0.55,
}

_ALIASES = alias_index()
# Longest first, so "hyaluronic acid" wins over "acid" and "cleansing oil" over "oil".
_ALIASES_BY_LENGTH: tuple[str, ...] = tuple(sorted(_ALIASES, key=len, reverse=True))

_SPLIT = re.compile(r"[,;•\n\r\|]+")
_NOISE = re.compile(r"\((?:[^()]*)\)|\d+(\.\d+)?\s*%|\bmay contain\b|\bci \d+\b", re.IGNORECASE)


@dataclass
class ParsedIngredient:
    key: str
    display_name: str
    family: str
    matched_text: str
    position: int | None
    confidence: float
    source: str

    @property
    def needs_confirmation(self) -> bool:
        return self.confidence < CONFIRMATION_THRESHOLD

    def as_dict(self) -> dict[str, Any]:
        return {
            "ingredient_key": self.key,
            "display_name": self.display_name,
            "family": self.family,
            "matched_text": self.matched_text,
            "position": self.position,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "needs_confirmation": self.needs_confirmation,
        }


def _clean(value: str) -> str:
    """Strip the things labels put in that are not ingredient names."""
    text = _NOISE.sub(" ", str(value or "").lower())
    text = text.replace("*", " ").replace("™", " ").replace("®", " ")
    return " ".join(text.split())


def _match_in(text: str) -> list[tuple[str, str]]:
    """Every ingredient alias present in a piece of text, longest first."""
    found: list[tuple[str, str]] = []
    seen: set = set()
    remaining = text
    for alias in _ALIASES_BY_LENGTH:
        if alias not in remaining:
            continue
        key = _ALIASES[alias]
        if key in seen:
            continue
        # Word boundaries, so "retinal" does not match inside "retinaldehyde"
        # by accident and "ha" does not match inside "shampoo".
        if not re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", remaining):
            continue
        seen.add(key)
        found.append((key, alias))
        # Remove what matched so a shorter alias cannot claim the same words.
        remaining = re.sub(rf"(?<!\w){re.escape(alias)}(?!\w)", " ", remaining)
    return found


def parse_label(text: str | None, *, source: str = SOURCE_LABEL) -> list[ParsedIngredient]:
    """Parse a full INCI list. Position in the list is preserved."""
    if not text:
        return []
    base_confidence = _SOURCE_CONFIDENCE.get(source, 0.6)
    results: list[ParsedIngredient] = []
    seen: set = set()

    for position, chunk in enumerate(_SPLIT.split(str(text))):
        cleaned = _clean(chunk)
        if not cleaned:
            continue
        for key, alias in _match_in(cleaned):
            if key in seen:
                continue
            seen.add(key)
            row = INGREDIENT_BY_KEY[key]
            # An exact single-token entry is a stronger match than a phrase
            # spotted inside a longer sentence.
            exact = cleaned == alias
            confidence = min(1.0, base_confidence + (0.1 if exact else 0.0))
            results.append(ParsedIngredient(
                key=key, display_name=row.display_name, family=row.family,
                matched_text=alias, position=position, confidence=confidence, source=source,
            ))
    return results


def parse_declared(values: Sequence[str] | None, *, source: str = SOURCE_USER) -> list[ParsedIngredient]:
    """Parse the short list a user typed. Higher confidence, no position."""
    if not values:
        return []
    base_confidence = _SOURCE_CONFIDENCE.get(source, 0.9)
    results: list[ParsedIngredient] = []
    seen: set = set()
    for value in values:
        cleaned = _clean(value)
        if not cleaned:
            continue
        matches = _match_in(cleaned)
        if not matches:
            continue
        for key, alias in matches:
            if key in seen:
                continue
            seen.add(key)
            row = INGREDIENT_BY_KEY[key]
            results.append(ParsedIngredient(
                key=key, display_name=row.display_name, family=row.family,
                matched_text=alias, position=None, confidence=base_confidence, source=source,
            ))
    return results


def parse_product(details: dict[str, Any], *, source: str = SOURCE_USER) -> list[ParsedIngredient]:
    """Everything we can read from one inventory item's recorded details.

    The user's own ``active_ingredients`` list wins over the same ingredient
    spotted in the label text, because a person chose it deliberately.
    """
    declared = parse_declared(details.get("active_ingredients"), source=source)
    label = parse_label(details.get("ingredients_text"), source=SOURCE_LABEL)
    by_key: dict[str, ParsedIngredient] = {row.key: row for row in label}
    for row in declared:
        by_key[row.key] = row
    return sorted(by_key.values(), key=lambda row: (-row.confidence, row.display_name))


def families_of(rows: Iterable[ParsedIngredient], *, confirmed_only: bool = False) -> set:
    return {
        row.family for row in rows
        if not confirmed_only or not row.needs_confirmation
    }


def unmatched_terms(text: str | None, limit: int = 10) -> list[str]:
    """Label entries we could not identify.

    Surfaced rather than hidden: "we did not recognise these" is honest, and it
    is how the ontology finds out what it is missing.
    """
    if not text:
        return []
    out: list[str] = []
    for chunk in _SPLIT.split(str(text)):
        cleaned = _clean(chunk)
        if not cleaned or len(cleaned) < 3:
            continue
        if _match_in(cleaned):
            continue
        out.append(cleaned[:60])
        if len(out) >= limit:
            break
    return out
