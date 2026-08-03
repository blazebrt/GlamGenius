"""Appearance digital twin routes."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MAX_IMAGE_BASE64_CHARS
from app.domains.consent import service as consent_service
from app.domains.profile import baseline, observations, service
from app.domains.profile.registry import ATTRIBUTE_REGISTRY
from app.domains.profile.schemas import BaselineRequest, ObservationEdit, ProfilePatch
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_profile"))])


async def _profile(session: AsyncSession, current: CurrentAccount):
    return await service.get_or_create_profile(session, current.account_id)


@router.get("/profile")
async def get_profile(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    body = await service.serialize_profile(session, profile)
    body["change_history"] = await service.change_history(session, profile.id)
    await session.commit()
    return body


@router.patch("/profile")
async def patch_profile(body: ProfilePatch, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    try:
        await service.apply_attributes(session, profile, [item.model_dump() for item in body.attributes])
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return await service.serialize_profile(session, profile)


@router.get("/profile/attributes")
async def get_attributes(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    rows = await service.attributes_for(session, profile.id)
    await session.commit()
    return {
        "attributes": [service.serialize_attribute(row) for row in rows],
        "registry": [{"key": spec.key, "label": spec.label, "section": spec.section, "kind": spec.kind} for spec in ATTRIBUTE_REGISTRY.values()],
        "readiness": service.readiness(rows), "weight_required": False,
    }


@router.get("/profile/observations")
async def get_observations(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    rows = await observations.list_for_profile(session, profile.id)
    await session.commit()
    return {"observations": [observations.serialize(row) for row in rows]}


@router.post("/profile/observations/{observation_id}/confirm")
async def confirm_observation(observation_id: uuid.UUID, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    row = await observations.owned(session, profile.id, observation_id)
    await observations.confirm(session, profile, row)
    await session.commit()
    return observations.serialize(row)


@router.post("/profile/observations/{observation_id}/reject")
async def reject_observation(observation_id: uuid.UUID, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    row = await observations.owned(session, profile.id, observation_id)
    observations.reject(row)
    await session.commit()
    return observations.serialize(row)


@router.patch("/profile/observations/{observation_id}")
async def edit_observation(observation_id: uuid.UUID, body: ObservationEdit, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await _profile(session, current)
    row = await observations.owned(session, profile.id, observation_id)
    try:
        observations.edit(row, body.value, body.verification_state)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return observations.serialize(row)


@router.post("/profile/baseline-analysis")
async def baseline_analysis(body: BaselineRequest, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    if len(body.image_base64) > MAX_IMAGE_BASE64_CHARS:
        raise ValidationFailedError("That image is too large. Please choose a smaller photo.", field="image_base64")
    await consent_service.require_analysis_consent(session, current.account_id)
    profile = await _profile(session, current)
    result = await baseline.analyse(session, profile, account_id_str=current.account_id_str, image_base64=body.image_base64)
    await session.commit()
    return result
