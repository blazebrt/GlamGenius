"""Validated contracts for the Today engine and the weekly planner.

``extra="forbid"`` throughout, so an injected ``account_id`` is a 422 rather
than something that might quietly be trusted. Identity always comes from the
token.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.planning.models import LAUNDRY_STATES, MODULES
from app.domains.recommendation.occasions import DRESS_CODES, get_occasion

WEATHER_CONDITIONS = ("hot", "warm", "mild", "cool", "cold", "humid", "rainy", "windy")


# --- Weather ----------------------------------------------------------------


class WeatherInput(BaseModel):
    """What the user says the weather will do. Better than a forecast for style."""

    model_config = ConfigDict(extra="forbid")

    for_date: date
    condition: Literal[WEATHER_CONDITIONS]  # type: ignore[valid-type]
    temp_min_c: float | None = Field(default=None, ge=-40, le=60)
    temp_max_c: float | None = Field(default=None, ge=-40, le=60)
    precipitation_chance: int | None = Field(default=None, ge=0, le=100)
    humidity: int | None = Field(default=None, ge=0, le=100)
    uv_index: float | None = Field(default=None, ge=0, le=20)
    location: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def _sane_range(self):
        if self.temp_min_c is not None and self.temp_max_c is not None and self.temp_min_c > self.temp_max_c:
            raise ValueError("The lowest temperature cannot be higher than the highest.")
        return self


# --- Air Quality ------------------------------------------------------------


class AirQualityInput(BaseModel):
    """What the user says the air quality will do."""

    model_config = ConfigDict(extra="forbid")

    for_date: date
    aqi: int = Field(ge=0, le=2000)
    index_system: Literal["india_naqi", "unknown"]
    location: str | None = Field(default=None, max_length=160)
    prominent_pollutant: str | None = Field(default=None, max_length=32)
    pm2_5: float | None = Field(default=None, ge=0)
    pm10: float | None = Field(default=None, ge=0)



# --- Calendar ---------------------------------------------------------------


class CalendarEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    location: str | None = Field(default=None, max_length=200)
    occasion_key: str | None = Field(default=None, max_length=32)
    dress_code_hint: str | None = Field(default=None, max_length=32)
    external_id: str | None = Field(default=None, max_length=200)

    @field_validator("occasion_key")
    @classmethod
    def _known_occasion(cls, value: str | None) -> str | None:
        if value:
            get_occasion(value)
        return value

    @field_validator("dress_code_hint")
    @classmethod
    def _known_dress_code(cls, value: str | None) -> str | None:
        if value and value not in DRESS_CODES:
            raise ValueError(f"'{value}' is not a dress code we support. Choose one of: {', '.join(DRESS_CODES)}.")
        return value

    @model_validator(mode="after")
    def _ends_after_start(self):
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("An event cannot end before it starts.")
        return self


class CalendarEventPatch(BaseModel):
    """Correcting what we guessed about an event."""

    model_config = ConfigDict(extra="forbid")

    occasion_key: str | None = Field(default=None, max_length=32)
    dress_code_hint: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, min_length=1, max_length=240)
    status: Literal["active", "dismissed"] | None = None

    @field_validator("occasion_key")
    @classmethod
    def _known_occasion(cls, value: str | None) -> str | None:
        if value:
            get_occasion(value)
        return value


class CalendarConnect(BaseModel):
    """Connect a calendar source.

    No token is accepted here. ``credential_ref`` is an opaque handle to a
    secret held elsewhere — this API never receives, stores or returns a
    credential.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="manual", max_length=32)
    credential_ref: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=160)
    events: list[CalendarEventInput] = Field(default_factory=list, max_length=200)


# --- Today ------------------------------------------------------------------


class TodayRegenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_date: date | None = None
    reason: Literal["weather_changed", "plans_changed", "not_my_style", "item_unavailable", "manual"] | None = None
    force: bool = False


class ActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: bool = True


class TodayOutfitSwap(BaseModel):
    """Swap one piece without regenerating the whole day."""

    model_config = ConfigDict(extra="forbid")

    plan_date: date | None = None
    slot: Literal["clothing", "shoes", "accessories", "perfume", "hair", "grooming"]
    from_item_id: uuid.UUID | None = None
    to_item_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=400)


class TodayFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_date: date | None = None
    rating: Literal["wore_it", "loved", "not_for_me", "changed_it"]
    reason: Literal["too_formal", "too_casual", "wrong_weather", "not_my_style", "uncomfortable", "great_fit", "other"] | None = None
    note: str | None = Field(default=None, max_length=500)


class ClarificationAnswer(BaseModel):
    """The answer to the single question the plan asked."""

    model_config = ConfigDict(extra="forbid")

    plan_date: date | None = None
    question_key: str = Field(min_length=1, max_length=48)
    answer: str = Field(min_length=1, max_length=64)


class ItemUnavailable(BaseModel):
    """Report that something cannot be worn right now."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    state: Literal[LAUNDRY_STATES]  # type: ignore[valid-type]
    available_from: date | None = None
    note: str | None = Field(default=None, max_length=240)


# --- Planner ----------------------------------------------------------------


class WeekGenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None
    repetition_window_days: int = Field(default=7, ge=0, le=30)
    regenerate_locked: bool = False


class DayPatch(BaseModel):
    """Edit one day: swap in a look, move a day, or add a note."""

    model_config = ConfigDict(extra="forbid")

    look_id: uuid.UUID | None = None
    swap_with_date: date | None = None
    note: str | None = Field(default=None, max_length=240)
    regenerate: bool = False


class DayLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locked: bool = True


class EventReadyLookPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    look_id: uuid.UUID | None


class EventReadyActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: bool


# --- Notifications ----------------------------------------------------------


class NotificationPreferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    native_push_enabled: bool | None = None
    daily_cap: int | None = Field(default=None, ge=0, le=5)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    preferred_hour: int | None = Field(default=None, ge=0, le=23)
    modules: dict[str, bool] | None = None

    @field_validator("modules")
    @classmethod
    def _known_modules(cls, value: dict[str, bool] | None) -> dict[str, bool] | None:
        if value:
            unknown = set(value) - set(MODULES)
            if unknown:
                raise ValueError(f"Unknown module: {sorted(unknown)[0]}. Choose from: {', '.join(MODULES)}.")
        return value


class NotificationDeviceRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_key: str = Field(min_length=1, max_length=160)
    platform: Literal["ios", "android", "web", "unknown"] = "unknown"
    expo_push_token: str = Field(min_length=1, max_length=512)
