import pytest
from datetime import date
from app.domains.planning.environment import determine_naqi_category, normalise_weather_condition, resolve_climate_context

def test_determine_naqi_category():
    assert determine_naqi_category(50, "india_naqi") == "Good"
    assert determine_naqi_category(100, "india_naqi") == "Satisfactory"
    assert determine_naqi_category(150, "india_naqi") == "Moderate"
    assert determine_naqi_category(250, "india_naqi") == "Poor"
    assert determine_naqi_category(350, "india_naqi") == "Very Poor"
    assert determine_naqi_category(450, "india_naqi") == "Severe"

    assert determine_naqi_category(None, "india_naqi") == "unknown"
    assert determine_naqi_category(-1, "india_naqi") == "unknown"

    assert determine_naqi_category(50, "us_aqi", "Custom") == "Custom"


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
