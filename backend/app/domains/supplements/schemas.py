"""Strict API contracts for structured supplement label facts."""
from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LabelComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_name: str = Field(min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=32)
    serving_text: str | None = Field(default=None, max_length=160)
    source: str = Field(default="user_declared", pattern="^(user_declared|photo_extracted)$")
    verification_state: str = Field(default="confirmed", pattern="^(draft|confirmed)$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_ai_run_id: uuid.UUID | None = None
    model_version: str | None = Field(default=None, max_length=64)
    prompt_version: str | None = Field(default=None, max_length=32)
    client_mutation_id: str | None = Field(default=None, max_length=80)


class LabelComponentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_name: str | None = Field(default=None, min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=32)
    serving_text: str | None = Field(default=None, max_length=160)
    verification_state: str | None = Field(default=None, pattern="^(draft|confirmed)$")
    confidence: float | None = Field(default=None, ge=0, le=1)


class LabelComponentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = True
