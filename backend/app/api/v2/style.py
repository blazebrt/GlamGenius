"""Occasion styling routes.

Every route resolves the caller from the token and scopes every query to the
account link that token maps to. No route reads an account or user id from a
path, a query string or a body.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.recommendation import occasions as occasion_catalogue
from app.domains.recommendation import orchestrator, service
from app.domains.recommendation.schemas import (
    LookFeedbackCreate,
    LookRevise,
    LookSwapItem,
    OccasionCreate,
    OccasionPatch,
    StyleForOccasion,
)
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_recommendations"))])


@router.get("/style/occasion-types")
async def get_occasion_types():
    """The supported occasions and the follow-up questions each one needs."""
    return {
        "occasions": occasion_catalogue.catalogue(),
        "dress_codes": [{"key": key, "formality": level} for key, level in occasion_catalogue.DRESS_CODES.items()],
        "note": "Only questions that change the answer for that occasion are asked.",
    }


@router.post("/occasions")
async def create_occasion(
    body: OccasionCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    record = await service.create_occasion(session, current.account_id, body)
    await session.commit()
    return service.serialize_occasion_record(record)


@router.get("/occasions")
async def list_occasions(
    include_archived: bool = False,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    rows = await service.list_occasions(session, current.account_id, include_archived=include_archived)
    return {"occasions": [service.serialize_occasion_record(row) for row in rows]}


@router.get("/occasions/{occasion_id}")
async def get_occasion(
    occasion_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    record = await service.owned_occasion(session, current.account_id, occasion_id)
    return service.serialize_occasion_record(record)


@router.patch("/occasions/{occasion_id}")
async def patch_occasion(
    occasion_id: uuid.UUID,
    body: OccasionPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    record = await service.owned_occasion(session, current.account_id, occasion_id)
    try:
        await service.update_occasion(session, record, body)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return service.serialize_occasion_record(record)


@router.post("/style/occasion")
async def style_for_occasion(
    body: StyleForOccasion,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Style me for an occasion. Returns up to three different looks."""
    if body.occasion_id is not None:
        record = await service.owned_occasion(session, current.account_id, body.occasion_id)
    else:
        try:
            record = await service.create_occasion(session, current.account_id, body.occasion)
        except ValueError as exc:
            raise ValidationFailedError(str(exc), field="occasion") from exc

    result = await orchestrator.style_for_occasion(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        occasion_record=record,
        preferred_item_ids=body.preferred_item_ids,
        client_mutation_id=body.client_mutation_id,
    )
    await session.commit()
    return result


@router.get("/looks/{look_id}")
async def get_look(
    look_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    look = await service.owned_look(session, current.account_id, look_id)
    return await service.serialize_look(session, look, include_history=True)


@router.post("/looks/{look_id}/revise")
async def revise_look(
    look_id: uuid.UUID,
    body: LookRevise,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    look = await service.owned_look(session, current.account_id, look_id)
    result = await orchestrator.revise_look(session, look=look, body=body, account_id_str=current.account_id_str)
    await session.commit()
    return result


@router.post("/looks/{look_id}/swap-item")
async def swap_look_item(
    look_id: uuid.UUID,
    body: LookSwapItem,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    look = await service.owned_look(session, current.account_id, look_id)
    result = await orchestrator.swap_item(session, look=look, body=body)
    await session.commit()
    return result


@router.post("/looks/{look_id}/feedback")
async def submit_look_feedback(
    look_id: uuid.UUID,
    body: LookFeedbackCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    look = await service.owned_look(session, current.account_id, look_id)
    await service.save_feedback(session, look, rating=body.rating, reason=body.reason, note=body.note, worn_on=body.worn_on)
    await session.commit()
    return await service.serialize_look(session, look, include_history=True)
