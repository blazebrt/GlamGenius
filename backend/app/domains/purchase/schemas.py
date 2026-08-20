"""Strict request and AI contracts for prospective Care purchase candidates."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.purchase.candidate_truth import validate_care_candidate_details
from app.domains.purchase.fragrance_truth import validate_fragrance_candidate_details

AnyPurchaseCategory = Literal[
    "wardrobe", "shoes", "accessories", "beauty", "hair", "perfumes", "supplements"
]


class CarePurchaseItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # The request accepts all known categories so the API can return the
    # correct inactive/prohibited boundary for a non-Care item.  Only beauty
    # and hair are validated as Care candidate facts and can be persisted.
    category: AnyPurchaseCategory
    display_name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    subcategory: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    product_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _validate_details(self):
        if self.category in {"beauty", "hair"}:
            self.details = validate_care_candidate_details(self.category, self.details)
        elif self.category == "perfumes":
            self.details = validate_fragrance_candidate_details(self.details)
        return self


class FragrancePurchaseItemInput(CarePurchaseItemInput):
    """Typed manual input for a prospective Fragrance candidate."""

    category: Literal["perfumes"]


class PurchaseCandidateInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["manual", "screenshot", "item_photo"] = "manual"
    item: CarePurchaseItemInput | FragrancePurchaseItemInput | None = None
    media_asset_id: uuid.UUID | None = None
    client_mutation_id: str | None = Field(default=None, max_length=80)
    expected_category: AnyPurchaseCategory | None = None

    @model_validator(mode="after")
    def _one_source(self):
        if self.item is None and self.media_asset_id is None:
            raise ValueError("Send Care product details or a product photo to inspect.")
        if self.item is not None and self.media_asset_id is not None:
            raise ValueError("Send either Care product details or a product photo, not both.")
        if self.source == "manual" and self.item is None:
            raise ValueError("Manual Care inspection requires candidate details.")
        if self.source != "manual" and self.media_asset_id is None:
            raise ValueError("Photo Care inspection requires media_asset_id.")
        if self.expected_category and self.item is not None and self.item.category != self.expected_category:
            raise ValueError("The selected purchase category must match the candidate category.")
        return self


class ExtractedPurchaseCandidate(BaseModel):
    """Visible facts only; this schema cannot express a recommendation."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: AnyPurchaseCategory
    display_name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    subcategory: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    confidence: float = Field(ge=0.35, le=1)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=30)
    photo_quality_notes: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def _care_details_only(self):
        if self.category in {"beauty", "hair"}:
            self.details = validate_care_candidate_details(self.category, self.details)
        elif self.details:
            raise ValueError("Non-Care extraction must not contain Care candidate details.")
        else:
            self.details = {}
        return self


class ExtractedFragranceCandidate(BaseModel):
    """Visible Fragrance facts; intended use remains customer-declared."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: Literal["perfumes"]
    display_name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    subcategory: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] = Field(default_factory=dict)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    confidence: float = Field(ge=0.35, le=1)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=30)
    photo_quality_notes: str = Field(min_length=3, max_length=400)

    @model_validator(mode="after")
    def _visible_fragrance_details(self):
        self.details = validate_fragrance_candidate_details(self.details)
        return self


class CarePurchaseCandidateConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    subcategory: str | None = Field(default=None, max_length=80)
    details: dict[str, Any] | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    product_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _validate_details(self):
        if self.details is not None:
            # Category is immutable and supplied by the stored candidate; the
            # service revalidates this payload against that category.
            # The stored candidate category is immutable and is not part of
            # this body.  Accept the union here; the service dispatches strict
            # validation by that stored category, preserving Care rejection.
            unknown = set(self.details) - {
                "product_type", "size", "purpose", "ingredients_text", "active_ingredients",
                "fragrance_family", "concentration", "season", "occasion", "longevity_user_reported",
            }
            if unknown:
                raise ValueError(f"Unsupported Care purchase detail: {sorted(unknown)[0]}")
        return self


__all__ = [
    "AnyPurchaseCategory",
    "CarePurchaseCandidateConfirm",
    "CarePurchaseItemInput",
    "FragrancePurchaseItemInput",
    "ExtractedFragranceCandidate",
    "PurchaseCandidateInspectRequest",
    "ExtractedPurchaseCandidate",
]
