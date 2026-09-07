"""Reading a skin-care ingredient label from a photo.

Deliberately **not** an extension of :mod:`app.domains.product.extraction`.
That module transcribes a packaged *food* pack: its schema carries nutrition
per 100 g, an FSSAI licence, an allergen declaration, a veg mark and an MRP
clause, and its prompt asks the model for a packaged food label. None of those
exist on a moisturiser, and widening the food schema until it also fits a
cosmetic would leave one prompt asking for two incompatible things and one
schema in which most fields are meaningless whichever photo arrives. Food
capture stays food capture; this is its own bounded path.

**The model transcribes. It does not decide anything.** It may not say the
product is suitable, may not judge an ingredient, may not infer a
concentration, may not infer efficacy, and may not add an ingredient it cannot
see. Every judgement in this product is made later, by the governed Step 7/8
chain, from reviewed evidence, against facts a person confirmed.

**The model is also not asked what kind of product this is.** The category is
not an output of this module at all — see
:mod:`app.domains.product.care_capture` and
``docs/architecture/SKIN_CARE_LABEL_CAPTURE.md``.

The photo goes to the gateway and nowhere else. Nothing here writes image
bytes, whole, truncated or hashed.
"""
from __future__ import annotations

import base64
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.media import service as media_service

FEATURE = "skin_care_label_transcribe"
PROMPT_VERSION = "skin-care-label.v1"
SCHEMA_VERSION = "skin-care-label.v1"

#: Every schema a stored skin-care transcription may carry and still be
#: confirmable.
#:
#: One entry today, because there has only ever been one. The set exists rather
#: than an equality check because the food path already learned the lesson: a
#: person can photograph a label, walk to the till, and tap confirm after a
#: deployment has happened in between, and refusing that review because the
#: schema moved under them loses a capture they already did the work for. New
#: transcriptions are only ever produced at :data:`SCHEMA_VERSION`.
CONFIRMABLE_SCHEMA_VERSIONS: frozenset[str] = frozenset({"skin-care-label.v1"})

SYSTEM = """You transcribe visible facts from one skin-care product label photograph.

Use only text visibly present in the image.

Never:
- recommend the product
- decide Buy / Wait / Skip
- diagnose a condition
- say the product is safe or unsafe
- say an ingredient is good or bad
- infer an ingredient that is not visible
- infer a concentration
- infer efficacy
- infer treatment benefit
- classify a medical condition
- add ingredients from general product knowledge

Copy the ingredient list in printed order.

If text is unreadable, omit it and report the field as uncertain."""


class ExtractedSkinCareLabel(BaseModel):
    """What a skin-care label photo may yield. Every field is printed on the pack.

    There is no category field, and there is no field the model could use to
    say anything about the product beyond what the pack itself says. That is
    the boundary, expressed as a schema rather than as a hope about the prompt:
    a model that decides to volunteer a verdict has nowhere to put it, and
    ``extra="forbid"`` turns the attempt into a validation failure.

    Declared active percentages are deliberately absent in V1. Transcribing one
    is not the hard part; the hard part is that a percentage is only meaningful
    next to reviewed evidence about that substance at that strength, and none
    of that exists yet. Storing the number early would invite a later layer to
    read it as though it had been reviewed.
    """

    model_config = ConfigDict(extra="forbid")

    product_name: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=160)
    #: What the pack calls itself: "Moisturising Cream", "Face Wash". A
    #: transcription of printed words, never a taxonomy this product assigns.
    product_type: str | None = Field(default=None, max_length=120)
    #: The one essential analytical field. Everything the governed chain can
    #: eventually say about this product is read from here.
    ingredients_text: str | None = Field(default=None, max_length=4000)

    confidence: float | None = Field(default=None, ge=0, le=1)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=24)
    photo_quality_notes: str | None = Field(default=None, max_length=400)

    @field_validator("uncertain_fields")
    @classmethod
    def _bounded_uncertainty(cls, value: list[str]) -> list[str]:
        if any(len(item) > 80 for item in value):
            raise ValueError("uncertain field names are too long")
        return value


def prompt() -> str:
    return """Transcribe this skin-care product label.
Return one JSON object with product_name, brand, product_type, ingredients_text,
confidence, uncertain_fields and photo_quality_notes.
Copy the ingredient list exactly as printed, in the printed order, including
punctuation and spelling. Copy product_type only from words printed on the pack.
Omit anything you cannot read clearly and name it in uncertain_fields."""


async def transcribe_label(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    media_asset_id: uuid.UUID,
) -> gateway.AIResult[ExtractedSkinCareLabel]:
    """Transcribe one owned skin-care label photo.

    The image goes to the gateway and nowhere else, exactly as the food, scan
    and purchase paths do. Ownership of the media asset is checked by the media
    domain before a single byte is read.
    """
    asset = await media_service.get_owned_asset(
        session, account_id=account_id, asset_id=media_asset_id,
    )
    data = await media_service.read_bytes(asset)
    return await gateway.run_structured(
        feature=FEATURE,
        prompt=prompt(),
        system=SYSTEM,
        schema=ExtractedSkinCareLabel,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        account_id_str=account_id_str,
        image_base64=base64.b64encode(data).decode("ascii"),
    )


__all__ = [
    "CONFIRMABLE_SCHEMA_VERSIONS",
    "FEATURE",
    "PROMPT_VERSION",
    "SCHEMA_VERSION",
    "SYSTEM",
    "ExtractedSkinCareLabel",
    "prompt",
    "transcribe_label",
]
