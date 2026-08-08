"""Provider registry.

One place that knows which providers exist and which are actually usable, so a
route can report connection status honestly without importing adapters.
"""
from __future__ import annotations

from app.domains.planning.providers.base import (
    CalendarEventReading,
    CalendarProvider,
    ProviderUnavailable,
    WeatherProvider,
    WeatherReading,
    AirQualityProvider,
    AirQualityReading,
)
from app.domains.planning.providers.manual import (
    PROVIDER_MANUAL,
    StoredCalendarProvider,
    StoredWeatherProvider,
    StoredAirQualityProvider,
    UnconfiguredProvider,
)

# Names a user may pass. `manual` is the one that works today; the others are
# declared so the API can say "known, not connected" rather than "unknown", and
# so adding a real adapter is a one-line registry change.
KNOWN_CALENDAR_PROVIDERS: dict[str, str] = {
    PROVIDER_MANUAL: "Events you add yourself",
    "google": "Google Calendar (not connected in this release)",
    "apple": "Apple Calendar (not connected in this release)",
    "outlook": "Outlook Calendar (not connected in this release)",
}

KNOWN_WEATHER_PROVIDERS: dict[str, str] = {
    PROVIDER_MANUAL: "Weather you enter yourself",
}


def calendar_provider(name: str, session, account_id) -> CalendarProvider:
    if name == PROVIDER_MANUAL:
        return StoredCalendarProvider(session, account_id)
    return UnconfiguredProvider(name, "calendar")


def weather_provider(name: str, session, account_id) -> WeatherProvider:
    if name == PROVIDER_MANUAL:
        return StoredWeatherProvider(session, account_id)
    return UnconfiguredProvider(name, "weather")


def air_quality_provider(name: str, session, account_id) -> AirQualityProvider:
    if name == PROVIDER_MANUAL:
        return StoredAirQualityProvider(session, account_id)
    return UnconfiguredProvider(name, "air_quality")


def catalogue() -> dict[str, list[dict[str, object]]]:
    return {
        "calendar": [
            {"key": key, "label": label, "available": key == PROVIDER_MANUAL}
            for key, label in KNOWN_CALENDAR_PROVIDERS.items()
        ],
        "weather": [
            {"key": key, "label": label, "available": key == PROVIDER_MANUAL}
            for key, label in KNOWN_WEATHER_PROVIDERS.items()
        ],
    }


__all__ = [
    "CalendarEventReading", "CalendarProvider", "ProviderUnavailable",
    "WeatherProvider", "WeatherReading", "PROVIDER_MANUAL",
    "KNOWN_CALENDAR_PROVIDERS", "KNOWN_WEATHER_PROVIDERS",
    "calendar_provider", "weather_provider", "catalogue",
    "AirQualityProvider", "AirQualityReading", "air_quality_provider",
]
