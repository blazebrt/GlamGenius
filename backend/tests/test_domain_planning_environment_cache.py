import uuid
from datetime import UTC, date, datetime

from app.domains.planning.context import DayContext, cache_key, input_rows
from app.domains.planning.providers.base import AirQualityReading, WeatherReading

PLAN_DATE = date(2026, 4, 15)
ACCOUNT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _context(
    *,
    temp_max_c: float = 38,
    humidity: int = 25,
    uv_index: float = 8.5,
    weather_raw: dict | None = None,
    aqi: int = 120,
    air_raw: dict | None = None,
) -> DayContext:
    return DayContext(
        account_id=ACCOUNT_ID,
        plan_date=PLAN_DATE,
        timezone_name="Asia/Kolkata",
        now_local=datetime(2026, 4, 15, 8, tzinfo=UTC),
        profile={"city": "Delhi"},
        weather=WeatherReading(
            for_date=PLAN_DATE,
            condition="hot",
            temp_min_c=27,
            temp_max_c=temp_max_c,
            precipitation_chance=0,
            humidity=humidity,
            uv_index=uv_index,
            location=None,
            provider="manual",
            source="user_declared",
            raw=weather_raw or {},
        ),
        air_quality=AirQualityReading(
            for_date=PLAN_DATE,
            aqi=aqi,
            index_system="india_naqi",
            category="Moderate" if aqi == 120 else "Poor",
            prominent_pollutant="PM2.5",
            provider="manual",
            source="user_declared",
            raw=air_raw or {},
        ),
    )


def test_cache_ignores_raw_payload_and_ingestion_metadata():
    first = _context(
        weather_raw={"provider_timestamp": "one", "nested": {"b": 2, "a": 1}},
        air_raw={"request_id": "first"},
    )
    second = _context(
        weather_raw={"nested": {"a": 1, "b": 2}, "provider_timestamp": "two"},
        air_raw={"request_id": "second", "extra": True},
    )
    assert cache_key(first) == cache_key(second)


def test_material_temperature_humidity_and_regime_change_cache_key():
    hot_dry = _context(temp_max_c=38, humidity=25)
    warm_humid = _context(temp_max_c=32, humidity=80)
    assert hot_dry.climate.daily_regime == "hot_dry"
    assert warm_humid.climate.daily_regime == "warm_humid"
    assert cache_key(hot_dry) != cache_key(warm_humid)


def test_material_uv_change_changes_cache_key():
    assert cache_key(_context(uv_index=8.5)) != cache_key(_context(uv_index=9.0))


def test_material_aqi_change_changes_cache_key():
    assert cache_key(_context(aqi=120)) != cache_key(_context(aqi=250))


def test_environmental_provenance_records_normalized_facts_without_raw_payloads():
    context = _context(
        weather_raw={"secret_provider_detail": "must not enter plan inputs"},
        air_raw={"request_id": "must not enter plan inputs"},
    )
    rows = input_rows(context)
    by_key = {(row["input_type"], row["input_key"]): row for row in rows}

    expected_climate = {
        "climate_region",
        "calendar_prior",
        "season",
        "season_source",
        "confidence",
        "temperature_band",
        "moisture_regime",
        "daily_regime",
        "reason",
        "observed_signals",
    }
    assert expected_climate.issubset(
        key for input_type, key in by_key if input_type == "climate"
    )
    assert by_key[("climate", "climate_region")]["value"] == "north_plains"
    assert by_key[("climate", "daily_regime")]["value"] == "hot_dry"
    assert by_key[("weather", "uv_index")]["value"] == 8.5
    assert by_key[("air_quality", "aqi")]["value"] == 120
    assert by_key[("air_quality", "index_system")]["value"] == "india_naqi"
    assert by_key[("air_quality", "category")]["value"] == "Moderate"
    assert by_key[("air_quality", "prominent_pollutant")]["value"] == "PM2.5"
    assert "secret_provider_detail" not in str(rows)
    assert "request_id" not in str(rows)
