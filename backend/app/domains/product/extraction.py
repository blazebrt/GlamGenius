"""Reading a food label from a photo.

Extends the Care and fragrance extraction pipeline rather than repeating it:
same gateway, same structured-output contract, same rule that the model
transcribes what is visible and nothing else. Only the schema and the wording
differ, because a food pack carries different things from a moisturiser.

The boundary is the point. This prompt may not decide whether the food is good,
may not infer a nutrient that is not printed, and may not suggest anything.
Judging comes later, from our own thresholds, against facts a person confirmed.
"""
from __future__ import annotations

import base64
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.media import service as media_service

PROMPT_VERSION = "scan-label.v2"
SCHEMA_VERSION = "scan-label.v2"
FEATURE = "product_label_transcribe"

#: Every schema a stored transcription may carry and still be confirmable.
#:
#: A person can photograph a label, walk to the till, and tap confirm after a
#: deployment has happened in between. Refusing that review because the schema
#: moved under them would lose a capture they already did the work for, so v1
#: stays acceptable: ``mrp_text`` is optional, and a v1 payload simply lacks it.
#: New transcriptions are only ever produced at :data:`SCHEMA_VERSION`.
CONFIRMABLE_SCHEMA_VERSIONS: frozenset[str] = frozenset({"scan-label.v1", "scan-label.v2"})

# Deliberately close in shape to purchase/extraction.py's SYSTEM prompt: the
# same boundary, stated for a food pack.
SYSTEM = """You transcribe visible facts from one photograph of a packaged food label.
Use only text visibly printed on the pack. Never diagnose, never infer a nutrient that
is not printed, never estimate a value you cannot read, never judge whether the food is
healthy or unhealthy, and never recommend eating or avoiding it. Do not describe the
food as good, bad, better, worse, clean, junk, or any similar word. Do not comment on
who should eat it. If a field is unreadable, omit it and say so in uncertain_fields
rather than guessing. Copy numbers exactly as printed, including their units.

Never state or estimate a price. The only money you may transcribe is an explicit
printed maximum-retail-price declaration, copied word for word into mrp_text. Do not
decide whether anything is cheap, expensive, affordable or good value."""


class ExtractedLabel(BaseModel):
    """What a label photo may yield. Every field is something printed on a pack."""

    model_config = ConfigDict(extra="forbid")

    product_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=160)
    ingredients_text: str | None = Field(default=None, max_length=4000)
    #: Nutrition exactly as printed: {"energy_kcal": "480", "sugars_g": "22.5"}.
    nutrition_per_100g: dict[str, str] | None = Field(default=None, max_length=24)
    nutrition_basis: Literal["per_100g", "per_100ml"] | None = None
    serving_size: str | None = Field(default=None, max_length=80)
    net_quantity: str | None = Field(default=None, max_length=80)
    fssai_licence: str | None = Field(default=None, max_length=20)
    batch_number: str | None = Field(default=None, max_length=80)
    #: The MRP clause exactly as printed: "MRP ₹120", "M.R.P. Rs. 99.00".
    #:
    #: A transcription, never a judgement and never a number the model chose.
    #: The clause is copied whole and parsed deterministically later, so a
    #: reviewer can compare what is stored against what the pack says. It is
    #: optional in both directions: a v1 payload predating this field validates
    #: without it, and a pack whose price cannot be read produces no value.
    mrp_text: str | None = Field(default=None, max_length=120)
    veg_mark: str | None = Field(default=None, max_length=24)
    allergen_text: str | None = Field(default=None, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=24)
    photo_quality_notes: str | None = Field(default=None, max_length=400)

    @field_validator("nutrition_per_100g")
    @classmethod
    def _bounded_nutrition(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for key, item in value.items():
            if len(key) > 64 or len(item) > 64:
                raise ValueError("nutrition entries are too long")
        return value

    @field_validator("uncertain_fields")
    @classmethod
    def _bounded_uncertainty(cls, value: list[str]) -> list[str]:
        if any(len(item) > 80 for item in value):
            raise ValueError("uncertain field names are too long")
        return value


def prompt() -> str:
    return """Transcribe this packaged food label.
Return one JSON object with product_name, brand, ingredients_text,
nutrition_per_100g, nutrition_basis, serving_size, net_quantity, fssai_licence, batch_number, veg_mark,
allergen_text, mrp_text, confidence, uncertain_fields and photo_quality_notes.
Copy the nutrition table exactly as printed. Set nutrition_basis to per_100g only
when "per 100 g" is visibly printed, or per_100ml only when "per 100 ml" is visibly
printed. If the basis is absent or unreadable, omit it and include nutrition_basis
in uncertain_fields; never infer it from the product name. Copy the ingredient list
in the order printed. The FSSAI licence is a
fourteen-digit number, usually near the words Lic. No.
Only populate mrp_text when the photograph clearly contains an explicit MRP, M.R.P. or
Maximum Retail Price declaration, and then copy that whole clause exactly as printed,
including the currency symbol. A rupee symbol beside a number is not enough on its own,
and an offer price, a selling price or a shelf price is not an MRP. If no such
declaration is legible, omit mrp_text and include it in uncertain_fields.
Omit anything you cannot read clearly and name it in uncertain_fields."""


async def transcribe_label(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    media_asset_id: uuid.UUID,
) -> gateway.AIResult[ExtractedLabel]:
    """Transcribe one owned label photo. The image goes to the gateway and nowhere else.

    The photo is read and dropped, exactly as the scan and purchase paths do:
    nothing here writes image bytes, whole, truncated or hashed.
    """
    asset = await media_service.get_owned_asset(
        session, account_id=account_id, asset_id=media_asset_id,
    )
    data = await media_service.read_bytes(asset)
    return await gateway.run_structured(
        feature=FEATURE,
        prompt=prompt(),
        system=SYSTEM,
        schema=ExtractedLabel,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        account_id_str=account_id_str,
        image_base64=base64.b64encode(data).decode("ascii"),
    )
