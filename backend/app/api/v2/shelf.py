"""Shelf intelligence routes.

Everything here reads the caller's own inventory. There is no path parameter or
body field that names another account — ownership always comes from the token,
so there is nothing to tamper with.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.routines import service
from app.domains.routines.schemas import ShelfAnalyseRequest, ShelfManagerRespondRequest
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


@router.get("/shelf/manager")
async def shelf_manager(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The one thing your manager has decided about your Skin and Hair products.

    Read-only, and it never runs out of an honest answer: an empty shelf and a
    shelf with nothing to decide both return a real message rather than filler.
    """
    return await service.shelf_manager(session, account_id=current.account_id)


@router.post("/shelf/manager/respond")
async def shelf_manager_respond(
    body: ShelfManagerRespondRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Accept or set aside the decision at the front of the queue.

    The body names a decision and the fingerprint it was shown with, and nothing
    else. Which product it touches and what happens to it are decided here, from
    a queue recompiled on this request.
    """
    result = await service.shelf_manager_respond(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        body=body,
    )
    await session.commit()
    return result
