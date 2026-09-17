"""Family Circle API: account-local profiles, no cross-account invites."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family import service
from app.domains.family.schemas import (
    FamilyCircleResponse,
    FamilyProfileCreate,
    FamilyProfilePatch,
    FamilyProfileResponse,
)
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()


@router.get("/family-circle", response_model=FamilyCircleResponse)
async def get_family_circle(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await service.read_circle(session, current.account_id)


@router.post("/family-circle/profiles", response_model=FamilyProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_family_profile(
    body: FamilyProfileCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    try:
        profile = await service.add_profile(
            session, current.account_id, relation=body.relation, age_band=body.age_band,
        )
    except service.FamilyProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": str(exc)}) from exc
    await session.commit()
    return service.serialise_profile(profile)


@router.patch("/family-circle/profiles/{profile_id}", response_model=FamilyProfileResponse)
async def update_family_profile(
    profile_id: uuid.UUID,
    body: FamilyProfilePatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    # Only the fields the caller actually named. ``exclude_unset`` is what the
    # rest of this application's patch routes use, and it is what keeps
    # "set active to false" apart from "say nothing about active".
    changes = body.model_dump(exclude_unset=True)
    try:
        profile = await service.update_profile(
            session,
            current.account_id,
            profile_id,
            active=changes.get("active", service.UNCHANGED),
            age_band=changes.get("age_band", service.UNCHANGED),
        )
    except service.FamilyProfileError as exc:
        # Unchanged from before this route learned about age bands, including
        # for the self row: a refusal here does not confirm whether the
        # identifier names anybody.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": str(exc)}) from exc
    await session.commit()
    return service.serialise_profile(profile)
