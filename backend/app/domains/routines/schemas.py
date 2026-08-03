"""Validated contracts for routine and shelf intelligence.

``extra="forbid"`` throughout. Ownership is never a field: an ``account_id`` in
a request body is a 422, and identity always comes from the signed token.

The AI response schemas at the bottom are deliberately narrow. The model is
given a decision that has already been made and asked to phrase it — there is
no field it could fill that would change what the engine decided, and every
free-text field is swept by :func:`app.domains.routines.safety.narrative_is_safe`
before it is stored or shown.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.routines.compiler import ROUTINE_KINDS
from app.domains.routines.nutrition import DIETS, NUTRIENT_BY_KEY
from app.domains.routines.ontology import ALL_SLOTS
from app.domains.routines.parser import SOURCE_EXTRACTED, SOURCE_LABEL, SOURCE_USER

SCHEMA_VERSION_ROUTINE_EXPLANATION = "routine-explanation-v1"
SCHEMA_VERSION_INGREDIENT_EXPLANATION = "ingredient-explanation-v1"

SHELF_CATEGORIES = ("beauty", "hair")
SLOT_KEYS = tuple(spec.key for spec in ALL_SLOTS)
INGREDIENT_SOURCES = (SOURCE_USER, SOURCE_LABEL, SOURCE_EXTRACTED)

CLIMATES = ("hot", "humid", "cold", "dry", "rainy")
TIMES_OF_DAY = ("morning", "afternoon", "evening", "night")
SEASONS = ("summer", "monsoon", "winter", "spring", "autumn")


# --- Shelf ------------------------------------------------------------------


class ShelfAnalyseRequest(BaseModel):
    """Re-read the user's own shelf and store what the engine concluded."""

    model_config = ConfigDict(extra="forbid")

    categories: List[Literal[SHELF_CATEGORIES]] = Field(  # type: ignore[valid-type]
        default_factory=lambda: list(SHELF_CATEGORIES), max_length=2,
    )
    climate: Optional[Literal[CLIMATES]] = None  # type: ignore[valid-type]
    # Overriding "today" is for tests and for a user in a different timezone. It
    # cannot reach further back than a year, so no run can rewrite old history.
    as_of: Optional[date] = None

    @field_validator("categories")
    @classmethod
    def _at_least_one(cls, value: List[str]) -> List[str]:
        if not value:
            raise ValueError("Choose at least one of: beauty, hair.")
        return list(dict.fromkeys(value))


# --- Ingredients -------------------------------------------------------------


class IngredientCheckRequest(BaseModel):
    """Check a label, a typed list, or products already owned.

    At least one of the three has to be present, otherwise there is nothing to
    check and an empty answer would look like a clean result.
    """

    model_config = ConfigDict(extra="forbid")

    label_text: Optional[str] = Field(default=None, max_length=8000)
    ingredients: List[str] = Field(default_factory=list, max_length=60)
    item_ids: List[uuid.UUID] = Field(default_factory=list, max_length=20)
    source: Literal[INGREDIENT_SOURCES] = SOURCE_USER  # type: ignore[valid-type]
    against_owned: bool = True
    explain: bool = False

    @field_validator("ingredients")
    @classmethod
    def _clean(cls, value: List[str]) -> List[str]:
        return [" ".join(row.split())[:120] for row in value if row and row.strip()]

    def has_input(self) -> bool:
        return bool(self.label_text or self.ingredients or self.item_ids)


class IngredientConfirmRequest(BaseModel):
    """Confirm or reject a low-confidence read on one of your own products.

    Nothing read at low confidence drives a warning until this happens.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    ingredient_keys: List[str] = Field(min_length=1, max_length=60)
    confirmed: bool = True


# --- Routines ----------------------------------------------------------------


class RoutineGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: List[Literal[ROUTINE_KINDS]] = Field(default_factory=list, max_length=5)  # type: ignore[valid-type]
    climate: Optional[Literal[CLIMATES]] = None  # type: ignore[valid-type]
    explain: bool = True
    as_of: Optional[date] = None

    @field_validator("kinds")
    @classmethod
    def _dedupe(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))


class RoutineStepComplete(BaseModel):
    """Mark a step done. Consistency, never a streak."""

    model_config = ConfigDict(extra="forbid")

    done_on: Optional[date] = None
    completed: bool = True
    note: Optional[str] = Field(default=None, max_length=240)


class ObservationInput(BaseModel):
    """Something the user noticed, in their own words.

    Stored verbatim and never interpreted. If it reads like a health question,
    the response carries the professional boundary rather than an answer.
    """

    model_config = ConfigDict(extra="forbid")

    observed_on: Optional[date] = None
    area: Literal["skin", "hair", "scalp", "nails", "general"] = "general"
    note: str = Field(min_length=1, max_length=500)
    item_id: Optional[uuid.UUID] = None


# --- Perfume -----------------------------------------------------------------


class PerfumeQuery(BaseModel):
    """Query parameters for a perfume suggestion, validated like a body."""

    model_config = ConfigDict(extra="forbid")

    occasion_key: Optional[str] = Field(default=None, max_length=32)
    weather: Optional[Literal[CLIMATES]] = None  # type: ignore[valid-type]
    time_of_day: Optional[Literal[TIMES_OF_DAY]] = None  # type: ignore[valid-type]
    season: Optional[Literal[SEASONS]] = None  # type: ignore[valid-type]


# --- Nutrition and hydration --------------------------------------------------


class NutritionPreferencePatch(BaseModel):
    """What this person eats. A constraint on suggestions, not a suggestion."""

    model_config = ConfigDict(extra="forbid")

    diet: Optional[Literal[DIETS]] = None  # type: ignore[valid-type]
    avoid_foods: Optional[List[str]] = Field(default=None, max_length=40)
    focus_nutrients: Optional[List[str]] = Field(default=None, max_length=12)
    enabled: Optional[bool] = None

    @field_validator("focus_nutrients")
    @classmethod
    def _known(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        unknown = [row for row in value if row not in NUTRIENT_BY_KEY]
        if unknown:
            raise ValueError(
                f"We have no food context for: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(NUTRIENT_BY_KEY))}."
            )
        return list(dict.fromkeys(value))

    @field_validator("avoid_foods")
    @classmethod
    def _clean_foods(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return [" ".join(row.split())[:80] for row in value if row and row.strip()]


class HydrationPreferencePatch(BaseModel):
    """Hydration reminders. No target volume — that would be a health instruction."""

    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    remind_in_hot_weather_only: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=240)


# --- Supplements --------------------------------------------------------------


class SupplementFlagAcknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_ids: List[uuid.UUID] = Field(min_length=1, max_length=50)


# --- What the model is allowed to return --------------------------------------
# Wording only. Every field here is prose about a decision already made, and
# there is no field the model could fill that changes a rule, a product choice,
# a severity or a step order.


class RoutineNarrative(BaseModel):
    """Better wording for one routine that has already been compiled."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=24)
    summary: str = Field(max_length=600)
    step_notes: Dict[str, str] = Field(default_factory=dict)

    @field_validator("step_notes")
    @classmethod
    def _bounded(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > 12:
            raise ValueError("Too many step notes.")
        return {key[:32]: text[:280] for key, text in value.items()}

    def texts(self) -> List[str]:
        return [self.summary, *self.step_notes.values()]


class RoutineExplanationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routines: List[RoutineNarrative] = Field(default_factory=list, max_length=5)


class IngredientNarrative(BaseModel):
    """Plain-English wording for one reviewed ingredient note.

    ``severity`` is not here on purpose. How serious something is comes from the
    reviewed rule row, never from the model.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(max_length=64)
    plain_english: str = Field(max_length=600)


class IngredientExplanationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: List[IngredientNarrative] = Field(default_factory=list, max_length=12)


__all__ = [
    "SCHEMA_VERSION_ROUTINE_EXPLANATION",
    "SCHEMA_VERSION_INGREDIENT_EXPLANATION",
    "SHELF_CATEGORIES",
    "CLIMATES",
    "ShelfAnalyseRequest",
    "IngredientCheckRequest",
    "IngredientConfirmRequest",
    "RoutineGenerateRequest",
    "RoutineStepComplete",
    "ObservationInput",
    "PerfumeQuery",
    "NutritionPreferencePatch",
    "HydrationPreferencePatch",
    "SupplementFlagAcknowledge",
    "RoutineNarrative",
    "RoutineExplanationResponse",
    "IngredientNarrative",
    "IngredientExplanationResponse",
]
