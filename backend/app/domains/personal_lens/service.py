"""Read-only projection of trusted Profile facts for future FOR YOU work.

Step 8A builds only the personal side of a future evidence join. It has no
product input, evidence input, score, verdict, recommendation, persistence, AI,
network access, or entitlement behavior.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.routines.hard_handoff as hard_handoff
from app.domains.personal_lens.enums import (
    PersonalFactKind,
    PersonalFactMissingReason,
    PersonalLensCategory,
    PersonalLensStatus,
)
from app.domains.profile import service as profile_service

SKIN_BODY_FACT_KEYS = (
    "care_skin_usual_feel",
    "care_skin_sensitivity",
)

HAIR_BODY_FACT_KEYS = (
    "care_hair_pattern",
    "care_hair_strand_characteristic",
    "care_hair_density",
    "care_hair_wash_frequency",
    "care_hair_processing",
    "care_heat_styling_frequency",
    "care_scalp_usual_feel",
    "care_humidity_frizz_sensitivity",
)

PREFERENCE_FACT_KEYS = (
    "care_fragrance_preference",
    "care_routine_effort",
)

BODY_FACT_KEYS_BY_CATEGORY: dict[PersonalLensCategory, tuple[str, ...]] = {
    PersonalLensCategory.PACKAGED_FOOD: (),
    PersonalLensCategory.SKIN_CARE: SKIN_BODY_FACT_KEYS,
    PersonalLensCategory.HAIR_CARE: HAIR_BODY_FACT_KEYS,
    PersonalLensCategory.COSMETICS: SKIN_BODY_FACT_KEYS,
}

PREFERENCE_FACT_KEYS_BY_CATEGORY: dict[PersonalLensCategory, tuple[str, ...]] = {
    PersonalLensCategory.PACKAGED_FOOD: (),
    PersonalLensCategory.SKIN_CARE: PREFERENCE_FACT_KEYS,
    PersonalLensCategory.HAIR_CARE: PREFERENCE_FACT_KEYS,
    PersonalLensCategory.COSMETICS: PREFERENCE_FACT_KEYS,
}


@dataclass(frozen=True, slots=True)
class PersonalLensSafetyInput:
    """Ephemeral context used only by the existing hard-handoff authority."""

    text: str | None = None
    stated_age: int | None = None
    subject_is_child: bool = False


@dataclass(frozen=True, slots=True)
class PersonalLensFact:
    key: str
    value: object
    source: str
    verification_state: str
    profile_attribute_id: uuid.UUID
    explicit_unknown: bool
    last_reviewed_at: datetime | None


@dataclass(frozen=True, slots=True)
class MissingPersonalLensFact:
    key: str
    kind: PersonalFactKind
    reason: PersonalFactMissingReason


@dataclass(frozen=True, slots=True)
class PersonalLensHandoff:
    """Safe projection of the existing decision; never carries input text."""

    reason: str
    message: str


@dataclass(frozen=True, slots=True)
class PersonalLensContext:
    category: PersonalLensCategory
    status: PersonalLensStatus
    profile_id: uuid.UUID | None
    profile_version: int | None
    body_facts: tuple[PersonalLensFact, ...]
    preference_facts: tuple[PersonalLensFact, ...]
    missing_information: tuple[MissingPersonalLensFact, ...]
    handoff: PersonalLensHandoff | None


def _freeze(value: Any) -> object:
    """Recursively prevent callers from mutating projected JSON values."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def _is_explicit_unknown(value: Any) -> bool:
    return value == "not_sure" or value in (["not_sure"], ("not_sure",))


def _missing_reason(row: Any | None) -> PersonalFactMissingReason | None:
    if row is None:
        return PersonalFactMissingReason.MISSING
    if row.source != "user_declared":
        return PersonalFactMissingReason.UNTRUSTED_SOURCE
    if row.verification_state != "confirmed":
        return PersonalFactMissingReason.NOT_CONFIRMED
    if _is_explicit_unknown(row.value):
        return PersonalFactMissingReason.EXPLICIT_UNKNOWN
    return None


def _fact(row: Any) -> PersonalLensFact:
    value = _freeze(row.value)
    return PersonalLensFact(
        key=row.key,
        value=value,
        source=row.source,
        verification_state=row.verification_state,
        profile_attribute_id=row.id,
        explicit_unknown=_is_explicit_unknown(value),
        last_reviewed_at=row.last_reviewed_at,
    )


def _project_facts(
    rows_by_key: dict[str, Any],
    keys: tuple[str, ...],
    *,
    kind: PersonalFactKind,
) -> tuple[tuple[PersonalLensFact, ...], tuple[MissingPersonalLensFact, ...]]:
    facts: list[PersonalLensFact] = []
    missing: list[MissingPersonalLensFact] = []
    for key in keys:
        row = rows_by_key.get(key)
        reason = _missing_reason(row)
        if reason is None:
            facts.append(_fact(row))
            continue
        if reason is PersonalFactMissingReason.EXPLICIT_UNKNOWN:
            facts.append(_fact(row))
        missing.append(MissingPersonalLensFact(key=key, kind=kind, reason=reason))
    return tuple(facts), tuple(missing)


async def build_personal_lens_context(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    category: PersonalLensCategory,
    safety: PersonalLensSafetyInput | None = None,
) -> PersonalLensContext:
    """Build live trusted context, stopping before all reads on hard handoff."""
    if not isinstance(category, PersonalLensCategory):
        raise ValueError("category must be a PersonalLensCategory")
    if safety is not None and not isinstance(safety, PersonalLensSafetyInput):
        raise ValueError("safety must be a PersonalLensSafetyInput")

    safety_context = safety or PersonalLensSafetyInput()
    decision = hard_handoff.evaluate(
        safety_context.text,
        stated_age=safety_context.stated_age,
        subject_is_child=safety_context.subject_is_child,
    )
    if decision.handoff:
        assert decision.reason is not None
        return PersonalLensContext(
            category=category,
            status=PersonalLensStatus.HANDOFF_REQUIRED,
            profile_id=None,
            profile_version=None,
            body_facts=(),
            preference_facts=(),
            missing_information=(),
            handoff=PersonalLensHandoff(
                reason=decision.reason.value,
                message=decision.message,
            ),
        )

    profile = await profile_service.get_profile(session, account_id)
    rows = await profile_service.attributes_for(session, profile.id) if profile is not None else []
    rows_by_key = {row.key: row for row in rows}

    body_keys = BODY_FACT_KEYS_BY_CATEGORY[category]
    preference_keys = PREFERENCE_FACT_KEYS_BY_CATEGORY[category]
    body_facts, body_missing = _project_facts(
        rows_by_key,
        body_keys,
        kind=PersonalFactKind.BODY,
    )
    preference_facts, preference_missing = _project_facts(
        rows_by_key,
        preference_keys,
        kind=PersonalFactKind.PREFERENCE,
    )

    usable_body_fact_count = sum(not fact.explicit_unknown for fact in body_facts)
    if usable_body_fact_count == 0:
        status = PersonalLensStatus.NOT_ENOUGH_PERSONAL_CONTEXT
    elif not body_missing:
        status = PersonalLensStatus.CONTEXT_AVAILABLE
    else:
        status = PersonalLensStatus.PARTIAL_CONTEXT

    return PersonalLensContext(
        category=category,
        status=status,
        profile_id=profile.id if profile is not None else None,
        profile_version=profile.version if profile is not None else None,
        body_facts=body_facts,
        preference_facts=preference_facts,
        missing_information=body_missing + preference_missing,
        handoff=None,
    )


__all__ = [
    "BODY_FACT_KEYS_BY_CATEGORY",
    "HAIR_BODY_FACT_KEYS",
    "PREFERENCE_FACT_KEYS",
    "PREFERENCE_FACT_KEYS_BY_CATEGORY",
    "SKIN_BODY_FACT_KEYS",
    "MissingPersonalLensFact",
    "PersonalLensContext",
    "PersonalLensFact",
    "PersonalLensHandoff",
    "PersonalLensSafetyInput",
    "build_personal_lens_context",
]
