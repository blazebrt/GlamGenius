"""Strict V1 structured payload for personal body-fact applicability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domains.personal_applicability.enums import (
    PersonalApplicabilityCategory,
    PersonalApplicabilityOperator,
)
from app.domains.personal_lens.service import BODY_FACT_KEYS_BY_CATEGORY
from app.domains.profile.registry import ATTRIBUTE_REGISTRY

PERSONAL_APPLICABILITY_SCHEMA_VERSION = "1"
PERSONAL_APPLICABILITY_PAYLOAD_KEY = "substance_personal_applicability"
MAX_PERSONAL_APPLICABILITY_CONDITIONS = 4

_BLOCK_KEYS = frozenset({"schema_version", "category", "all_of"})
_CONDITION_KEYS = frozenset({"fact_key", "operator", "values"})


@dataclass(frozen=True, slots=True)
class PersonalApplicabilityCondition:
    fact_key: str
    operator: PersonalApplicabilityOperator
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersonalApplicabilityPayload:
    schema_version: str
    category: PersonalApplicabilityCategory
    all_of: tuple[PersonalApplicabilityCondition, ...]


def _body_keys(category: PersonalApplicabilityCategory) -> tuple[str, ...]:
    # The category values are deliberately identical, but the conversion stays
    # explicit so neither domain silently becomes the other's authority.
    from app.domains.personal_lens.enums import PersonalLensCategory

    return BODY_FACT_KEYS_BY_CATEGORY[PersonalLensCategory(category.value)]


def _condition(
    value: Any,
    *,
    allowed_fact_keys: tuple[str, ...],
) -> PersonalApplicabilityCondition | None:
    if not isinstance(value, Mapping) or set(value) != _CONDITION_KEYS:
        return None

    fact_key = value.get("fact_key")
    if not isinstance(fact_key, str) or fact_key not in allowed_fact_keys:
        return None
    spec = ATTRIBUTE_REGISTRY.get(fact_key)
    if spec is None or not spec.choices:
        return None

    try:
        operator = PersonalApplicabilityOperator(value.get("operator"))
    except (TypeError, ValueError):
        return None
    expected_operator = (
        PersonalApplicabilityOperator.CONTAINS_ANY
        if spec.kind == "list"
        else PersonalApplicabilityOperator.EQUALS_ANY
    )
    if operator is not expected_operator:
        return None

    raw_values = value.get("values")
    if not isinstance(raw_values, (list, tuple)) or not raw_values:
        return None
    if any(not isinstance(item, str) or not item for item in raw_values):
        return None
    values = tuple(raw_values)
    if len(set(values)) != len(values) or "not_sure" in values:
        return None
    if any(item not in spec.choices for item in values):
        return None

    return PersonalApplicabilityCondition(
        fact_key=fact_key,
        operator=operator,
        values=values,
    )


def parse_personal_applicability_payload(value: Any) -> PersonalApplicabilityPayload | None:
    """Return a complete exact V1 payload or ``None``; never coerce or infer."""
    if not isinstance(value, Mapping):
        return None
    block = value.get(PERSONAL_APPLICABILITY_PAYLOAD_KEY)
    if not isinstance(block, Mapping) or set(block) != _BLOCK_KEYS:
        return None
    if block.get("schema_version") != PERSONAL_APPLICABILITY_SCHEMA_VERSION:
        return None
    try:
        category = PersonalApplicabilityCategory(block.get("category"))
    except (TypeError, ValueError):
        return None

    all_of = block.get("all_of")
    if (
        not isinstance(all_of, list)
        or not all_of
        or len(all_of) > MAX_PERSONAL_APPLICABILITY_CONDITIONS
    ):
        return None

    allowed_fact_keys = _body_keys(category)
    conditions: list[PersonalApplicabilityCondition] = []
    seen_fact_keys: set[str] = set()
    for raw_condition in all_of:
        condition = _condition(raw_condition, allowed_fact_keys=allowed_fact_keys)
        if condition is None or condition.fact_key in seen_fact_keys:
            return None
        seen_fact_keys.add(condition.fact_key)
        conditions.append(condition)

    return PersonalApplicabilityPayload(
        schema_version=PERSONAL_APPLICABILITY_SCHEMA_VERSION,
        category=category,
        all_of=tuple(conditions),
    )
