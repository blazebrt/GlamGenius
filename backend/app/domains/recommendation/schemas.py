"""Validated contracts for the Phase 4 decision engine.

Two families live here:

* **Request models** — what a caller may send. ``extra="forbid"`` throughout, so
  a stray ``account_id`` or ``user_id`` in a body is a 422 rather than something
  that might quietly be trusted.
* **AI output models** — what a model is allowed to say. These are deliberately
  narrow. The model never names an item, never picks an item and never returns
  a verdict; it writes prose about a decision that has already been made
  deterministically, keyed by a slot or factor the orchestrator supplied. That
  is the whole reason a bad model response can degrade the wording of a look
  but can never invent one.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.recommendation.occasions import (
    COMFORT_LEVELS, DRESS_CODES, OCCASION_KEYS, SETTINGS, TIMES_OF_DAY, WEATHER_CONDITIONS, get_occasion,
)

SCHEMA_VERSION_LOOK_EXPLANATION = "look_explanation.v1"
SCHEMA_VERSION_SHOPPING_ITEM = "shopping_candidate.v1"
SCHEMA_VERSION_PURCHASE_EXPLANATION = "purchase_explanation.v1"

# --- Occasions --------------------------------------------------------------


class OccasionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occasion_key: str = Field(min_length=1, max_length=32)
    title: Optional[str] = Field(default=None, max_length=160)
    event_date: Optional[date] = None
    time_of_day: Optional[Literal[TIMES_OF_DAY]] = None  # type: ignore[valid-type]
    location: Optional[str] = Field(default=None, max_length=160)
    setting: Optional[Literal[SETTINGS]] = None  # type: ignore[valid-type]
    dress_code: Optional[str] = Field(default=None, max_length=32)
    weather: Optional[Literal[WEATHER_CONDITIONS]] = None  # type: ignore[valid-type]
    comfort_preference: Optional[Literal[COMFORT_LEVELS]] = None  # type: ignore[valid-type]
    optional_budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    preparation_time: Optional[str] = Field(default=None, max_length=48)
    notes: Optional[str] = Field(default=None, max_length=500)

    @field_validator("occasion_key")
    @classmethod
    def _known_occasion(cls, value: str) -> str:
        get_occasion(value)  # raises ValueError naming the supported options
        return value

    @field_validator("dress_code")
    @classmethod
    def _known_dress_code(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in DRESS_CODES:
            raise ValueError(f"'{value}' is not a dress code we support. Choose one of: {', '.join(DRESS_CODES)}.")
        return value


class OccasionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=160)
    event_date: Optional[date] = None
    time_of_day: Optional[Literal[TIMES_OF_DAY]] = None  # type: ignore[valid-type]
    location: Optional[str] = Field(default=None, max_length=160)
    setting: Optional[Literal[SETTINGS]] = None  # type: ignore[valid-type]
    dress_code: Optional[str] = Field(default=None, max_length=32)
    weather: Optional[Literal[WEATHER_CONDITIONS]] = None  # type: ignore[valid-type]
    comfort_preference: Optional[Literal[COMFORT_LEVELS]] = None  # type: ignore[valid-type]
    optional_budget: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    preparation_time: Optional[str] = Field(default=None, max_length=48)
    notes: Optional[str] = Field(default=None, max_length=500)
    status: Optional[Literal["active", "archived"]] = None

    @field_validator("dress_code")
    @classmethod
    def _known_dress_code(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in DRESS_CODES:
            raise ValueError(f"'{value}' is not a dress code we support. Choose one of: {', '.join(DRESS_CODES)}.")
        return value


# --- Styling ----------------------------------------------------------------


class StyleForOccasion(BaseModel):
    """Ask for looks. Either name a saved occasion, or describe a new one."""

    model_config = ConfigDict(extra="forbid")

    occasion_id: Optional[uuid.UUID] = None
    occasion: Optional[OccasionCreate] = None
    preferred_item_ids: List[uuid.UUID] = Field(default_factory=list, max_length=12)
    client_mutation_id: Optional[str] = Field(default=None, max_length=80)

    @field_validator("occasion")
    @classmethod
    def _one_of_two(cls, value: Optional[OccasionCreate], info) -> Optional[OccasionCreate]:
        if value is None and not info.data.get("occasion_id"):
            raise ValueError("Send either occasion_id for a saved occasion, or occasion to describe a new one.")
        return value


class LookRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Optional[Literal["too_formal", "too_casual", "too_warm", "too_cold", "not_my_style", "want_bolder", "want_simpler"]] = None
    note: Optional[str] = Field(default=None, max_length=400)
    avoid_item_ids: List[uuid.UUID] = Field(default_factory=list, max_length=12)


class LookSwapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: Literal["clothing", "shoes", "accessories", "perfume", "hair", "grooming"]
    from_item_id: Optional[uuid.UUID] = None
    to_item_id: Optional[uuid.UUID] = None
    note: Optional[str] = Field(default=None, max_length=400)


class LookFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["loved", "worn", "saved", "not_for_me"]
    reason: Optional[Literal["too_formal", "too_casual", "wrong_weather", "not_my_style", "uncomfortable", "great_fit", "other"]] = None
    note: Optional[str] = Field(default=None, max_length=500)
    worn_on: Optional[date] = None


# --- Shopping ---------------------------------------------------------------


class ShoppingItemInput(BaseModel):
    """What the user tells us about the thing they are considering."""

    model_config = ConfigDict(extra="forbid")

    category: Literal["wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements"]
    display_name: str = Field(min_length=1, max_length=200)
    brand: Optional[str] = Field(default=None, max_length=120)
    subcategory: Optional[str] = Field(default=None, max_length=80)
    colour: Optional[str] = Field(default=None, max_length=80)
    size: Optional[str] = Field(default=None, max_length=40)
    fabric: Optional[str] = Field(default=None, max_length=100)
    fit: Optional[str] = Field(default=None, max_length=80)
    formality: Optional[str] = Field(default=None, max_length=80)
    occasion_tags: List[str] = Field(default_factory=list, max_length=20)
    season_tags: List[str] = Field(default_factory=list, max_length=8)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    product_url: Optional[str] = Field(default=None, max_length=2048)


class ShoppingEvaluateRequest(BaseModel):
    """Evaluate a screenshot, a photo, or details typed by hand.

    ``media_asset_id`` must be an asset this caller owns; ownership is checked
    against the media domain, never assumed from the id.
    """

    model_config = ConfigDict(extra="forbid")

    media_asset_id: Optional[uuid.UUID] = None
    item: Optional[ShoppingItemInput] = None
    source: Literal["screenshot", "item_photo", "manual"] = "manual"
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    product_url: Optional[str] = Field(default=None, max_length=2048)
    occasion_key: Optional[str] = Field(default=None, max_length=32)
    client_mutation_id: Optional[str] = Field(default=None, max_length=80)

    @field_validator("occasion_key")
    @classmethod
    def _known_occasion(cls, value: Optional[str]) -> Optional[str]:
        if value:
            get_occasion(value)
        return value

    @field_validator("item")
    @classmethod
    def _need_something(cls, value: Optional[ShoppingItemInput], info) -> Optional[ShoppingItemInput]:
        if value is None and not info.data.get("media_asset_id"):
            raise ValueError("Send a photo or screenshot to read, or the item details to evaluate.")
        return value


class PurchaseDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["bought", "waiting", "skipped"]
    note: Optional[str] = Field(default=None, max_length=500)


# --- AI output contracts ----------------------------------------------------
# Nothing below chooses anything. Each model writes prose about a decision the
# deterministic engine already made, addressed by an opaque key the orchestrator
# handed it. A response that references an unknown key is discarded.


class _AIBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LookNarrative(_AIBlock):
    variant: Literal["recommended", "comfortable", "expressive"]
    why_it_works: str = Field(min_length=10, max_length=700)
    weather_note: str = Field(default="", max_length=400)
    dress_code_note: str = Field(default="", max_length=400)
    preparation_steps: List[str] = Field(default_factory=list, max_length=8)

    @field_validator("preparation_steps")
    @classmethod
    def _bounded_steps(cls, value: List[str]) -> List[str]:
        return [step.strip()[:200] for step in value if step and step.strip()]


class LookExplanationResponse(BaseModel):
    """Wording for looks that already exist. It cannot add or remove one."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    looks: List[LookNarrative] = Field(min_length=1, max_length=3)


class ExtractedShoppingItem(BaseModel):
    """What a model may read off a product screenshot."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: Literal["wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements"]
    display_name: str = Field(min_length=1, max_length=200)
    brand: Optional[str] = Field(default=None, max_length=120)
    subcategory: Optional[str] = Field(default=None, max_length=80)
    colour: Optional[str] = Field(default=None, max_length=80)
    size: Optional[str] = Field(default=None, max_length=40)
    fabric: Optional[str] = Field(default=None, max_length=100)
    fit: Optional[str] = Field(default=None, max_length=80)
    formality: Optional[str] = Field(default=None, max_length=80)
    occasion_tags: List[str] = Field(default_factory=list, max_length=20)
    season_tags: List[str] = Field(default_factory=list, max_length=8)
    # Price is read only when it is printed on the screenshot. A missing price
    # stays missing; the ROI model has an explicit branch for that.
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    confidence: float = Field(ge=0.35, le=1)
    uncertain_fields: List[str] = Field(default_factory=list, max_length=30)
    photo_quality_notes: str = Field(min_length=3, max_length=400)


class PurchaseNarrative(BaseModel):
    """Wording for a verdict that has already been computed.

    The headline is deliberately not asked for. It states Buy, Wait or Skip, so
    it is generated from the computed verdict and can never disagree with it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    summary: str = Field(min_length=20, max_length=800)


# --- Language safety --------------------------------------------------------
# Phase 1 removed diagnostic language; Phase 4 adds a second class of wording
# that must never appear. A narrative containing any of these is discarded and
# the deterministic text is used instead — the look itself is unaffected,
# because the look was never the model's to decide.
BANNED_NARRATIVE_TERMS = (
    "money wasted", "wasted money", "flattering for your body type", "slimming",
    "hide your", "hides your", "camouflage your", "problem area", "problem areas",
    "flaw", "flaws", "figure flaw", "too fat", "too thin", "overweight",
    "underweight", "lose weight", "gain weight", "body type", "flatter your figure",
    "diagnos", "disease", "disorder", "prescri", "treatment for", "cure",
    "attractiveness score", "beauty score", "rate your looks",
)


def narrative_is_safe(text: str) -> bool:
    lowered = (text or "").lower()
    return not any(term in lowered for term in BANNED_NARRATIVE_TERMS)
