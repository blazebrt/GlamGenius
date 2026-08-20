"""Visible-fact Fragrance extraction; never a verdict or inventory write."""
from __future__ import annotations

import base64
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.media import service as media_service
from app.domains.purchase.schemas import ExtractedFragranceCandidate

FEATURE = "fragrance_purchase_candidate_extract"
PROMPT_VERSION = "v3-05.9"
SCHEMA_VERSION = "v3-05.9"

SYSTEM = """Transcribe only visible facts from a prospective perfume image.
Never infer intended occasion or season, chemistry, skin behaviour, longevity,
projection, sillage, compliments, gender suitability, quality, or whether the
customer should buy it. A customer must review and confirm every extracted fact.
"""


def prompt() -> str:
    return """Inspect this perfume product image and return visible facts only:
category=perfumes, display_name, brand, subcategory, fragrance_family and
concentration only when explicitly visible, price, currency, confidence,
uncertain_fields, and photo_quality_notes. Details may contain only
fragrance_family and concentration. Do not return occasion, season, longevity,
usage, remaining quantity, chemistry, or performance context. Never return a
recommendation."""


async def extract_fragrance_candidate(
    session: AsyncSession,
    *, account_id: uuid.UUID,
    account_id_str: str,
    media_asset_id: uuid.UUID,
):
    asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=media_asset_id)
    data = await media_service.read_bytes(asset)
    return await gateway.run_structured(
        feature=FEATURE,
        prompt=prompt(),
        system=SYSTEM,
        schema=ExtractedFragranceCandidate,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        account_id_str=account_id_str,
        image_base64=base64.b64encode(data).decode("ascii"),
    )


extract = extract_fragrance_candidate

__all__ = ["FEATURE", "PROMPT_VERSION", "SCHEMA_VERSION", "extract", "extract_fragrance_candidate", "prompt"]
