from datetime import date

from app.domains.planning.environment import (
    determine_naqi_category,
    normalise_weather_condition,
    resolve_climate_context,
)


def test_determine_naqi_category():
    # Good: 0-50
    assert determine_naqi_category(0, "india_naqi") == "Good"
    assert determine_naqi_category(50, "india_naqi") == "Good"
    # Satisfactory: 51-100
    assert determine_naqi_category(51, "india_naqi") == "Satisfactory"
    assert determine_naqi_category(100, "india_naqi") == "Satisfactory"
    # Moderate: 101-200
    assert determine_naqi_category(101, "india_naqi") == "Moderate"
    assert determine_naqi_category(200, "india_naqi") == "Moderate"
    # Poor: 201-300
    assert determine_naqi_category(201, "india_naqi") == "Poor"
    assert determine_naqi_category(300, "india_naqi") == "Poor"
    # Very Poor: 301-400
    assert determine_naqi_category(301, "india_naqi") == "Very Poor"
    assert determine_naqi_category(400, "india_naqi") == "Very Poor"
    # Severe: > 400
    assert determine_naqi_category(401, "india_naqi") == "Severe"
    assert determine_naqi_category(500, "india_naqi") == "Severe"

    assert determine_naqi_category(None, "india_naqi") == "unknown"
    assert determine_naqi_category(-1, "india_naqi") == "unknown"

    assert determine_naqi_category(50, "unknown", "Custom") == "Custom"


def test_normalise_weather_condition():
    assert normalise_weather_condition("summer") == "hot"
    assert normalise_weather_condition("Heavy Rain") == "rainy"
    assert normalise_weather_condition("Snow") == "cold"
    assert normalise_weather_condition("Humid") == "humid"
    assert normalise_weather_condition("Warm breeze") == "warm"


def test_resolve_climate_context_base():
    ctx = resolve_climate_context(date(2023, 1, 15))
    assert ctx.season == "winter"
    assert ctx.calendar_prior == "winter"
    assert ctx.confidence == 0.8  # No location

    ctx = resolve_climate_context(date(2023, 4, 15), location="Delhi")
    assert ctx.season == "summer"
    assert ctx.confidence == 1.0

def test_resolve_climate_context_south_india():
    # South India should NOT override winter to autumn anymore
    ctx = resolve_climate_context(date(2023, 1, 15), location="Chennai, Tamil Nadu")
    assert ctx.season == "winter"
    assert "location_south_india" in ctx.signals
    assert ctx.confidence == 1.0

    # Autumn rain in South India gets a specific reason
    ctx = resolve_climate_context(date(2023, 11, 15), location="Chennai", precipitation_chance=80)
    assert ctx.season == "autumn"
    assert ctx.reason == "southern_post_monsoon_wet_context"

def test_resolve_climate_context_weather_overrides():
    # Rain in summer -> no longer overrides season, just adds signal and lowers confidence
    ctx = resolve_climate_context(date(2023, 4, 15), location="Delhi", precipitation_chance=80)
    assert ctx.season == "summer"
    assert "rain_likely" in ctx.observed_signals
    assert ctx.confidence == 0.7
    assert ctx.reason == "Conflicting precipitation observation for season"

    # Heat in winter -> no longer overrides season
    ctx = resolve_climate_context(date(2023, 1, 15), location="Delhi", temp_max_c=36)
    assert ctx.season == "winter"
    assert "unusually_hot" in ctx.observed_signals
    assert ctx.confidence == 0.7
    assert ctx.reason == "Conflicting temperature observation for season"

    # Monsoon with rain confirms prior
    ctx = resolve_climate_context(date(2023, 8, 15), location="Delhi", precipitation_chance=80)
    assert ctx.season == "monsoon"
    assert ctx.confidence == 1.0
    assert ctx.reason == "Observations confirm monsoon prior"
