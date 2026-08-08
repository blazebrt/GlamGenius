from datetime import date

import pytest
from app.domains.planning.environment import (
    NORTH_PLAINS_SEASON_PROFILE,
    REGIONAL_SEASON_PROFILES,
    ClimateRegion,
    determine_naqi_category,
    normalise_weather_condition,
    resolve_climate_context,
    resolve_climate_region,
)


def test_determine_naqi_category():
    assert determine_naqi_category(0, "india_naqi") == "Good"
    assert determine_naqi_category(50, "india_naqi") == "Good"
    assert determine_naqi_category(51, "india_naqi") == "Satisfactory"
    assert determine_naqi_category(100, "india_naqi") == "Satisfactory"
    assert determine_naqi_category(101, "india_naqi") == "Moderate"
    assert determine_naqi_category(200, "india_naqi") == "Moderate"
    assert determine_naqi_category(201, "india_naqi") == "Poor"
    assert determine_naqi_category(300, "india_naqi") == "Poor"
    assert determine_naqi_category(301, "india_naqi") == "Very Poor"
    assert determine_naqi_category(400, "india_naqi") == "Very Poor"
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


@pytest.mark.parametrize(
    ("for_date", "expected"),
    [
        (date(2026, 12, 1), "winter"),
        (date(2026, 2, 28), "winter"),
        (date(2026, 3, 1), "summer"),
        (date(2026, 6, 30), "summer"),
        (date(2026, 7, 1), "monsoon"),
        (date(2026, 9, 30), "monsoon"),
        (date(2026, 10, 1), "autumn"),
        (date(2026, 11, 30), "autumn"),
    ],
)
def test_north_plains_reviewed_calendar_boundaries(for_date, expected):
    context = resolve_climate_context(for_date, location="Delhi")
    assert context.climate_region == ClimateRegion.NORTH_PLAINS
    assert context.calendar_prior == expected
    assert context.season == expected
    assert context.season_source == "regional_profile"


@pytest.mark.parametrize(
    ("month", "expected"),
    [(12, "winter"), (4, "summer"), (6, "summer"), (8, "monsoon"), (10, "autumn")],
)
def test_delhi_month_examples_use_north_plains_profile(month, expected):
    context = resolve_climate_context(date(2026, month, 15), location="Delhi")
    assert context.climate_region == "north_plains"
    assert context.season == expected


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Chennai, Tamil Nadu", ClimateRegion.SOUTHEAST_PENINSULA),
        ("Mumbai, Maharashtra", ClimateRegion.WEST_COAST),
        ("Jaipur, Rajasthan", ClimateRegion.NORTHWEST_ARID),
        ("Bengaluru, Karnataka", ClimateRegion.DECCAN_INTERIOR),
        ("Shillong, Meghalaya", ClimateRegion.NORTHEAST),
        ("Port Blair", ClimateRegion.ISLANDS),
        ("A city not in the reviewed map", ClimateRegion.UNKNOWN_INDIA),
        (None, ClimateRegion.UNKNOWN_INDIA),
    ],
)
def test_climate_region_resolver_is_deterministic_and_conservative(location, expected):
    assert resolve_climate_region(location) == expected


def test_city_mapping_takes_precedence_over_broad_state_mapping():
    assert resolve_climate_region("Mangaluru, Karnataka") == ClimateRegion.WEST_COAST


def test_unreviewed_regions_keep_season_unknown():
    context = resolve_climate_context(date(2026, 11, 15), location="Chennai")
    assert context.climate_region == "southeast_peninsula"
    assert context.calendar_prior == "unknown"
    assert context.season == "unknown"
    assert context.season_source == "unreviewed_region"
    assert "unreviewed_region" in context.signals
    assert not any(signal.startswith("reviewed_profile_") for signal in context.signals)
    assert context.confidence == 0.4


def test_chennai_december_without_weather_has_no_season_claim():
    context = resolve_climate_context(date(2026, 12, 15), location="Chennai")
    assert context.climate_region == ClimateRegion.SOUTHEAST_PENINSULA
    assert context.calendar_prior == "unknown"
    assert context.season == "unknown"
    assert context.season_source == "unreviewed_region"


def test_chennai_observations_remain_useful_with_unknown_season():
    context = resolve_climate_context(
        date(2026, 11, 15),
        temp_max_c=29,
        condition="rainy",
        location="Chennai",
        humidity=85,
        precipitation_chance=80,
    )
    assert context.climate_region == "southeast_peninsula"
    assert context.calendar_prior == "unknown"
    assert context.season == "unknown"
    assert context.season_source == "unreviewed_region"
    assert context.temperature_band == "warm"
    assert context.moisture_regime == "wet"
    assert context.daily_regime == "warm_wet"
    assert context.confidence == 0.6


def test_unknown_location_stays_unknown_and_low_confidence():
    context = resolve_climate_context(date(2026, 4, 15), location="Unmapped Place")
    assert context.climate_region == "unknown_india"
    assert context.region_source == "unknown"
    assert context.calendar_prior == "unknown"
    assert context.season == "unknown"
    assert context.season_source == "unreviewed_region"
    assert context.moisture_regime == "unknown"
    assert context.daily_regime == "unknown"
    assert context.confidence == 0.4


@pytest.mark.parametrize(
    ("location", "expected_region", "for_date"),
    [
        ("Mumbai", ClimateRegion.WEST_COAST, date(2026, 8, 15)),
        ("Bengaluru", ClimateRegion.DECCAN_INTERIOR, date(2026, 1, 15)),
        ("Shillong", ClimateRegion.NORTHEAST, date(2026, 7, 15)),
        ("Unmapped Indian location", ClimateRegion.UNKNOWN_INDIA, date(2026, 4, 15)),
    ],
)
def test_unreviewed_region_examples_have_unknown_season(
    location, expected_region, for_date
):
    context = resolve_climate_context(for_date, location=location)
    assert context.climate_region == expected_region
    assert context.season == "unknown"
    assert context.calendar_prior == "unknown"
    assert context.season_source == "unreviewed_region"


def test_every_unreviewed_region_can_never_borrow_north_plains_calendar():
    locations = {
        ClimateRegion.NORTHWEST_ARID: "Jaipur",
        ClimateRegion.WESTERN_HIMALAYA: "Shimla",
        ClimateRegion.CENTRAL_INDIA: "Bhopal",
        ClimateRegion.WEST_COAST: "Mumbai",
        ClimateRegion.EAST_GANGETIC: "Patna",
        ClimateRegion.NORTHEAST: "Shillong",
        ClimateRegion.DECCAN_INTERIOR: "Bengaluru",
        ClimateRegion.SOUTHEAST_PENINSULA: "Chennai",
        ClimateRegion.ISLANDS: "Port Blair",
        ClimateRegion.UNKNOWN_INDIA: "Unmapped Indian location",
    }
    assert set(REGIONAL_SEASON_PROFILES) == {ClimateRegion.NORTH_PLAINS}
    for region, location in locations.items():
        context = resolve_climate_context(date(2026, 4, 15), location=location)
        assert context.climate_region == region
        assert context.season == "unknown"
        assert context.calendar_prior == "unknown"
        assert context.season_source == "unreviewed_region"
        assert context.season not in NORTH_PLAINS_SEASON_PROFILE.values()


def test_delhi_april_hot_dry_daily_regime():
    context = resolve_climate_context(
        date(2026, 4, 15),
        temp_max_c=38,
        condition="hot",
        location="Delhi",
        humidity=25,
        precipitation_chance=0,
    )
    assert context.season == "summer"
    assert context.temperature_band == "hot"
    assert context.moisture_regime == "dry"
    assert context.daily_regime == "hot_dry"
    assert context.confidence == 0.9


def test_delhi_august_hot_humid_rain_is_warm_wet():
    context = resolve_climate_context(
        date(2026, 8, 15),
        temp_max_c=32,
        condition="rainy",
        location="Delhi",
        humidity=85,
        precipitation_chance=80,
    )
    assert context.season == "monsoon"
    assert context.temperature_band == "warm"
    assert context.moisture_regime == "wet"
    assert context.daily_regime == "warm_wet"
    assert {"humid", "rain_likely", "wet"}.issubset(context.observed_signals)
    assert context.confidence == 0.9


def test_delhi_january_cold_dry_daily_regime():
    context = resolve_climate_context(
        date(2026, 1, 15),
        temp_max_c=10,
        condition="cold",
        location="Delhi",
        humidity=30,
        precipitation_chance=0,
    )
    assert context.season == "winter"
    assert context.daily_regime == "cold_dry"


def test_hot_january_observation_does_not_rewrite_winter():
    context = resolve_climate_context(
        date(2026, 1, 15),
        temp_max_c=36,
        condition="hot",
        location="Delhi",
        humidity=25,
        precipitation_chance=0,
    )
    assert context.calendar_prior == "winter"
    assert context.season == "winter"
    assert context.daily_regime == "hot_dry"
    assert "unusually_hot" in context.observed_signals
    assert context.confidence == 0.6


def test_rainy_april_observation_does_not_rewrite_summer():
    context = resolve_climate_context(
        date(2026, 4, 15),
        temp_max_c=30,
        condition="rainy",
        location="Delhi",
        humidity=85,
        precipitation_chance=80,
    )
    assert context.calendar_prior == "summer"
    assert context.season == "summer"
    assert context.daily_regime == "warm_wet"
    assert "rain_likely" in context.observed_signals
    assert context.confidence == 0.6


def test_reviewed_profile_without_current_weather_has_moderate_confidence():
    context = resolve_climate_context(date(2026, 1, 15), location="Delhi")
    assert context.confidence == 0.6
    assert context.temperature_band == "unknown"
    assert context.moisture_regime == "unknown"
    assert context.daily_regime == "unknown"
