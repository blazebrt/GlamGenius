"""Strict API contracts for structured supplement label facts."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class LabelComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_name: str = Field(min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=32)
    serving_text: str | None = Field(default=None, max_length=160)
    client_mutation_id: str | None = Field(default=None, max_length=80)


class LabelComponentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_name: str | None = Field(default=None, min_length=1, max_length=160)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=6)
    unit: str | None = Field(default=None, max_length=32)
    serving_text: str | None = Field(default=None, max_length=160)


class LabelComponentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = True
