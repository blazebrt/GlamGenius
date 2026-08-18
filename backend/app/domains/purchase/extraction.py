"""Visible-facts extraction for prospective Care purchase candidates.

Unlike inventory extraction, this module never creates an InventoryItem draft.
The gateway result is only candidate metadata awaiting explicit user review.
"""
from __future__ import annotations

import base64
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.media import service as media_service
from app.domains.purchase.schemas import ExtractedPurchaseCandidate

PROMPT_VERSION = "v3-05.1"
SCHEMA_VERSION = "v3-05.1"
FEATURE = "care_purchase_candidate_extract"

SYSTEM = """You transcribe visible facts from one product image for a prospective purchase review.
Use only text or product facts visibly present in the image. Never diagnose, infer a
condition, claim efficacy, decide whether the product is suitable, or recommend
Buy/Wait/Skip. Never invent ingredients, active ingredients, product type, purpose,
price, or brand. Mark uncertain fields explicitly and keep confidence honest.
Identify the actual category when possible: wardrobe, shoes, accessories, beauty,
hair, perfumes, or supplements. For supplements, label information only; never
provide dosage, interaction, pregnancy, treatment, or medical advice. Do not return
seller, review, popularity, ratings, or compatibility information."""


def prompt() -> str:
    return """Inspect this single prospective purchase product image.
Return one JSON object with category, display_name, brand, subcategory, details,
price, currency, confidence, uncertain_fields, and photo_quality_notes.
For beauty or hair, details may contain only product_type, size, purpose,
ingredients_text, and active_ingredients. For every other category details must be
an empty object. Omit any fact that is not visible; do not infer it."""


async def extract_purchase_candidate(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    media_asset_id: uuid.UUID,
):
    asset = await media_service.get_owned_asset(
        session, account_id=account_id, asset_id=media_asset_id
    )
    data = await media_service.read_bytes(asset)
    return await gateway.run_structured(
        feature=FEATURE,
        prompt=prompt(),
        system=SYSTEM,
        schema=ExtractedPurchaseCandidate,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        account_id_str=account_id_str,
        image_base64=base64.b64encode(data).decode("ascii"),
    )


# A short alias makes the route seam easy to stub without exposing inventory's
# ``analyse`` function or accidentally creating owned-product rows.
extract = extract_purchase_candidate


__all__ = [
    "FEATURE",
    "PROMPT_VERSION",
    "SCHEMA_VERSION",
    "extract",
    "extract_purchase_candidate",
    "prompt",
]
