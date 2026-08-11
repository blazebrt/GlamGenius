"""Account-scoped Care fact assembly for V3-03.1."""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.context_adapter import project_environment, project_primary_event
from app.domains.care.product_preferences import (
    CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
    is_effective_user_pause,
)
from app.domains.care.reasons import CareFactSource, CareMissingReason
from app.domains.care.schemas import (
    CARE_CONTEXT_VERSION,
    CareContext,
    CareFact,
    MissingCareFact,
)
from app.domains.inventory.models import InventoryAttribute
from app.domains.planning.context import DayContext
from app.domains.profile import service as profile_service
from app.domains.profile.models import ProfileAttribute
from app.domains.routines import shelf

SKIN_KEYS = (
    "care_skin_usual_feel",
    "care_skin_sensitivity",
)
HAIR_KEYS = (
    "care_hair_pattern",
    "care_hair_strand_characteristic",
    "care_hair_density",
    "care_hair_wash_frequency",
    "care_hair_processing",
    "care_heat_styling_frequency",
    "care_scalp_usual_feel",
    "care_humidity_frizz_sensitivity",
    "care_hair_styling_preference",
)
PREFERENCE_KEYS = (
    "care_routine_effort",
    "care_fragrance_preference",
    "care_event_preparation_effort",
)

LEGACY_HAIR_MAP: dict[str, tuple[str, frozenset[str]]] = {
    "care_hair_pattern": ("hair_type", frozenset({"straight", "wavy", "curly", "coily"})),
    "care_hair_strand_characteristic": ("hair_texture", frozenset({"fine", "medium", "coarse"})),
    "care_hair_density": ("hair_density", frozenset({"low", "medium", "high"})),
}


def _normalised_exact(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().casefold()


def _explicit_unknown(value: Any) -> bool:
    return value == "not_sure" or value in (["not_sure"], ("not_sure",))


def _freeze_fact_value(value: Any) -> Any:
    """Keep canonical Care fact values immutable at the contract boundary."""
    if isinstance(value, list):
        return tuple(_freeze_fact_value(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_fact_value(item) for key, item in value.items()})
    if isinstance(value, set):
        return frozenset(_freeze_fact_value(item) for item in value)
    return value


def _fact(row: ProfileAttribute, *, fact_source: CareFactSource, value: Any | None = None) -> CareFact:
    resolved = _freeze_fact_value(row.value if value is None else value)
    return CareFact(
        key=row.key,
        value=resolved,
        fact_source=fact_source.value,
        record_source=row.source,
        confidence=row.confidence,
        verification_state=row.verification_state,
        profile_attribute_id=row.id,
        explicit_unknown=_explicit_unknown(resolved),
    )


def _trusted_explicit(row: ProfileAttribute | None) -> bool:
    return row is not None and row.source == "user_declared" and row.verification_state == "confirmed"


def _legacy_fact(
    key: str,
    rows: dict[str, ProfileAttribute],
) -> CareFact | None:
    mapping = LEGACY_HAIR_MAP.get(key)
    if mapping is None:
        return None
    legacy_key, allowed = mapping
    row = rows.get(legacy_key)
    if row is None or row.verification_state != "confirmed":
        return None
    value = _normalised_exact(row.value)
    if value not in allowed:
        return None
    return CareFact(
        key=key,
        value=_freeze_fact_value(value),
        fact_source=CareFactSource.LEGACY_PROFILE_CONFIRMED.value,
        record_source=row.source,
        confidence=row.confidence,
        verification_state=row.verification_state,
        profile_attribute_id=row.id,
        explicit_unknown=False,
    )


def _assemble_profile_facts(
    rows: dict[str, ProfileAttribute],
    keys: Iterable[str],
    *,
    area: str,
) -> tuple[dict[str, CareFact], list[MissingCareFact]]:
    facts: dict[str, CareFact] = {}
    missing: list[MissingCareFact] = []
    for key in keys:
        row = rows.get(key)
        if _trusted_explicit(row):
            facts[key] = _fact(row, fact_source=CareFactSource.CARE_USER_DECLARED)
            continue

        if area == "hair":
            legacy = _legacy_fact(key, rows)
            if legacy is not None:
                facts[key] = legacy
                continue

        reason = CareMissingReason.UNTRUSTED.value if row is not None else CareMissingReason.MISSING.value
        missing.append(MissingCareFact(area=area, key=key, reason=reason))
    return facts, missing


async def build_care_context(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    day_context: DayContext,
) -> CareContext:
    """Assemble trusted account facts without making Care decisions.

    The ownership check is intentionally the first operation. Every subsequent
    read is account-scoped and goes through the existing profile and shelf
    boundaries; no UserConstraint query or provider call is made here.
    """
    if day_context.account_id != account_id:
        raise ValueError("DayContext account does not match Care account")

    profile = await profile_service.get_profile(session, account_id)
    rows = (
        {row.key: row for row in await profile_service.attributes_for(session, profile.id)}
        if profile is not None
        else {}
    )
    skin_facts, missing = _assemble_profile_facts(rows, SKIN_KEYS, area="skin")
    hair_facts, hair_missing = _assemble_profile_facts(rows, HAIR_KEYS, area="hair")
    preference_facts, preference_missing = _assemble_profile_facts(rows, PREFERENCE_KEYS, area="preferences")
    missing.extend(hair_missing)
    missing.extend(preference_missing)

    shelf_context = await shelf.gather(
        session,
        account_id=account_id,
        today=day_context.plan_date,
    )
    environment = project_environment(day_context)
    primary_event = project_primary_event(day_context)
    if day_context.weather is None:
        missing.append(MissingCareFact("environment", "weather", CareMissingReason.ENVIRONMENT_MISSING.value))
    if day_context.air_quality is None:
        missing.append(MissingCareFact("environment", "air_quality", CareMissingReason.ENVIRONMENT_MISSING.value))

    skin_products = tuple(shelf.build(shelf_context, "beauty"))
    hair_products = tuple(shelf.build(shelf_context, "hair"))
    product_ids = tuple(product.item.id for product in (*skin_products, *hair_products))
    paused_product_ids: frozenset[uuid.UUID] = frozenset()
    if product_ids:
        attribute_rows = (await session.execute(
            select(InventoryAttribute).where(
                InventoryAttribute.item_id.in_(product_ids),
                InventoryAttribute.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
            )
        )).scalars().all()
        paused_product_ids = frozenset(
            row.item_id for row in attribute_rows
            if is_effective_user_pause(
                value=row.value, source=row.source, verification_state=row.verification_state,
            )
        )

    return CareContext(
        context_version=CARE_CONTEXT_VERSION,
        account_id=account_id,
        plan_date=day_context.plan_date,
        skin_facts=skin_facts,
        hair_facts=hair_facts,
        preferences=preference_facts,
        environment=environment,
        primary_event=primary_event,
        allergies=tuple(shelf_context.allergies),
        skin_products=skin_products,
        hair_products=hair_products,
        draft_product_count=shelf_context.draft_count,
        missing_information=tuple(missing),
        paused_product_ids=paused_product_ids,
    )


__all__ = [
    "HAIR_KEYS",
    "LEGACY_HAIR_MAP",
    "PREFERENCE_KEYS",
    "SKIN_KEYS",
    "build_care_context",
]
