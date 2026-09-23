"""Owned supplement label-fact and utility routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.supplements import boundary as supplement_boundary
from app.domains.supplements import photo, service
from app.domains.supplements.schemas import LabelComponentCreate, LabelComponentPatch, LabelPhotoTranscribe
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_inventory"))])


class SupplementQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


@router.get("/supplements/summary")
async def supplement_summary(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await service.summary(session, current.account_id)


@router.post("/supplements/professional-boundary")
async def supplement_professional_boundary(
    body: SupplementQuestion,
    current: CurrentAccount = Depends(get_current_account),
):
    """Route health-like supplement questions without attempting an answer."""
    return supplement_boundary.evaluate(body.question).as_dict()


@router.get("/supplements/items/{item_id}")
async def supplement_detail(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Step 13: one owned supplement's label facts, provenance and overlap."""
    return await service.detail(session, current.account_id, item_id)


@router.post("/supplements/items/{item_id}/label-photo/transcribe")
async def transcribe_label_photo(
    item_id: uuid.UUID,
    body: LabelPhotoTranscribe,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read this supplement's label from the person's own photo, into drafts.

    Every row is ``photo_extracted`` and ``draft``: shown as not confirmed, and
    driving nothing until the person confirms it. Nothing else is written — no
    scan, no product record, no decision, no watch.

    The answer is a state, ``created`` or ``replayed``, with at least one row;
    the app renders its own keyed copy for it. A photo with no usable label
    detail is a retryable ``no_label_details`` refusal, not a success.
    """
    status, rows = await photo.transcribe(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        item_id=item_id,
        media_asset_id=body.media_asset_id,
        client_request_id=body.client_request_id,
    )
    await session.commit()
    return {"status": status, "label_facts": [service.serialize_fact(row) for row in rows]}


@router.get("/supplements/items/{item_id}/label-facts")
async def list_label_facts(
    item_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return {"label_facts": await service.list_facts(session, current.account_id, item_id)}


@router.post("/supplements/items/{item_id}/label-facts")
async def create_label_fact(
    item_id: uuid.UUID,
    body: LabelComponentCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await service.create_fact(session, current.account_id, item_id, body)
    await session.commit()
    return service.serialize_fact(row)


@router.patch("/supplements/items/{item_id}/label-facts/{fact_id}")
async def patch_label_fact(
    item_id: uuid.UUID,
    fact_id: uuid.UUID,
    body: LabelComponentPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await service.update_fact(session, current.account_id, item_id, fact_id, body)
    await session.commit()
    return service.serialize_fact(row)


@router.post("/supplements/items/{item_id}/label-facts/{fact_id}/confirm")
async def confirm_label_fact(
    item_id: uuid.UUID,
    fact_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = await service.confirm_fact(session, current.account_id, item_id, fact_id)
    await session.commit()
    return service.serialize_fact(row)


@router.delete("/supplements/items/{item_id}/label-facts/{fact_id}")
async def delete_label_fact(
    item_id: uuid.UUID,
    fact_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    await service.delete_fact(session, current.account_id, item_id, fact_id)
    await session.commit()
    return {"deleted": True, "id": str(fact_id)}
