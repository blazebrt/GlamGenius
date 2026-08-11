"""Immutable in-memory contracts for the V3-03.1 Care foundation."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from app.domains.routines.rules import ShelfProduct

CARE_CONTEXT_VERSION = "v3-03.11"


@dataclass(frozen=True, slots=True)
class CareFact:
    key: str
    value: Any
    fact_source: str
    record_source: str | None
    confidence: float | None
    verification_state: str | None
    profile_attribute_id: uuid.UUID | None
    explicit_unknown: bool


@dataclass(frozen=True, slots=True)
class MissingCareFact:
    area: str
    key: str
    reason: str


@dataclass(frozen=True, slots=True)
class CareEnvironment:
    weather_snapshot_id: uuid.UUID | None
    air_quality_snapshot_id: uuid.UUID | None
    condition: str | None
    temp_min_c: float | None
    temp_max_c: float | None
    humidity: int | None
    precipitation_chance: int | None
    uv_index: float | None
    aqi: int | None
    aqi_index_system: str | None
    aqi_category: str | None
    climate_region: str | None
    calendar_prior: str | None
    season: str | None
    temperature_band: str | None
    moisture_regime: str | None
    daily_regime: str | None
    climate_confidence: float | None
    climate_reason: str | None
    weather_unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CareEvent:
    id: uuid.UUID | None
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    occasion_key: str | None
    confidence: float
    user_confirmed: bool


@dataclass(frozen=True, slots=True)
class CareContext:
    context_version: str
    account_id: uuid.UUID
    plan_date: date
    skin_facts: Mapping[str, CareFact]
    hair_facts: Mapping[str, CareFact]
    preferences: Mapping[str, CareFact]
    environment: CareEnvironment
    primary_event: CareEvent | None
    allergies: tuple[str, ...]
    skin_products: tuple[ShelfProduct, ...]
    hair_products: tuple[ShelfProduct, ...]
    draft_product_count: int
    missing_information: tuple[MissingCareFact, ...]
    paused_product_ids: frozenset[uuid.UUID] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "skin_facts", MappingProxyType(dict(self.skin_facts)))
        object.__setattr__(self, "hair_facts", MappingProxyType(dict(self.hair_facts)))
        object.__setattr__(self, "preferences", MappingProxyType(dict(self.preferences)))
        object.__setattr__(self, "allergies", tuple(self.allergies))
        object.__setattr__(self, "skin_products", tuple(self.skin_products))
        object.__setattr__(self, "hair_products", tuple(self.hair_products))
        object.__setattr__(self, "missing_information", tuple(self.missing_information))
        object.__setattr__(self, "paused_product_ids", frozenset(self.paused_product_ids))


__all__ = [
    "CARE_CONTEXT_VERSION",
    "CareContext",
    "CareEnvironment",
    "CareEvent",
    "CareFact",
    "MissingCareFact",
]
