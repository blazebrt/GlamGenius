"""Consent routes.

Not in the original Phase 1 route list, but consent cannot be *given* through
any of the listed routes, and "consent before photo analysis" is a Phase 1
requirement. Two routes added, documented here as a deliberate addition.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, client_ip, get_current_account

router = APIRouter()

ALLOWED_CONSENT_TYPES = {CONSENT_PHOTO_ANALYSIS}


class ConsentRequest(BaseModel):
    consent_type: str = CONSENT_PHOTO_ANALYSIS
    granted: bool


@router.get("/consent")
async def get_consent(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return await consent_service.summary(session, current.account_id)


@router.post("/consent")
async def post_consent(
    body: ConsentRequest,
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record an answer. Both granting and revoking are appended, never edited."""
    if body.consent_type not in ALLOWED_CONSENT_TYPES:
        raise ValidationFailedError(
            "That is not something we ask consent for.", field="consent_type"
        )

    await consent_service.record(
        session,
        account_id=current.account_id,
        consent_type=body.consent_type,
        granted=body.granted,
        source="app",
        client_ip=client_ip(request),
    )
    await session.commit()
    return await consent_service.summary(session, current.account_id)
