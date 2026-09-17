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
    SubjectCareProfilePatch,
    SubjectCareProfileResponse,
)
from app.domains.family.subject import SubjectNotFound, resolve_subject
from app.domains.profile import service as profile_service
from app.domains.profile.identity import resolve_subject_profile_for_write
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
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


@router.patch(
    "/family-circle/profiles/{profile_id}/care-profile",
    response_model=SubjectCareProfileResponse,
)
async def update_subject_care_profile(
    profile_id: uuid.UUID,
    body: SubjectCareProfilePatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Record Care facts about one named member of this household.

    The narrow seam that lets a member ever acquire a Personal Lens. Per-subject
    onboarding is a later slice; without something like this a member could be
    named and then never described, and the lens would answer "we do not know
    enough about this person" forever.

    ``profile_id`` is a claim, not an instruction. It goes through the Step 11A
    resolver, so a member of another household, a deactivated member and an
    invented id are refused identically and without echoing the id back.

    An under-12 member may have facts recorded and corrected here. That is not
    permission to advise: the hard handoff still fires on every personalised
    decision for them. Storing what somebody's skin is like and telling them
    what to put on it are different acts, and only the second one is barred.
    """
    # One ``try`` around the whole identity sequence, not just the first read.
    # The write resolver canonicalises again on purpose, and a member
    # deactivated between the two resolutions must answer like any other
    # unknown member rather than as a server fault. Splitting the catch is how
    # that race became a 500.
    try:
        subject = await resolve_subject(
            session, account_id=current.account_id, subject_id=profile_id,
        )
        profile = await resolve_subject_profile_for_write(
            session, subject, principal_account_id=current.account_id,
        )
    except SubjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "family_profile_not_found"},
        ) from exc

    try:
        await profile_service.apply_attributes(
            session, profile, [item.model_dump() for item in body.attributes],
        )
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()

    rows = await profile_service.attributes_for(session, profile.id)
    return {
        "subject_id": subject.subject_id or profile_id,
        "relation": subject.relation,
        "age_band": subject.age_band,
        "attributes": [profile_service.serialize_attribute(row) for row in rows],
    }
