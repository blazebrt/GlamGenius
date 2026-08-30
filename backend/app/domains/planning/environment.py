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
#
# India publishes its own index. The European AQI saturates above 100 and cannot
# tell NAQI 150 from NAQI 450 — which is the entire range that matters in the
# North Indian winter. So we compute the Indian NAQI ourselves from the raw
# PM2.5 and PM10 concentrations a provider returns, using the published CPCB
# breakpoints, and the European value is kept only as a stored fallback.
#
# Source: Central Pollution Control Board, Ministry of Environment, Forest and
# Climate Change, Government of India — "National Air Quality Index" (2014),
# breakpoint table for sub-index calculation.
# https://cpcb.nic.in/openpdffile.php?id=TGF0ZXN0RmlsZS9MYXRlc3RfMTI1X0FRSV9SRVBPUlRfMjAxNC5wZGY=
#
# One honest limitation, stated rather than hidden: CPCB's published index is
# computed from a minimum of three pollutants, one of which must be PM2.5 or
# PM10. We have two. In Indian conditions particulate matter is almost always
# the pollutant that sets the index, so the PM-only maximum is the closest
# honest reading — but it is a PM-only reading, and every record says so in
# ``naqi_basis``. It is never presented as the official CPCB station value.

NAQI_INDEX_SYSTEM = "india_naqi"
NAQI_BASIS_PM_ONLY = "pm2_5_pm10_only"
NAQI_SOURCE = "CPCB National Air Quality Index (2014) breakpoints"

#: CPCB sub-index breakpoints: (concentration low, concentration high,
#: index low, index high). Concentrations are 24-hour averages in µg/m³.
CPCB_PM25_BREAKPOINTS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 30.0, 0, 50),
    (30.0, 60.0, 51, 100),
    (60.0, 90.0, 101, 200),
    (90.0, 120.0, 201, 300),
    (120.0, 250.0, 301, 400),
    (250.0, 500.0, 401, 500),
)

CPCB_PM10_BREAKPOINTS: tuple[tuple[float, float, int, int], ...] = (
    (0.0, 50.0, 0, 50),
    (50.0, 100.0, 51, 100),
    (100.0, 250.0, 101, 200),
    (250.0, 350.0, 201, 300),
    (350.0, 430.0, 301, 400),
    (430.0, 1000.0, 401, 500),
)

#: The published category vocabulary, with the index band each one covers.
NAQI_CATEGORY_BANDS: tuple[tuple[int, str], ...] = (
    (50, "Good"),
    (100, "Satisfactory"),
    (200, "Moderate"),
    (300, "Poor"),
    (400, "Very Poor"),
    (500, "Severe"),
)

#: Ordered worst-last. Comparisons in the rules engine use this, never the
#: raw number, so a band change is one edit here.
NAQI_CATEGORY_ORDER: tuple[str, ...] = (
    "Good", "Satisfactory", "Moderate", "Poor", "Very Poor", "Severe",
)


def naqi_sub_index(
    concentration: float | None, breakpoints: tuple[tuple[float, float, int, int], ...]
) -> int | None:
    """One pollutant's CPCB sub-index, by linear interpolation within its band."""
    if concentration is None or concentration < 0:
        return None
    for low_c, high_c, low_i, high_i in breakpoints:
        if concentration <= high_c:
            if high_c == low_c:
                return low_i
            span = (high_i - low_i) / (high_c - low_c)
            return round(low_i + span * (concentration - low_c))
    # Above the top breakpoint the index is capped at its published maximum.
    return breakpoints[-1][3]


def naqi_from_particulates(
    pm2_5: float | None, pm10: float | None
) -> tuple[int | None, str | None]:
    """The Indian NAQI and its prominent particulate, from raw concentrations.

    Returns ``(None, None)`` when neither pollutant is available. Guessing an
    index from nothing would be worse than saying we do not know.
    """
    sub_indices = {
        "pm2_5": naqi_sub_index(pm2_5, CPCB_PM25_BREAKPOINTS),
        "pm10": naqi_sub_index(pm10, CPCB_PM10_BREAKPOINTS),
    }
    available = {key: value for key, value in sub_indices.items() if value is not None}
    if not available:
        return None, None
    highest = max(available.values())
    # Ties go to PM2.5, which is the finer fraction and the one CPCB reports
    # first when two sub-indices coincide.
    prominent = "pm2_5" if available.get("pm2_5") == highest else next(
        key for key, value in available.items() if value == highest
    )
    return highest, prominent


def naqi_category(aqi: int | None) -> str:
    """The published CPCB category for an Indian NAQI value."""
    if aqi is None or aqi < 0:
        return "unknown"
    for ceiling, name in NAQI_CATEGORY_BANDS:
        if aqi <= ceiling:
            return name
    return "Severe"


def determine_naqi_category(
    aqi: int | None, index_system: str, existing_category: str | None = None
) -> str | None:
    """Map an Indian NAQI value to its published category vocabulary.

    Any other index system keeps whatever category it arrived with: a European
    or US category is not an Indian one, and relabelling it would be inventing
    a reading.
    """
    if index_system != NAQI_INDEX_SYSTEM:
        return existing_category
    return naqi_category(aqi)


def naqi_at_least(category: str | None, threshold: str) -> bool:
    """True when ``category`` is ``threshold`` or worse on the published scale."""
    if category not in NAQI_CATEGORY_ORDER or threshold not in NAQI_CATEGORY_ORDER:
        return False
    return NAQI_CATEGORY_ORDER.index(category) >= NAQI_CATEGORY_ORDER.index(threshold)


def naqi_at_most(category: str | None, threshold: str) -> bool:
    """True when ``category`` is ``threshold`` or better on the published scale."""
    if category not in NAQI_CATEGORY_ORDER or threshold not in NAQI_CATEGORY_ORDER:
        return False
    return NAQI_CATEGORY_ORDER.index(category) <= NAQI_CATEGORY_ORDER.index(threshold)


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
