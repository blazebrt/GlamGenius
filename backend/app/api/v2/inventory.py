"""Authenticated Phase 3 complete inventory routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import batch, extraction, service
from app.domains.inventory.schemas import (
    BatchDecisions,
    BatchExtractRequest,
    ConditionCreate,
    DuplicateResolve,
    ExtractRequest,
    ItemCreate,
    ItemPatch,
    UsageCreate,
)
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import FeatureUnavailableError, ValidationFailedError
from app.shared.flags import service as flag_service
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_inventory"))])


@router.post("/inventory/extract")
async def extract_inventory_item(body: ExtractRequest, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    if body.capture_type in {"shelf_photo", "wardrobe_photo", "wardrobe_video"} and not await flag_service.is_enabled(session, "v2_inventory_batch"):
        raise FeatureUnavailableError("v2_inventory_batch")
    job, item, extracted = await extraction.analyse(session, account_id=current.account_id, account_id_str=current.account_id_str, media_asset_id=body.media_asset_id, category_hint=body.category_hint, capture_type=body.capture_type)
    await session.commit()
    return extraction.serialize_result(job, await service.serialize_item(session, item, include_history=True), extracted)


@router.post("/inventory/extract/batch")
async def extract_inventory_batch(
    body: BatchExtractRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """One photo of a shelf, several candidates, no items yet.

    Nothing here is on the shelf. Each candidate needs one tap.
    """
    if not await flag_service.is_enabled(session, "v2_inventory_batch"):
        raise FeatureUnavailableError("v2_inventory_batch")
    job, candidates, extracted = await batch.analyse_batch(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        media_asset_id=body.media_asset_id,
        category_hint=body.category_hint,
        capture_type=body.capture_type,
    )
    await session.commit()
    return batch.serialize_batch(job, candidates, extracted)


@router.get("/inventory/imports/{job_id}")
async def read_inventory_import(
    job_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The review list, re-readable — a phone that dropped mid-review resumes here."""
    job = await batch.owned_job(session, current.account_id, job_id)
    candidates = await batch.candidates_for(session, current.account_id, job_id)
    return batch.serialize_batch(job, candidates)


@router.post("/inventory/imports/{job_id}/candidates/{candidate_id}/confirm")
async def confirm_import_candidate(
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """One tap. The item is created, already confirmed."""
    candidate = await batch.owned_candidate(session, current.account_id, job_id, candidate_id)
    decided = await batch.decide(session, account_id=current.account_id, candidate=candidate, accept=True)
    await session.commit()
    return batch.serialize_candidate(decided)


@router.post("/inventory/imports/{job_id}/candidates/{candidate_id}/reject")
async def reject_import_candidate(
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """One tap the other way. Nothing is created."""
    candidate = await batch.owned_candidate(session, current.account_id, job_id, candidate_id)
    decided = await batch.decide(session, account_id=current.account_id, candidate=candidate, accept=False)
    await session.commit()
    return batch.serialize_candidate(decided)


@router.post("/inventory/imports/{job_id}/decisions")
async def decide_import_candidates(
    job_id: uuid.UUID,
    body: BatchDecisions,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Several taps in one request, for when taps outrun the network."""
    await batch.owned_job(session, current.account_id, job_id)
    decided = await batch.decide_many(
        session,
        account_id=current.account_id,
        job_id=job_id,
        decisions=[(row.candidate_id, row.accept) for row in body.decisions],
    )
    await session.commit()
    job = await batch.owned_job(session, current.account_id, job_id)
    candidates = await batch.candidates_for(session, current.account_id, job_id)
    return {
        "decided": [batch.serialize_candidate(row) for row in decided],
        **batch.serialize_batch(job, candidates),
    }


@router.post("/inventory/items")
async def create_inventory_item(body: ItemCreate, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    try:
        item = await service.create_item(session, current.account_id, body)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return await service.serialize_item(session, item, include_history=True)


@router.get("/inventory/items")
async def get_inventory_items(
    page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), q: str | None = Query(None, max_length=120),
    category: str | None = None, brand: str | None = Query(None, max_length=120), colour: str | None = Query(None, max_length=80),
    ingredient: str | None = Query(None, max_length=120), occasion: str | None = Query(None, max_length=120), season: str | None = Query(None, max_length=80),
    condition: str | None = Query(None, max_length=24), expiry_status: str | None = None, usage_level: str | None = None, verification_state: str | None = None, sort: str = "newest",
    current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session),
):
    return await service.list_items(session, current.account_id, page=page, page_size=page_size, q=q, category=category, brand=brand, colour=colour, ingredient=ingredient, occasion=occasion, season=season, condition=condition, expiry_status=expiry_status, usage_level=usage_level, verification_state=verification_state, sort=sort)


@router.get("/inventory/search")
async def search_inventory(
    q: str = Query("", max_length=120), page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), category: str | None = None,
    brand: str | None = None, colour: str | None = None, ingredient: str | None = None, occasion: str | None = None, season: str | None = None,
    condition: str | None = None, expiry_status: str | None = None, usage_level: str | None = None, verification_state: str | None = None, sort: str = "newest",
    current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session),
):
    return await service.list_items(session, current.account_id, page=page, page_size=page_size, q=q or None, category=category, brand=brand, colour=colour, ingredient=ingredient, occasion=occasion, season=season, condition=condition, expiry_status=expiry_status, usage_level=usage_level, verification_state=verification_state, sort=sort)


@router.get("/inventory/items/{item_id}")
async def get_inventory_item(item_id: uuid.UUID, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return await service.serialize_item(session, await service.owned_item(session, current.account_id, item_id), include_history=True)


@router.patch("/inventory/items/{item_id}")
async def patch_inventory_item(item_id: uuid.UUID, body: ItemPatch, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    item = await service.owned_item(session, current.account_id, item_id)
    try:
        await service.update_item(session, item, body)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return await service.serialize_item(session, item, include_history=True)


@router.delete("/inventory/items/{item_id}")
async def delete_inventory_item(item_id: uuid.UUID, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    item = await service.owned_item(session, current.account_id, item_id)
    await service.archive_item(session, item); await session.commit()
    return {"id": str(item.id), "status": "archived", "message": "Item removed from your active inventory. Its history is retained."}


@router.post("/inventory/items/{item_id}/confirm")
async def confirm_inventory_item(item_id: uuid.UUID, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    item = await service.owned_item(session, current.account_id, item_id); await service.confirm_item(session, item); await session.commit()
    return await service.serialize_item(session, item, include_history=True)


@router.post("/inventory/items/{item_id}/usage")
async def log_item_usage(item_id: uuid.UUID, body: UsageCreate, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    item = await service.owned_item(session, current.account_id, item_id); await service.log_usage(session, item, body.used_on, body.quantity, body.note); await session.commit()
    return await service.serialize_item(session, item, include_history=True)


@router.post("/inventory/items/{item_id}/condition")
async def log_item_condition(item_id: uuid.UUID, body: ConditionCreate, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    item = await service.owned_item(session, current.account_id, item_id); await service.log_condition(session, item, body.condition, body.note); await session.commit()
    return await service.serialize_item(session, item, include_history=True)


@router.get("/inventory/duplicates")
async def get_duplicate_candidates(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return {"label": "Duplicate Candidates", "candidates": await service.duplicates(session, current.account_id)}


@router.post("/inventory/duplicates/{candidate_id}/resolve")
async def resolve_duplicate_candidate(candidate_id: uuid.UUID, body: DuplicateResolve, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    row = await service.resolve_duplicate(session, current.account_id, candidate_id, body.resolution, body.canonical_item_id); await session.commit()
    return {"id": str(row.id), "status": row.status, "resolution": row.resolution}


@router.get("/inventory/expiring")
async def get_expiring_inventory(days: int = Query(90, ge=1, le=365), current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return {"label": "Products Expiring Soon", "days": days, "items": await service.expiring_items(session, current.account_id, days)}


@router.get("/inventory/low-use")
async def get_low_use_inventory(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return {"label": "Low-Use Products", "definition": "Active items at least 30 days old, used no more than twice and not used in the last 30 days.", "items": await service.low_use_items(session, current.account_id)}


@router.get("/inventory/value-to-recover")
async def get_value_to_recover(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return await service.value_report(session, current.account_id)


@router.get("/inventory/summary")
async def get_inventory_summary(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    return await service.summary(session, current.account_id)
