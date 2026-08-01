"""Authenticated Phase 3 complete inventory routes."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import extraction, service
from app.domains.inventory.schemas import ConditionCreate, DuplicateResolve, ExtractRequest, ItemCreate, ItemPatch, UsageCreate
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import FeatureUnavailableError, ValidationFailedError
from app.shared.flags import service as flag_service
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_inventory"))])


@router.post("/inventory/extract")
async def extract_inventory_item(body: ExtractRequest, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    if body.capture_type in {"shelf_photo", "wardrobe_photo", "wardrobe_video"} and not await flag_service.is_enabled(session, "v2_inventory_batch"):
        raise FeatureUnavailableError("v2_inventory_batch")
    job, item, extracted = await extraction.analyse(session, account_id=current.account_id, v1_user_id=current.v1_user_id, media_asset_id=body.media_asset_id, category_hint=body.category_hint, capture_type=body.capture_type)
    await session.commit()
    return extraction.serialize_result(job, await service.serialize_item(session, item, include_history=True), extracted)


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
    page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), q: Optional[str] = Query(None, max_length=120),
    category: Optional[str] = None, brand: Optional[str] = Query(None, max_length=120), colour: Optional[str] = Query(None, max_length=80),
    ingredient: Optional[str] = Query(None, max_length=120), occasion: Optional[str] = Query(None, max_length=120), season: Optional[str] = Query(None, max_length=80),
    condition: Optional[str] = Query(None, max_length=24), expiry_status: Optional[str] = None, usage_level: Optional[str] = None, verification_state: Optional[str] = None, sort: str = "newest",
    current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session),
):
    return await service.list_items(session, current.account_id, page=page, page_size=page_size, q=q, category=category, brand=brand, colour=colour, ingredient=ingredient, occasion=occasion, season=season, condition=condition, expiry_status=expiry_status, usage_level=usage_level, verification_state=verification_state, sort=sort)


@router.get("/inventory/search")
async def search_inventory(
    q: str = Query("", max_length=120), page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100), category: Optional[str] = None,
    brand: Optional[str] = None, colour: Optional[str] = None, ingredient: Optional[str] = None, occasion: Optional[str] = None, season: Optional[str] = None,
    condition: Optional[str] = None, expiry_status: Optional[str] = None, usage_level: Optional[str] = None, verification_state: Optional[str] = None, sort: str = "newest",
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
