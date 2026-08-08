"""Environmental normalization and interpretation rules.

This module provides deterministic mapping from raw inputs (AQI, temperature,
seasons) to the categorical values the orchestration engine understands.
No external API calls are made here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domains.recommendation.compatibility import (
    SEASON_FOR_CONDITION,
)

# --- Air Quality ------------------------------------------------------------

def determine_naqi_category(aqi: int | None, index_system: str, existing_category: str | None = None) -> str | None:
    """Map raw AQI to the Indian National Air Quality Index (NAQI) categories."""
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


# --- Climate & Weather ------------------------------------------------------

@dataclass
class ClimateContext:
    """The resolved environmental context for a given day."""

    season: str
    temperature_band: str
    condition: str | None


def normalise_weather_condition(condition: str | None) -> str | None:
    """Map raw weather condition text to known orchestration conditions."""
    if not condition:
        return None
    text = " ".join(condition.lower().split())

    if not text:
        return None

    # Known orchestration values
    if text in SEASON_FOR_CONDITION:
        return text

    # Map legacy season vocabulary to expected conditions
    season_to_condition = {
        "summer": "hot",
        "winter": "cold",
        "monsoon": "rainy",
        "spring": "mild",
        "autumn": "cool",
    }
    if text in season_to_condition:
        return season_to_condition[text]

    # Heuristic partial matches for typical user inputs
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


def resolve_climate_context(
    for_date: date,
    temp_max_c: float | None = None,
    condition: str | None = None,
    location: str | None = None,
    humidity: int | None = None,
    precipitation_chance: int | None = None,
) -> ClimateContext:
    """Resolve the overarching climate context for a specific day in India."""
    month = for_date.month
    
    # 1. Base season on Indian calendar months
    if month in (12, 1, 2):
        season = "winter"
    elif month in (3, 4, 5, 6):
        season = "summer"
    elif month in (7, 8, 9):
        season = "monsoon"
    else:  # 10, 11
        season = "autumn"

    # Location Must Change Reasoning (South peninsular vs rest of India)
    # South India does not experience true winter.
    is_south = False
    if location:
        loc = location.lower()
        if any(s in loc for s in ("kerala", "tamil nadu", "karnataka", "andhra", "telangana", "goa", "chennai", "bengaluru", "bangalore", "hyderabad", "kochi", "trivandrum")):
            is_south = True

    if is_south and season == "winter":
        season = "autumn"

    # Observed Weather Must Matter
    # Rain out of season -> transition feeling
    if precipitation_chance is not None and precipitation_chance > 60:
        if season == "summer":
            season = "monsoon"
    
    # Heat out of season
    if temp_max_c is not None and temp_max_c >= 35:
        if season in ("autumn", "winter"):
            season = "summer"

    # 2. Temperature banding
    if temp_max_c is None:
        temp_band = "unknown"
    elif temp_max_c < 15:
        temp_band = "cold"
    elif temp_max_c < 25:
        temp_band = "mild"
    elif temp_max_c < 35:
        temp_band = "warm"
    else:
        temp_band = "hot"

    # 3. Resolve normalized condition
    norm_condition = normalise_weather_condition(condition)

    return ClimateContext(
        season=season,
        temperature_band=temp_band,
        condition=norm_condition,
    )
