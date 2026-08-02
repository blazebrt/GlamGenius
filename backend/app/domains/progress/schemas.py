"""Validated contracts for progress, goals and memory.

``extra="forbid"`` throughout, so an injected ``account_id`` is a 422 rather
than something that might quietly be trusted. Identity always comes from the
signed token.

The photo schema is worth a note: the conditions a photo was taken in are
**required** fields, not optional ones. A photo without them cannot be used in
a comparison, and accepting it with defaults would mean quietly guessing —
which is how a fake before-and-after gets made.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.progress import comparison, memory, registry

GOAL_KINDS = ("no_buy", "use_up", "routine", "wardrobe", "custom")
PERIODS = ("week", "month")


# --- Progress -------------------------------------------------------------------


class ProgressQuery(BaseModel):
    """Query parameters for the progress overview, validated like a body."""

    model_config = ConfigDict(extra="forbid")

    period: Literal[PERIODS] = "week"  # type: ignore[valid-type]
    as_of: Optional[date] = None


class SelfReport(BaseModel):
    """How the user says they felt. The only source for that metric.

    A 1-5 scale the person chooses. Nothing about a photo, an outfit or a
    purchase ever writes to this.
    """

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    note: Optional[str] = Field(default=None, max_length=500)
    recorded_on: Optional[date] = None


# --- Photos and comparison ---------------------------------------------------------


class ProgressPhotoInput(BaseModel):
    """A photo kept for comparison, with the conditions it was taken in.

    Every condition is required. A photo we would have to guess about is a
    photo we cannot honestly compare.
    """

    model_config = ConfigDict(extra="forbid")

    media_id: uuid.UUID
    body_area: Literal[comparison.BODY_AREAS]  # type: ignore[valid-type]
    lighting: Literal[comparison.LIGHTING]  # type: ignore[valid-type]
    angle: Literal[comparison.ANGLES]  # type: ignore[valid-type]
    framing: Literal[comparison.FRAMING]  # type: ignore[valid-type]
    taken_on: Optional[date] = None
    time_of_day: Optional[Literal[comparison.TIMES_OF_DAY]] = None  # type: ignore[valid-type]
    note: Optional[str] = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def _not_in_the_future(self):
        if self.taken_on and self.taken_on > date.today():
            raise ValueError("A photo cannot have been taken in the future.")
        return self


class ComparisonQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_area: Literal[comparison.BODY_AREAS] = "face"  # type: ignore[valid-type]


# --- Goals ---------------------------------------------------------------------------


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[GOAL_KINDS]  # type: ignore[valid-type]
    title: str = Field(min_length=1, max_length=160)
    metric_key: Optional[str] = Field(default=None, max_length=48)
    target_value: Optional[float] = None
    starts_on: Optional[date] = None
    target_date: Optional[date] = None
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("metric_key")
    @classmethod
    def _known_metric(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in registry.METRIC_BY_KEY:
            raise ValueError(
                f"'{value}' is not a metric we track. Choose from: {', '.join(registry.METRIC_KEYS)}."
            )
        return value

    @model_validator(mode="after")
    def _target_after_start(self):
        start = self.starts_on or date.today()
        if self.target_date and self.target_date < start:
            raise ValueError("The target date cannot be before the goal starts.")
        return self


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=160)
    target_value: Optional[float] = None
    target_date: Optional[date] = None
    status: Optional[Literal["active", "paused", "achieved", "abandoned"]] = None
    note: Optional[str] = Field(default=None, max_length=500)
    # A progress note the user records themselves, for goals with no metric.
    progress_value: Optional[float] = None
    progress_note: Optional[str] = Field(default=None, max_length=500)


# --- Memory -----------------------------------------------------------------------------


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Optional[Literal[memory.CATEGORIES]] = None  # type: ignore[valid-type]
    include_deleted: bool = False


class MemoryPatch(BaseModel):
    """Edit or confirm one remembered fact.

    A correction from the user outranks anything the app inferred — they are
    the authority on themselves — so a corrected fact goes to full confidence.
    """

    model_config = ConfigDict(extra="forbid")

    fact: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    verification_state: Optional[Literal[memory.VERIFICATION_STATES]] = None  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _something_to_do(self):
        if self.fact is None and self.verification_state is None:
            raise ValueError("Give us either new wording or a verification state.")
        return self


class MemoryCategoryPatch(BaseModel):
    """Switch a whole category of memory on or off.

    Off means we stop writing it *and* stop reading it, without destroying what
    is already there — so it can be switched back on later intact.
    """

    model_config = ConfigDict(extra="forbid")

    category: Literal[memory.CATEGORIES]  # type: ignore[valid-type]
    enabled: bool


class FeedbackInput(BaseModel):
    """A reaction to something the app suggested."""

    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["look", "product", "routine_step", "purchase", "colour", "occasion"]
    subject_id: Optional[uuid.UUID] = None
    signal: Literal["liked", "rejected", "wore_it", "not_for_me", "returned", "complimented"]
    reason: Optional[str] = Field(default=None, max_length=240)
    # What the feedback is about in words, used to form the memory fact.
    subject_label: Optional[str] = Field(default=None, max_length=200)


__all__ = [
    "GOAL_KINDS", "PERIODS",
    "ProgressQuery", "SelfReport", "ProgressPhotoInput", "ComparisonQuery",
    "GoalCreate", "GoalPatch",
    "MemoryQuery", "MemoryPatch", "MemoryCategoryPatch", "FeedbackInput",
]
