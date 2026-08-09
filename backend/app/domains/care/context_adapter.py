"""Pure projections from Planning's already assembled ``DayContext``."""
from __future__ import annotations

from app.domains.care.schemas import CareEnvironment, CareEvent
from app.domains.planning.context import DayContext, DayEvent


def _event_projection(event: DayEvent | None) -> CareEvent | None:
    if event is None:
        return None
    return CareEvent(
        id=event.id,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        all_day=event.all_day,
        occasion_key=event.occasion_key,
        confidence=event.confidence,
        user_confirmed=event.user_confirmed,
    )


def project_environment(day_context: DayContext) -> CareEnvironment:
    """Project only values already present on ``DayContext``.

    This function deliberately has no session argument and no provider seam.
    Climate values come from Planning's normalized ``ClimateContext``; weather
    and AQI values are copied only when their readings exist.
    """
    weather = day_context.weather
    air_quality = day_context.air_quality
    climate = day_context.climate
    return CareEnvironment(
        weather_snapshot_id=day_context.weather_snapshot_id,
        air_quality_snapshot_id=day_context.air_quality_snapshot_id,
        condition=weather.condition if weather else None,
        temp_min_c=weather.temp_min_c if weather else None,
        temp_max_c=weather.temp_max_c if weather else None,
        humidity=weather.humidity if weather else None,
        precipitation_chance=weather.precipitation_chance if weather else None,
        uv_index=weather.uv_index if weather else None,
        aqi=air_quality.aqi if air_quality else None,
        aqi_index_system=air_quality.index_system if air_quality else None,
        aqi_category=air_quality.category if air_quality else None,
        climate_region=str(climate.climate_region) if climate.climate_region else None,
        calendar_prior=climate.calendar_prior,
        season=climate.season,
        temperature_band=climate.temperature_band,
        moisture_regime=climate.moisture_regime,
        daily_regime=climate.daily_regime,
        climate_confidence=climate.confidence,
        climate_reason=climate.reason,
        weather_unavailable_reason=day_context.weather_unavailable_reason,
    )


def project_primary_event(day_context: DayContext) -> CareEvent | None:
    """Project Planning's selected primary event without creating persistence."""
    return _event_projection(day_context.primary_event)


__all__ = ["project_environment", "project_primary_event"]
