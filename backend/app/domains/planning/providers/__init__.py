"""Provider registry.

One place that knows which providers exist and which are actually usable, so a
route can report connection status honestly without importing adapters.
"""
from __future__ import annotations

from app.config import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_CALENDAR_CREDENTIAL_STORE,
    GOOGLE_CALENDAR_ENABLED,
    LIVE_ENVIRONMENT_PROVIDER,
    OPEN_METEO_MODE,
)
from app.domains.planning.providers.base import (
    AirQualityProvider,
    AirQualityReading,
    CalendarEventReading,
    CalendarProvider,
    ProviderUnavailable,
    WeatherProvider,
    WeatherReading,
)
from app.domains.planning.providers.google_calendar import GoogleCalendarProvider
from app.domains.planning.providers.manual import (
    PROVIDER_MANUAL,
    StoredAirQualityProvider,
    StoredCalendarProvider,
    StoredWeatherProvider,
    UnconfiguredProvider,
)
from app.domains.planning.providers.open_meteo import OpenMeteoProvider

# Names a user may pass. `manual` is the one that works today; the others are
# declared so the API can say "known, not connected" rather than "unknown", and
# so adding a real adapter is a one-line registry change.
KNOWN_CALENDAR_PROVIDERS: dict[str, str] = {
    PROVIDER_MANUAL: "Events you add yourself",
    "google": "Google Calendar · read-only primary calendar",
    "apple": "Apple Calendar · unavailable",
    "outlook": "Outlook Calendar · unavailable",
}

KNOWN_WEATHER_PROVIDERS: dict[str, str] = {
    PROVIDER_MANUAL: "Weather you enter yourself",
    "open_meteo": "Live weather · Open-Meteo",
}

KNOWN_AIR_QUALITY_PROVIDERS: dict[str, str] = {
    PROVIDER_MANUAL: "Air quality you enter yourself",
    "open_meteo": "Live air quality · Open-Meteo / CAMS",
}


def calendar_provider(name: str, session, account_id) -> CalendarProvider:
    if name == PROVIDER_MANUAL:
        return StoredCalendarProvider(session, account_id)
    if name == "google" and GOOGLE_CALENDAR_ENABLED and GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET and GOOGLE_CALENDAR_CREDENTIAL_STORE == "supabase_vault":
        from app.domains.planning.credentials import credential_store
        return GoogleCalendarProvider(credential_store(session))
    return UnconfiguredProvider(name, "calendar")


def weather_provider(name: str, session, account_id) -> WeatherProvider:
    if name == PROVIDER_MANUAL:
        return StoredWeatherProvider(session, account_id)
    if name == "open_meteo":
        return OpenMeteoProvider()
    return UnconfiguredProvider(name, "weather")


def air_quality_provider(name: str, session, account_id) -> AirQualityProvider:
    if name == PROVIDER_MANUAL:
        return StoredAirQualityProvider(session, account_id)
    if name == "open_meteo":
        return OpenMeteoProvider()
    return UnconfiguredProvider(name, "air_quality")


def catalogue() -> dict[str, list[dict[str, object]]]:
    return {
        "calendar": [
            {"key": key, "label": label, "available": key == PROVIDER_MANUAL or (key == "google" and GOOGLE_CALENDAR_ENABLED and bool(GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET and GOOGLE_CALENDAR_CREDENTIAL_STORE == "supabase_vault"))}
            for key, label in KNOWN_CALENDAR_PROVIDERS.items()
        ],
        "weather": [
            {"key": key, "label": label, "available": key == PROVIDER_MANUAL or (key == "open_meteo" and LIVE_ENVIRONMENT_PROVIDER == "open_meteo" and OPEN_METEO_MODE in {"evaluation", "commercial"})}
            for key, label in KNOWN_WEATHER_PROVIDERS.items()
        ],
        "air_quality": [
            {
                "key": key,
                "label": label,
                "available": key == PROVIDER_MANUAL
                or (
                    key == "open_meteo"
                    and LIVE_ENVIRONMENT_PROVIDER == "open_meteo"
                    and OPEN_METEO_MODE in {"evaluation", "commercial"}
                ),
            }
            for key, label in KNOWN_AIR_QUALITY_PROVIDERS.items()
        ],
    }


__all__ = [
    "AirQualityProvider",
    "AirQualityReading",
    "CalendarEventReading",
    "CalendarProvider",
    "KNOWN_AIR_QUALITY_PROVIDERS",
    "KNOWN_CALENDAR_PROVIDERS",
    "KNOWN_WEATHER_PROVIDERS",
    "OpenMeteoProvider",
    "GoogleCalendarProvider",
    "PROVIDER_MANUAL",
    "ProviderUnavailable",
    "WeatherProvider",
    "WeatherReading",
    "air_quality_provider",
    "calendar_provider",
    "catalogue",
    "weather_provider",
]
