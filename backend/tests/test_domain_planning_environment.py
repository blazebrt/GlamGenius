import pytest
from datetime import date
from app.domains.planning.environment import determine_naqi_category, normalise_weather_condition, resolve_climate_context

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

    ctx = resolve_climate_context(date(2023, 4, 15))
    assert ctx.season == "summer"

    ctx = resolve_climate_context(date(2023, 8, 15))
    assert ctx.season == "monsoon"

    ctx = resolve_climate_context(date(2023, 11, 15))
    assert ctx.season == "autumn"

def test_resolve_climate_context_south_india():
    # South India should override winter to autumn
    ctx = resolve_climate_context(date(2023, 1, 15), location="Chennai, Tamil Nadu")
    assert ctx.season == "autumn"

    ctx = resolve_climate_context(date(2023, 1, 15), location="Bengaluru")
    assert ctx.season == "autumn"

    # Non-winter stays the same
    ctx = resolve_climate_context(date(2023, 4, 15), location="Chennai")
    assert ctx.season == "summer"

    # North India doesn't get overridden
    ctx = resolve_climate_context(date(2023, 1, 15), location="Delhi")
    assert ctx.season == "winter"

def test_resolve_climate_context_weather_overrides():
    # Rain in summer -> monsoon
    ctx = resolve_climate_context(date(2023, 4, 15), precipitation_chance=80)
    assert ctx.season == "monsoon"

    # Heat in winter -> summer
    ctx = resolve_climate_context(date(2023, 1, 15), temp_max_c=36)
    assert ctx.season == "summer"
