"""Validated API and AI contracts for inventory."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

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
    subcategory: str | None = Field(default=None, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    condition: str = Field(default="good", max_length=24)
    replacement_priority: str = Field(default="none", max_length=24)
    details: dict[str, Any] = Field(default_factory=dict)
    attributes: list[AttributeInput] = Field(default_factory=list, max_length=80)
    image_ids: list[uuid.UUID] = Field(default_factory=list, max_length=12)
    client_mutation_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_category_details(self):
        self.details = validate_details(self.category, self.details)
        return self


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int | None = Field(default=None, ge=1)
    subcategory: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
    purchase_date: date | None = None
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    condition: str | None = Field(default=None, max_length=24)
    replacement_priority: str | None = Field(default=None, max_length=24)
    details: dict[str, Any] = Field(default_factory=dict)
    attributes: list[AttributeInput] = Field(default_factory=list, max_length=80)
    image_ids: list[uuid.UUID] | None = Field(default=None, max_length=12)


class UsageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    used_on: date = Field(default_factory=date.today)
    quantity: int = Field(default=1, ge=1, le=100)
    note: str | None = Field(default=None, max_length=240)


class ConditionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    condition: Literal["excellent", "good", "fair", "worn", "needs_attention"]
    note: str | None = Field(default=None, max_length=240)


class DuplicateResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution: Literal["keep_both", "not_duplicate", "merge"]
    canonical_item_id: uuid.UUID | None = None


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    media_asset_id: uuid.UUID
    category_hint: Category | None = None
    capture_type: Literal["item_photo", "screenshot", "shelf_photo", "wardrobe_photo", "wardrobe_video"] = "item_photo"


#: The most candidates one photo may yield. A shelf holds more than this in
#: theory; in practice a photo that shows forty things shows none of them
#: legibly, and a review list nobody can finish is worse than a second photo.
BATCH_ITEM_LIMIT = 25


class BatchExtractRequest(BaseModel):
    """One photo of a shelf, a counter or a drawer."""

    model_config = ConfigDict(extra="forbid")
    media_asset_id: uuid.UUID
    category_hint: Category | None = None
    capture_type: Literal["shelf_photo", "wardrobe_photo", "counter_photo"] = "shelf_photo"


class CandidateDecision(BaseModel):
    """One tap. ``accept`` is the whole decision."""

    model_config = ConfigDict(extra="forbid")
    candidate_id: uuid.UUID
    accept: bool


class BatchDecisions(BaseModel):
    """Several taps, sent together.

    The person still decides one item at a time; this only spares the phone a
    request per tap when the taps come faster than the network.
    """

    model_config = ConfigDict(extra="forbid")
    decisions: list[CandidateDecision] = Field(min_length=1, max_length=BATCH_ITEM_LIMIT)


class ExtractedAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=64)
    value: Any
    confidence: float = Field(ge=0.35, le=1)


class ExtractedInventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    subcategory: str | None = Field(default=None, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    brand: str | None = Field(default=None, max_length=120)
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


class ExtractedInventoryBatch(BaseModel):
    """What one shelf photo may yield.

    ``items`` reuses the single-item contract exactly, so a candidate and a
    single extraction validate against the same rules — the same categories,
    the same detail fields, the same honest confidence.
    """

    model_config = ConfigDict(extra="forbid")
    items: list[ExtractedInventoryItem] = Field(default_factory=list, max_length=BATCH_ITEM_LIMIT)
    photo_quality_notes: str = Field(min_length=3, max_length=400)
    #: Things visible in the photo that could not be identified. Stated rather
    #: than guessed at, so the person knows the list is not the whole shelf.
    unreadable_count: int = Field(default=0, ge=0, le=50)
