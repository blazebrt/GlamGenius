"""Validated contracts for billing, support and try-on.

``extra="forbid"`` throughout. Notice what is *absent* from every schema here:
there is no field by which a client can name a plan to be granted, an
entitlement to be created, an amount to be charged, or a payment to be treated
as successful. The only thing a client may say is *which offer it wants to
start paying for*; everything else comes from the catalogue and from a verified
webhook.
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.billing import catalogue
from app.domains.billing.operations import SUPPORT_CATEGORIES


class CheckoutRequest(BaseModel):
    """Start a payment.

    The amount is not a field. A client that could name a price could name
    zero.
    """

    model_config = ConfigDict(extra="forbid")

    offer_key: str = Field(min_length=1, max_length=48)
    # For an Event Pass: which occasion it is for.
    occasion_id: Optional[uuid.UUID] = None
    # Lets a retried tap reuse the same order instead of creating a second one.
    idempotency_key: Optional[str] = Field(default=None, max_length=80)

    @field_validator("offer_key")
    @classmethod
    def _known_offer(cls, value: str) -> str:
        if catalogue.offer_by_key(value) is None:
            available = ", ".join(row.key for row in catalogue.offers())
            raise ValueError(f"'{value}' is not something we sell. Available: {available}.")
        return value


class CheckoutCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID


class EventPassRequest(BaseModel):
    """Buy a pass for one occasion."""

    model_config = ConfigDict(extra="forbid")

    occasion_id: uuid.UUID
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


class SupportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[SUPPORT_CATEGORIES]  # type: ignore[valid-type]
    subject: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=4000)


class StylistReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["look", "outfit", "wardrobe", "routine", "occasion"]
    subject_id: Optional[uuid.UUID] = None
    question: str = Field(min_length=1, max_length=2000)


class TryOnRequest(BaseModel):
    """Kept so the route exists and 404s honestly while no provider is chosen."""

    model_config = ConfigDict(extra="forbid")

    source_media_id: uuid.UUID
    item_ids: list[uuid.UUID] = Field(default_factory=list, max_length=8)


__all__ = [
    "CheckoutRequest", "CheckoutCancel", "EventPassRequest",
    "SupportRequest", "StylistReviewRequest", "TryOnRequest",
]
