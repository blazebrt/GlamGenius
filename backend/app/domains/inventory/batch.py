"""Multi-item capture: one photo of a shelf, one tap per thing on it.

Why this is a separate step rather than fifteen drafts
------------------------------------------------------
The single-item path writes a draft ``InventoryItem`` straight away, and that
is fine for one thing a person deliberately photographed. It is not fine for
fifteen guesses from one photo of a shelf: they would appear in the inventory
list, enter the duplicates queue and carry attribute rows before anyone had
looked at them. "Nothing enters the shelf unconfirmed" is only true if the
unconfirmed thing is not on the shelf.

So a shelf photo produces **candidates**. A candidate is a row in its own
table, tied to the import job, and it is not an item. One tap accepts it —
and only then does it become a real item, created by the same
``service.create_item`` every other item goes through, already confirmed
because the tap *is* the confirmation. One tap rejects it, and nothing is
created.

This is the VC-07 shape, extended rather than repeated: extracted, shown back,
and counted only when a person says so. The AI contract is the same
``ExtractedInventoryItem`` the single-item path validates against, so a
candidate cannot carry a category, a detail field or an attribute key that a
hand-typed item could not.
"""
from __future__ import annotations

import base64
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.models import AIRun
from app.domains.inventory import service
from app.domains.inventory.models import InventoryImportCandidate, InventoryImportJob
from app.domains.inventory.schemas import (
    BATCH_ITEM_LIMIT,
    AttributeInput,
    ExtractedInventoryBatch,
    ExtractedInventoryItem,
    ItemCreate,
)
from app.domains.inventory.taxonomy import CATEGORIES
from app.domains.media import service as media_service
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import (
    AnalysisUnavailableError,
    NotFoundError,
    ValidationFailedError,
)

PROMPT_VERSION = "inventory-batch-v1"
SCHEMA_VERSION = "inventory-batch-v1"
FEATURE = "inventory_extract_batch"

STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"
STATE_REJECTED = "rejected"

# The same boundary the single-item prompt carries, said for a shelf. The
# additions are all about restraint: list what is legible, count what is not,
# and never pad the list to look thorough.
SYSTEM = """You list the separate products visible in one photograph of a shelf, counter or drawer.
Return one JSON object matching the requested schema. Use only visible evidence.
List a product only if you can read enough of its label or shape to name it. Count anything
you can see but cannot identify in unreadable_count instead of guessing at it. Never invent
a product to make the list longer, never repeat the same physical item twice, never invent a
brand, and never claim uncertain text is exact.
Never diagnose and never infer sensitive traits. For supplements, transcribe label information
only: no dosage advice, prescriptions, disease claims, treatment changes, pregnancy advice or
interactions. Use one of these categories: wardrobe, shoes, accessories, beauty, hair,
perfumes, supplements. Each item's confidence describes that item alone and must be honest.
"""


def prompt(category_hint: str | None) -> str:
    allowed = ", ".join(CATEGORIES)
    return f"""Inspect this single photograph of several products together.
Category hint: {category_hint or 'none'}. Allowed categories: {allowed}.
Return one JSON object with items, photo_quality_notes and unreadable_count.
Each entry in items must have category, subcategory, display_name, brand, confidence,
category-specific details, searchable attributes with per-field confidence, uncertain_fields
and photo_quality_notes. List them in the order they appear, left to right, front to back.
Return at most {BATCH_ITEM_LIMIT} items. Lists must be JSON arrays. Dates must be YYYY-MM-DD.
Omit facts that are not visible. Do not return purchase price. Do not return supplement dosage
or usage recommendations.
"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

async def analyse_batch(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    media_asset_id: uuid.UUID,
    category_hint: str | None,
    capture_type: str,
) -> tuple[InventoryImportJob, list[InventoryImportCandidate], ExtractedInventoryBatch]:
    """Read one shelf photo into candidates. No item is created here."""
    asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=media_asset_id)
    data = await media_service.read_bytes(asset)
    job = InventoryImportJob(
        account_id=account_id, capture_type=capture_type,
        status="processing", media_asset_id=media_asset_id,
    )
    session.add(job)
    await session.flush()

    try:
        result = await gateway.run_structured(
            feature=FEATURE,
            prompt=prompt(category_hint),
            system=SYSTEM,
            schema=ExtractedInventoryBatch,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            account_id_str=account_id_str,
            image_base64=base64.b64encode(data).decode("ascii"),
        )
    except AnalysisUnavailableError as exc:
        job.status = "failed"
        job.error_code = exc.failure_type.value
        job.completed_at = utcnow()
        # The request dependency rolls back a raised request. Commit the
        # import-job fact on its own so the person can see why it failed.
        await session.commit()
        raise

    ai_run_id = result.run_id if await session.get(AIRun, result.run_id) else None
    candidates = [
        _candidate_from(account_id, job.id, position, extracted)
        for position, extracted in enumerate(result.data.items)
    ]
    for row in candidates:
        session.add(row)

    job.status = "completed"
    job.ai_run_id = ai_run_id
    job.detected_count = len(candidates)
    job.completed_at = utcnow()
    await session.flush()
    return job, candidates, result.data


def _candidate_from(
    account_id: uuid.UUID, job_id: uuid.UUID, position: int, extracted: ExtractedInventoryItem,
) -> InventoryImportCandidate:
    return InventoryImportCandidate(
        job_id=job_id,
        account_id=account_id,
        position=position,
        category=extracted.category,
        subcategory=extracted.subcategory,
        display_name=extracted.display_name.strip(),
        brand=extracted.brand.strip() if extracted.brand else None,
        confidence=extracted.confidence,
        details=extracted.details,
        attributes=[
            {"key": row.key, "value": row.value, "confidence": row.confidence}
            for row in extracted.attributes
        ],
        uncertain_fields=list(extracted.uncertain_fields),
        photo_quality_notes=extracted.photo_quality_notes,
        state=STATE_PENDING,
    )


# ---------------------------------------------------------------------------
# One tap
# ---------------------------------------------------------------------------

async def owned_job(session: AsyncSession, account_id: uuid.UUID, job_id: uuid.UUID) -> InventoryImportJob:
    job = (await session.execute(
        select(InventoryImportJob).where(
            InventoryImportJob.id == job_id, InventoryImportJob.account_id == account_id,
        )
    )).scalar_one_or_none()
    if job is None:
        raise NotFoundError("We could not find that capture.")
    return job


async def owned_candidate(
    session: AsyncSession, account_id: uuid.UUID, job_id: uuid.UUID, candidate_id: uuid.UUID,
) -> InventoryImportCandidate:
    row = (await session.execute(
        select(InventoryImportCandidate).where(
            InventoryImportCandidate.id == candidate_id,
            InventoryImportCandidate.account_id == account_id,
            InventoryImportCandidate.job_id == job_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError("We could not find that item from the photo.")
    return row


async def decide(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    candidate: InventoryImportCandidate,
    accept: bool,
) -> InventoryImportCandidate:
    """Accept or reject one candidate.

    Accepting creates the item through the ordinary create path, already
    confirmed: the tap is the confirmation, and asking for a second one would
    be asking the same question twice.

    Deciding again is a no-op rather than an error. A phone that retries a tap
    it already sent must not create the item twice.
    """
    if candidate.state != STATE_PENDING:
        return candidate

    if not accept:
        candidate.state = STATE_REJECTED
        candidate.decided_at = utcnow()
        await session.flush()
        return candidate

    job = await session.get(InventoryImportJob, candidate.job_id)
    body = ItemCreate(
        category=candidate.category,
        subcategory=candidate.subcategory,
        display_name=candidate.display_name,
        brand=candidate.brand,
        details=candidate.details or {},
        attributes=[
            AttributeInput(key=row["key"], value=row["value"])
            for row in (candidate.attributes or [])
        ],
        image_ids=[job.media_asset_id] if job and job.media_asset_id else [],
    )
    item = await service.create_item(
        session, account_id, body,
        source="photo_extracted",
        # The person has just looked at it and said yes. That is the
        # confirmation; there is no second step.
        verification_state="confirmed",
        confidence=1.0,
        ai_run_id=job.ai_run_id if job else None,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    candidate.state = STATE_CONFIRMED
    candidate.item_id = item.id
    candidate.decided_at = utcnow()
    await session.flush()
    return candidate


async def decide_many(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    job_id: uuid.UUID,
    decisions: list[tuple[uuid.UUID, bool]],
) -> list[InventoryImportCandidate]:
    """Apply several taps in one request. Order is preserved."""
    seen: set[uuid.UUID] = set()
    decided: list[InventoryImportCandidate] = []
    for candidate_id, accept in decisions:
        if candidate_id in seen:
            raise ValidationFailedError("That item was decided twice in one request.", field="decisions")
        seen.add(candidate_id)
        candidate = await owned_candidate(session, account_id, job_id, candidate_id)
        decided.append(await decide(session, account_id=account_id, candidate=candidate, accept=accept))
    return decided


# ---------------------------------------------------------------------------
# What the review screen reads
# ---------------------------------------------------------------------------

def serialize_candidate(row: InventoryImportCandidate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "position": row.position,
        "category": row.category,
        "subcategory": row.subcategory,
        "display_name": row.display_name,
        "brand": row.brand,
        "confidence": row.confidence,
        "details": row.details or {},
        "attributes": row.attributes or [],
        "uncertain_fields": row.uncertain_fields or [],
        "photo_quality_notes": row.photo_quality_notes,
        "state": row.state,
        "item_id": str(row.item_id) if row.item_id else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


async def candidates_for(
    session: AsyncSession, account_id: uuid.UUID, job_id: uuid.UUID,
) -> list[InventoryImportCandidate]:
    return list((await session.execute(
        select(InventoryImportCandidate)
        .where(
            InventoryImportCandidate.account_id == account_id,
            InventoryImportCandidate.job_id == job_id,
        )
        .order_by(InventoryImportCandidate.position.asc(), InventoryImportCandidate.id.asc())
    )).scalars().all())


def serialize_batch(
    job: InventoryImportJob,
    candidates: list[InventoryImportCandidate],
    extracted: ExtractedInventoryBatch | None = None,
) -> dict[str, Any]:
    pending = [row for row in candidates if row.state == STATE_PENDING]
    return {
        "job_id": str(job.id),
        "status": job.status,
        "capture_type": job.capture_type,
        "detected_count": job.detected_count,
        "pending_count": len(pending),
        "confirmed_count": sum(1 for row in candidates if row.state == STATE_CONFIRMED),
        "rejected_count": sum(1 for row in candidates if row.state == STATE_REJECTED),
        "candidates": [serialize_candidate(row) for row in candidates],
        "photo_quality_notes": extracted.photo_quality_notes if extracted else None,
        # Said rather than hidden: the list is not necessarily the whole shelf.
        "unreadable_count": extracted.unreadable_count if extracted else None,
        "message": (
            "Nothing here is on your shelf yet. Tap the tick to add one, the cross to drop it."
        ),
    }
