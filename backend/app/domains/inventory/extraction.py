"""Schema-validated inventory photo extraction that creates drafts only."""
from __future__ import annotations

import base64
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.models import AIRun
from app.domains.inventory import service
from app.domains.inventory.models import InventoryImportJob
from app.domains.inventory.schemas import AttributeInput, ExtractedInventoryItem, ItemCreate
from app.domains.inventory.taxonomy import CATEGORIES
from app.domains.media import service as media_service
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import AnalysisUnavailableError

PROMPT_VERSION = "inventory-extract-v1"
SCHEMA_VERSION = "inventory-item-v1"
SYSTEM = """You extract visible label and item facts for an appearance inventory.
Return one JSON object matching the requested schema. Use only visible evidence.
Never diagnose, infer sensitive traits, invent a brand, or claim uncertain text is exact.
For supplements, transcribe label information only. Never provide dosage advice,
prescriptions, disease claims, treatment changes, pregnancy advice, or interactions.
Use one of these categories: wardrobe, shoes, accessories, beauty, hair, perfumes,
supplements. Confidence describes the whole extraction and must be honest.
"""


def prompt(category_hint: Optional[str]) -> str:
    allowed = ", ".join(CATEGORIES)
    return f"""Inspect this single inventory item or product screenshot.
Category hint: {category_hint or 'none'}. Allowed categories: {allowed}.
Return category, subcategory, display_name, brand, confidence, category-specific details,
searchable attributes with per-field confidence, uncertain_fields, and photo_quality_notes.
Lists must be JSON arrays. Dates must be YYYY-MM-DD. Omit facts that are not visible.
Do not return purchase price. Do not return supplement dosage or usage recommendations.
"""


async def analyse(
    session: AsyncSession,
    *, account_id: uuid.UUID, v1_user_id: str, media_asset_id: uuid.UUID,
    category_hint: Optional[str], capture_type: str,
) -> tuple[InventoryImportJob, Any, ExtractedInventoryItem]:
    asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=media_asset_id)
    data = await media_service.read_bytes(asset)
    job = InventoryImportJob(account_id=account_id, capture_type=capture_type, status="processing", media_asset_id=media_asset_id)
    session.add(job); await session.flush()
    try:
        result = await gateway.run_structured(feature="inventory_extract", prompt=prompt(category_hint), system=SYSTEM, schema=ExtractedInventoryItem, prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION, v1_user_id=v1_user_id, image_base64=base64.b64encode(data).decode("ascii"))
    except AnalysisUnavailableError as exc:
        job.status = "failed"; job.error_code = exc.failure_type.value; job.completed_at = utcnow()
        # The request dependency rolls back raised requests. Commit only this
        # import-job fact first so the user can later see why extraction failed.
        await session.commit()
        raise
    ai_run_id = result.run_id if await session.get(AIRun, result.run_id) else None
    body = ItemCreate(category=result.data.category, subcategory=result.data.subcategory, display_name=result.data.display_name, brand=result.data.brand, details=result.data.details, attributes=[AttributeInput(key=row.key, value=row.value) for row in result.data.attributes], image_ids=[media_asset_id])
    item = await service.create_item(session, account_id, body, source="photo_extracted", verification_state="draft", confidence=result.data.confidence, ai_run_id=ai_run_id, model_version=result.model, prompt_version=result.prompt_version, schema_version=result.schema_version)
    # Preserve each field's own confidence instead of flattening everything to
    # the item confidence used during the initial insert.
    rows = await service._attribute_map(session, item.id)
    for extracted in result.data.attributes:
        if extracted.key in rows:
            rows[extracted.key].confidence = extracted.confidence
    job.status = "completed"; job.ai_run_id = ai_run_id; job.detected_count = 1; job.completed_at = utcnow()
    await session.flush()
    return job, item, result.data


def serialize_result(job: InventoryImportJob, item: Dict[str, Any], extracted: ExtractedInventoryItem) -> Dict[str, Any]:
    return {"job_id": str(job.id), "status": job.status, "capture_type": job.capture_type, "batch_accuracy_claimed": False, "item": item, "uncertain_fields": extracted.uncertain_fields, "photo_quality_notes": extracted.photo_quality_notes, "message": "Review this draft. Nothing becomes a verified fact until you confirm it."}
