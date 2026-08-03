"""Shelf intelligence routes.

Everything here reads the caller's own inventory. There is no path parameter or
body field that names another account — ownership always comes from the token,
so there is nothing to tamper with.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.routines import service
from app.domains.routines.schemas import ShelfAnalyseRequest
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_routines"))])


@router.post("/shelf/analyse")
async def analyse_shelf(
    body: ShelfAnalyseRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Re-read your beauty and hair products and store what we worked out."""
    result = await service.analyse_shelf(session, account_id=current.account_id, body=body)
    await session.commit()
    return result


@router.get("/shelf/summary")
async def shelf_summary(
    climate: str | None = Query(None, max_length=24),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Your whole shelf at a glance. Counted, never scored."""
    return await service.shelf_summary(session, account_id=current.account_id, climate=climate)


@router.get("/shelf/expiring")
async def shelf_expiring(
    days: int = Query(60, ge=1, le=365),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What is running out, and what has no date recorded at all."""
    return await service.shelf_expiring(session, account_id=current.account_id, days=days)


@router.get("/shelf/low-use")
async def shelf_low_use(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Products sitting unused."""
    return await service.shelf_low_use(session, account_id=current.account_id)


@router.get("/shelf/value-to-recover")
async def shelf_value_to_recover(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """An estimate of what is sitting in unused beauty and hair products."""
    return await service.shelf_value_to_recover(session, account_id=current.account_id)
