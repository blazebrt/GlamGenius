"""Validated API and AI contracts for inventory."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.inventory.taxonomy import CATEGORIES, validate_attribute_keys, validate_details

Category = Literal["wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements"]
VerificationState = Literal["draft", "confirmed", "rejected"]


class AttributeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    value: Any


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    subcategory: Optional[str] = Field(default=None, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    brand: Optional[str] = Field(default=None, max_length=120)
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    condition: str = Field(default="good", max_length=24)
    replacement_priority: str = Field(default="none", max_length=24)
    details: dict[str, Any] = Field(default_factory=dict)
    attributes: list[AttributeInput] = Field(default_factory=list, max_length=80)
    image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=12)
    client_mutation_id: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_category_details(self):
        self.details = validate_details(self.category, self.details)
        return self


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Optional[int] = Field(default=None, ge=1)
    subcategory: Optional[str] = Field(default=None, max_length=80)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    brand: Optional[str] = Field(default=None, max_length=120)
    purchase_date: Optional[date] = None
    purchase_price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    condition: Optional[str] = Field(default=None, max_length=24)
    replacement_priority: Optional[str] = Field(default=None, max_length=24)
    details: dict[str, Any] = Field(default_factory=dict)
    attributes: list[AttributeInput] = Field(default_factory=list, max_length=80)
    image_ids: Optional[list[uuid.UUID]] = Field(default=None, max_length=12)


class UsageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    used_on: date = Field(default_factory=date.today)
    quantity: int = Field(default=1, ge=1, le=100)
    note: Optional[str] = Field(default=None, max_length=240)


class ConditionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Literal["excellent", "good", "fair", "worn", "needs_attention"]
    note: Optional[str] = Field(default=None, max_length=240)


class DuplicateResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["keep_both", "not_duplicate", "merge"]
    canonical_item_id: Optional[uuid.UUID] = None


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_asset_id: uuid.UUID
    category_hint: Optional[Category] = None
    capture_type: Literal["item_photo", "screenshot", "shelf_photo", "wardrobe_photo", "wardrobe_video"] = "item_photo"


class ExtractedAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    value: Any
    confidence: float = Field(ge=0.35, le=1)


class ExtractedInventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    subcategory: Optional[str] = Field(default=None, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    brand: Optional[str] = Field(default=None, max_length=120)
    confidence: float = Field(ge=0.35, le=1)
    details: dict[str, Any] = Field(default_factory=dict)
    attributes: list[ExtractedAttribute] = Field(default_factory=list, max_length=50)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=50)
    photo_quality_notes: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def validate_extracted_details(self):
        if self.category not in CATEGORIES:
            raise ValueError("Unsupported category")
        self.details = validate_details(self.category, self.details)
        validate_attribute_keys(self.category, (row.key for row in self.attributes))
        return self
