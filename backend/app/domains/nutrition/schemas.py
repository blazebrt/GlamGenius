"""Validated request contracts owned by the Nutrition domain."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.nutrition.preferences import SUPPORTED_DIETS, SUPPORTED_FOCUS_KEYS


class NutritionPreferencePatch(BaseModel):
    """What this person eats. A constraint on suggestions, not a suggestion."""

    model_config = ConfigDict(extra="forbid")

    diet: Literal[SUPPORTED_DIETS] | None = None  # type: ignore[valid-type]
    avoid_foods: list[str] | None = Field(default=None, max_length=40)
    focus_nutrients: list[str] | None = Field(default=None, max_length=12)
    enabled: bool | None = None

    @field_validator("focus_nutrients")
    @classmethod
    def _known(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = [row for row in value if row not in SUPPORTED_FOCUS_KEYS]
        if unknown:
            raise ValueError(
                f"We have no food context for: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(SUPPORTED_FOCUS_KEYS))}."
            )
        return list(dict.fromkeys(value))

    @field_validator("avoid_foods")
    @classmethod
    def _clean_foods(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [" ".join(row.split())[:80] for row in value if row and row.strip()]


class HydrationPreferencePatch(BaseModel):
    """Hydration reminders. No target volume — that would be a health instruction."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    remind_in_hot_weather_only: bool | None = None
    note: str | None = Field(default=None, max_length=240)


__all__ = ["NutritionPreferencePatch", "HydrationPreferencePatch"]
