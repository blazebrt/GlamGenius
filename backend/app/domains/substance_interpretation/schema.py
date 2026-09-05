"""Strict structured payload for one category-specific reference-role claim."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.substance_interpretation.enums import InterpretationCategory

INTERPRETATION_SCHEMA_VERSION = "1"
INTERPRETATION_PAYLOAD_KEY = "substance_category_interpretation"
REFERENCE_ROLE_KIND = "reference_role"
_PAYLOAD_KEYS = frozenset({"schema_version", "category", "kind"})


@dataclass(frozen=True)
class SubstanceInterpretationPayload:
    schema_version: str
    category: InterpretationCategory
    kind: str


def parse_interpretation_payload(value: Any) -> SubstanceInterpretationPayload | None:
    """Return a complete V1 payload or ``None``; never coerce or guess."""
    if not isinstance(value, dict):
        return None
    payload = value.get(INTERPRETATION_PAYLOAD_KEY)
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        return None
    if payload.get("schema_version") != INTERPRETATION_SCHEMA_VERSION:
        return None
    if payload.get("kind") != REFERENCE_ROLE_KIND:
        return None
    try:
        category = InterpretationCategory(payload.get("category"))
    except (TypeError, ValueError):
        return None
    return SubstanceInterpretationPayload(
        schema_version=INTERPRETATION_SCHEMA_VERSION,
        category=category,
        kind=REFERENCE_ROLE_KIND,
    )
