"""Provider abstractions for the two things Phase 5 reads from outside.

The planner never talks to a weather service or a calendar service directly. It
asks a provider, and a provider is a small object with one job. That matters for
three reasons:

* **The engine stays testable.** Tests inject a fake provider and assert on
  deterministic output, instead of mocking HTTP.
* **The user stays in control.** The manual providers are not a fallback or a
  placeholder — they are a first-class way to use the product without connecting
  any outside account at all.
* **Nothing is invented.** A provider that is not configured says
  :class:`ProviderUnavailable` and the plan is built without it, with the gap
  named. It never returns a guessed forecast.

The Google Calendar adapter is additive and read-only in VC-05. Manual events
remain first-class and provider-specific HTTP never appears in Today/Event
Ready code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

# --- Value objects ----------------------------------------------------------
# Deliberately plain. A provider's job is to return one of these, whatever shape
# its own API happens to use.


@dataclass
class WeatherReading:
    """What the weather is expected to do on one local day."""

    for_date: date
    condition: str            # one of compatibility.WEATHER_CONDITIONS
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    precipitation_chance: int | None = None   # 0-100
    humidity: int | None = None               # 0-100
    uv_index: float | None = None
    location: str | None = None
    provider: str = "manual"
    source: str = "user_declared"
    raw: dict[str, Any] = field(default_factory=dict)
    attribution: str | None = None
    is_stale: bool = False


@dataclass
class AirQualityReading:
    """Air quality index and categorization for one local day."""

    for_date: date
    aqi: int
    index_system: str
    category: str | None = None
    location: str | None = None
    prominent_pollutant: str | None = None
    pm2_5: float | None = None
    pm10: float | None = None
    provider: str = "manual"
    source: str = "user_declared"
    raw: dict[str, Any] = field(default_factory=dict)
    attribution: str | None = None
    is_stale: bool = False


@dataclass
class CalendarEventReading:
    """One commitment on the user's calendar."""

    external_id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None = None
    all_day: bool = False
    location: str | None = None
    provider: str = "manual"
    source: str = "user_declared"
    status: str = "active"
    raw: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        """What makes two events the same event.

        Calendars re-send the same event with a new sync token constantly, and a
        user can also type in something they already have. Keying on the
        external id plus the start instant catches both without collapsing a
        genuinely recurring weekly meeting into one row.
        """
        return f"{self.provider}:{self.external_id}:{self.starts_at.isoformat()}"


class ProviderUnavailable(Exception):
    """This provider cannot answer right now.

    Raised rather than returning an empty or invented result, so the compiler
    can tell "no weather data" apart from "clear skies" — they lead to very
    different plans.
    """

    def __init__(self, message: str, *, provider: str, reason: str = "not_configured") -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.reason = reason


# --- Protocols --------------------------------------------------------------


class WeatherProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    async def forecast(
        self, *, location: str | None, dates: list[date], timezone_name: str
    ) -> list[WeatherReading]:
        """Readings for the requested local dates. May return fewer than asked."""
        ...


class AirQualityProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    async def air_quality(
        self, *, location: str | None, dates: list[date], timezone_name: str
    ) -> list[AirQualityReading]:
        """Readings for the requested local dates. May return fewer than asked."""
        ...


class CalendarProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    async def events(
        self, *, since: datetime, until: datetime, credential_ref: str | None
    ) -> list[CalendarEventReading]:
        ...
