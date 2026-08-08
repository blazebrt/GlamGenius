"""Deterministic environmental normalization for the planning context.

This module interprets only facts already supplied by the user or a provider.
It does not call an external service and it does not make clothing decisions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.domains.recommendation.compatibility import SEASON_FOR_CONDITION

# --- Air quality -----------------------------------------------------------


def determine_naqi_category(
    aqi: int | None, index_system: str, existing_category: str | None = None
) -> str | None:
    """Map an Indian NAQI value to its published category vocabulary."""
    if index_system != "india_naqi":
        return existing_category
    if aqi is None or aqi < 0:
        return "unknown"
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Satisfactory"
    if aqi <= 200:
        return "Moderate"
    if aqi <= 300:
        return "Poor"
    if aqi <= 400:
        return "Very Poor"
    return "Severe"


# --- Climate regions and reviewed calendar priors -------------------------


class ClimateRegion(StrEnum):
    """Conservative GlamGenius product climate profiles.

    These profiles are routing categories for product context. They are not a
    replacement for IMD meteorological subdivisions or a climatology dataset.
    """

    NORTH_PLAINS = "north_plains"
    NORTHWEST_ARID = "northwest_arid"
    WESTERN_HIMALAYA = "western_himalaya"
    CENTRAL_INDIA = "central_india"
    WEST_COAST = "west_coast"
    EAST_GANGETIC = "east_gangetic"
    NORTHEAST = "northeast"
    DECCAN_INTERIOR = "deccan_interior"
    SOUTHEAST_PENINSULA = "southeast_peninsula"
    ISLANDS = "islands"
    UNKNOWN_INDIA = "unknown_india"


# City aliases take precedence over state aliases. That lets, for example,
# coastal Mangaluru resolve to west_coast even when the input also says
# Karnataka. The tables are deliberately small and reviewed, not exhaustive.
CITY_ALIASES_BY_REGION: dict[ClimateRegion, frozenset[str]] = {
    ClimateRegion.NORTH_PLAINS: frozenset(
        {
            "delhi",
            "new delhi",
            "gurgaon",
            "gurugram",
            "noida",
            "ghaziabad",
            "meerut",
            "chandigarh",
            "amritsar",
            "ludhiana",
        }
    ),
    ClimateRegion.NORTHWEST_ARID: frozenset(
        {"jaipur", "jodhpur", "jaisalmer", "bikaner", "udaipur", "kota"}
    ),
    ClimateRegion.WESTERN_HIMALAYA: frozenset(
        {"shimla", "srinagar", "manali", "leh", "dehradun", "mussoorie"}
    ),
    ClimateRegion.CENTRAL_INDIA: frozenset(
        {"bhopal", "indore", "gwalior", "jabalpur", "raipur", "nagpur"}
    ),
    ClimateRegion.WEST_COAST: frozenset(
        {
            "mumbai",
            "panaji",
            "goa",
            "mangaluru",
            "mangalore",
            "kochi",
            "cochin",
            "thiruvananthapuram",
            "trivandrum",
            "kozhikode",
        }
    ),
    ClimateRegion.EAST_GANGETIC: frozenset(
        {"kolkata", "patna", "ranchi", "varanasi", "gaya"}
    ),
    ClimateRegion.NORTHEAST: frozenset(
        {"guwahati", "shillong", "aizawl", "imphal", "kohima", "agartala", "itanagar", "gangtok"}
    ),
    ClimateRegion.DECCAN_INTERIOR: frozenset(
        {"bengaluru", "bangalore", "hyderabad", "mysuru", "mysore", "pune"}
    ),
    ClimateRegion.SOUTHEAST_PENINSULA: frozenset(
        {"chennai", "puducherry", "pondicherry", "visakhapatnam", "vizag", "vijayawada"}
    ),
    ClimateRegion.ISLANDS: frozenset({"port blair", "kavaratti"}),
}

STATE_ALIASES_BY_REGION: dict[ClimateRegion, frozenset[str]] = {
    ClimateRegion.NORTH_PLAINS: frozenset({"delhi ncr", "punjab", "haryana"}),
    ClimateRegion.NORTHWEST_ARID: frozenset({"rajasthan"}),
    ClimateRegion.WESTERN_HIMALAYA: frozenset(
        {"himachal pradesh", "uttarakhand", "jammu and kashmir", "jammu kashmir", "ladakh"}
    ),
    ClimateRegion.CENTRAL_INDIA: frozenset({"madhya pradesh", "chhattisgarh"}),
    ClimateRegion.WEST_COAST: frozenset({"goa", "kerala", "coastal karnataka"}),
    ClimateRegion.EAST_GANGETIC: frozenset({"bihar", "jharkhand", "west bengal"}),
    ClimateRegion.NORTHEAST: frozenset(
        {
            "assam",
            "meghalaya",
            "arunachal pradesh",
            "manipur",
            "mizoram",
            "nagaland",
            "tripura",
            "sikkim",
        }
    ),
    ClimateRegion.DECCAN_INTERIOR: frozenset({"karnataka", "telangana"}),
    ClimateRegion.SOUTHEAST_PENINSULA: frozenset(
        {"tamil nadu", "andhra pradesh", "coastal andhra", "puducherry"}
    ),
    ClimateRegion.ISLANDS: frozenset(
        {"andaman and nicobar", "andaman nicobar", "lakshadweep"}
    ),
}

NORTH_PLAINS_SEASON_PROFILE = {
    1: "winter",
    2: "winter",
    3: "summer",
    4: "summer",
    5: "summer",
    6: "summer",
    7: "monsoon",
    8: "monsoon",
    9: "monsoon",
    10: "autumn",
    11: "autumn",
    12: "winter",
}

# V3-01 has one explicitly reviewed regional calendar. New profiles must be
# reviewed before being added; enum membership alone never implies expertise.
REGIONAL_SEASON_PROFILES: dict[ClimateRegion, dict[int, str]] = {
    ClimateRegion.NORTH_PLAINS: NORTH_PLAINS_SEASON_PROFILE,
}

def _normalise_location(location: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", location.casefold()).split())


def _matching_region(
    location: str, aliases_by_region: dict[ClimateRegion, frozenset[str]]
) -> ClimateRegion | None:
    padded = f" {_normalise_location(location)} "
    for region, aliases in aliases_by_region.items():
        if any(f" {alias} " in padded for alias in aliases):
            return region
    return None


def _resolve_climate_region_with_source(
    location: str | None,
) -> tuple[ClimateRegion, str]:
    if not location or not location.strip():
        return ClimateRegion.UNKNOWN_INDIA, "unknown"
    city_region = _matching_region(location, CITY_ALIASES_BY_REGION)
    if city_region is not None:
        return city_region, "known_city"
    state_region = _matching_region(location, STATE_ALIASES_BY_REGION)
    if state_region is not None:
        return state_region, "known_state"
    return ClimateRegion.UNKNOWN_INDIA, "unknown"


def resolve_climate_region(location: str | None) -> ClimateRegion:
    """Resolve only reviewed city/state aliases; uncertainty stays unknown."""
    return _resolve_climate_region_with_source(location)[0]


# --- Daily observed conditions --------------------------------------------


TEMPERATURE_COLD_BELOW_C = 15.0
TEMPERATURE_MILD_BELOW_C = 25.0
TEMPERATURE_WARM_BELOW_C = 35.0
HUMIDITY_DRY_AT_OR_BELOW = 35
HUMIDITY_HUMID_AT_OR_ABOVE = 70
PRECIPITATION_WET_AT_OR_ABOVE = 60


@dataclass(frozen=True)
class ClimateContext:
    """Calendar context and current observations kept as separate concepts."""

    season: str
    calendar_prior: str
    climate_region: ClimateRegion
    region_source: str
    season_source: str
    temperature_band: str
    moisture_regime: str
    daily_regime: str
    condition: str | None
    confidence: float
    reason: str
    location: str | None
    signals: list[str]
    observed_signals: list[str]


def normalise_weather_condition(condition: str | None) -> str | None:
    """Map free text or legacy season vocabulary to orchestration conditions."""
    if not condition:
        return None
    text = " ".join(condition.lower().split())
    if not text:
        return None
    if text in SEASON_FOR_CONDITION:
        return text

    season_to_condition = {
        "summer": "hot",
        "winter": "cold",
        "monsoon": "rainy",
        "spring": "mild",
        "autumn": "cool",
    }
    if text in season_to_condition:
        return season_to_condition[text]
    if "rain" in text or "shower" in text or "drizzle" in text:
        return "rainy"
    if "snow" in text or "freez" in text or "ice" in text:
        return "cold"
    if "humid" in text or "muggy" in text:
        return "humid"
    if "hot" in text:
        return "hot"
    if "warm" in text:
        return "warm"
    if "cool" in text or "chilly" in text:
        return "cool"
    if "wind" in text or "breez" in text:
        return "windy"
    return text


def temperature_band(temp_max_c: float | None) -> str:
    if temp_max_c is None:
        return "unknown"
    if temp_max_c < TEMPERATURE_COLD_BELOW_C:
        return "cold"
    if temp_max_c < TEMPERATURE_MILD_BELOW_C:
        return "mild"
    if temp_max_c < TEMPERATURE_WARM_BELOW_C:
        return "warm"
    return "hot"


def moisture_regime(
    humidity: int | None,
    precipitation_chance: int | None,
    condition: str | None,
) -> str:
    if condition == "rainy" or (
        precipitation_chance is not None
        and precipitation_chance >= PRECIPITATION_WET_AT_OR_ABOVE
    ):
        return "wet"
    if humidity is None:
        return "unknown"
    if humidity >= HUMIDITY_HUMID_AT_OR_ABOVE:
        return "humid"
    if humidity <= HUMIDITY_DRY_AT_OR_BELOW:
        return "dry"
    return "normal"


def daily_climate_regime(temp_band: str, moisture: str) -> str:
    if temp_band == "unknown" or moisture == "unknown":
        return "unknown"
    if moisture == "normal":
        return temp_band
    return f"{temp_band}_{moisture}"


def resolve_climate_context(
    for_date: date,
    temp_max_c: float | None = None,
    condition: str | None = None,
    location: str | None = None,
    humidity: int | None = None,
    precipitation_chance: int | None = None,
) -> ClimateContext:
    """Resolve regional prior and current conditions without conflating them.

    Reliable current observed conditions outrank seasonal assumptions for
    appearance decisions. Regional climatology outranks generic nationwide
    assumptions. Unknown data remains unknown.
    """
    region, region_source = _resolve_climate_region_with_source(location)
    reviewed_profile = REGIONAL_SEASON_PROFILES.get(region)
    if reviewed_profile is not None:
        calendar_prior = reviewed_profile[for_date.month]
        season_source = "regional_profile"
    else:
        # No reviewed profile means no seasonal claim. Never borrow another
        # region's calendar merely to populate a legacy field.
        calendar_prior = "unknown"
        season_source = "unreviewed_region"

    # ``season`` is a reviewed calendar value or explicit unknown. Observations
    # deliberately never rewrite it; actual conditions are represented by
    # daily_regime instead.
    season = calendar_prior
    normalized_condition = normalise_weather_condition(condition)
    temp_band = temperature_band(temp_max_c)
    moisture = moisture_regime(
        humidity, precipitation_chance, normalized_condition
    )
    daily_regime = daily_climate_regime(temp_band, moisture)

    signals = [
        f"month_{for_date.month}",
        f"climate_region_{region.value}",
        (
            f"reviewed_profile_{region.value}"
            if reviewed_profile is not None
            else "unreviewed_region"
        ),
    ]
    observed_signals: list[str] = []
    if temp_band != "unknown":
        observed_signals.append(temp_band)
    if humidity is not None and humidity >= HUMIDITY_HUMID_AT_OR_ABOVE:
        observed_signals.append("humid")
    elif humidity is not None and humidity <= HUMIDITY_DRY_AT_OR_BELOW:
        observed_signals.append("dry")
    rain_signal = normalized_condition == "rainy" or (
        precipitation_chance is not None
        and precipitation_chance >= PRECIPITATION_WET_AT_OR_ABOVE
    )
    if rain_signal:
        observed_signals.append("rain_likely")
    if moisture == "wet":
        observed_signals.append("wet")

    has_observations = any(
        value is not None
        for value in (temp_max_c, normalized_condition, humidity, precipitation_chance)
    )
    if reviewed_profile is not None:
        confidence = 0.9 if has_observations else 0.6
    else:
        confidence = 0.6 if has_observations else 0.4

    conflicts: list[str] = []
    if temp_band == "hot" and season in {"winter", "autumn"}:
        observed_signals.append("unusually_hot")
        conflicts.append("observed heat conflicts with the calendar prior")
    elif temp_band == "cold" and season in {"summer", "monsoon"}:
        observed_signals.append("unusually_cold")
        conflicts.append("observed cold conflicts with the calendar prior")
    if rain_signal and season in {"summer", "winter"}:
        conflicts.append("observed rain conflicts with the calendar prior")

    if conflicts:
        confidence = 0.6 if reviewed_profile is not None else 0.4
        reason = "; ".join(conflicts).capitalize() + "."
    elif reviewed_profile is not None and has_observations:
        reason = "Reviewed regional calendar prior with current observed conditions."
    elif reviewed_profile is not None:
        reason = "Reviewed regional calendar prior; current weather is unavailable."
    elif has_observations:
        reason = "Regional season is unreviewed; current observed conditions remain available."
    else:
        reason = "Regional season is unreviewed and current weather is unavailable."

    return ClimateContext(
        season=season,
        calendar_prior=calendar_prior,
        climate_region=region,
        region_source=region_source,
        season_source=season_source,
        temperature_band=temp_band,
        moisture_regime=moisture,
        daily_regime=daily_regime,
        condition=normalized_condition,
        confidence=confidence,
        reason=reason,
        location=location,
        signals=signals,
        observed_signals=observed_signals,
    )
