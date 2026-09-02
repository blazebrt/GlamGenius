"""Shopper observation routes.

Scanning stays anonymous and viewing stays anonymous. Submitting does not: a
report can put a brand's name next to a stranger's claim, so it takes a real
account on a phone that account has claimed, and a scan of that very pack.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.admin import require_admin
from app.api.v2.product import current_device
from app.domains.community import service as community
from app.domains.community.models import (
    MODERATION_REASONS,
    REPORT_STATUS_ACCEPTED,
    REPORT_STATUS_INVALID,
    REPORT_STATUS_UNDER_REVIEW,
)
from app.domains.community.observations import OBSERVATION_CODES
from app.domains.product.models import ScanDevice
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()

_MODERATION_STATUSES = (REPORT_STATUS_UNDER_REVIEW, REPORT_STATUS_INVALID, REPORT_STATUS_ACCEPTED)


class ObservationBody(BaseModel):
    """Everything a shopper may send. There is no text field, by design.

    ``extra="forbid"`` matters more than usual here: reporter identity is taken
    from the authenticated session and the device token, never from the body, so
    an injected ``account_id`` must be refused rather than ignored.
    """

    model_config = ConfigDict(extra="forbid")

    client_report_id: str = Field(min_length=8, max_length=64)
    barcode: str = Field(min_length=6, max_length=64)
    observation_code: str = Field(min_length=3, max_length=48)
    photo_asset_id: uuid.UUID


@router.get("/community/observations/vocabulary")
async def read_observation_vocabulary():
    """The closed list of things a shopper may report, for the picker."""
    return {
        "observation_codes": sorted(OBSERVATION_CODES),
        "batch_scoped": sorted(community.is_batch_scoped_codes()),
        "policy_version": community.COMMUNITY_POLICY_VERSION,
    }


@router.get("/community/observations/context/{barcode}")
async def read_pack_context(
    barcode: str,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Whether this device currently has a lot number, decided by the server.

    The app must not guess, and must never be handed anybody else's lot. It
    reads only what this device itself last scanned.
    """
    return await community.pack_context_payload(session, barcode=barcode, device_id=device.id)


@router.get("/community/observations/mine/{barcode}")
async def read_own_observations(
    barcode: str,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The caller's own reports about one barcode, so they can withdraw one.

    Their rows only. Not a feed, not anybody else's history, not a profile.
    """
    reports = await community.own_reports_for_barcode(
        session, account_id=current.account_id, barcode=barcode,
    )
    return {"reports": [community.to_public_report(report) for report in reports]}


@router.post("/community/observations", status_code=status.HTTP_201_CREATED)
async def submit_observation(
    body: ObservationBody,
    device: ScanDevice = Depends(current_device),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    report, created = await community.submit_observation(
        session,
        account_id=current.account_id,
        device=device,
        barcode=body.barcode,
        observation_code=body.observation_code,
        photo_asset_id=body.photo_asset_id,
        client_report_id=body.client_report_id,
    )
    await session.commit()
    return {**community.to_public_report(report), "created": created}


@router.delete("/community/observations/{report_id}")
async def withdraw_observation(
    report_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Retract your own observation. It stops counting immediately."""
    report = await community.withdraw_observation(
        session, account_id=current.account_id, report_id=report_id,
    )
    await session.commit()
    return community.to_public_report(report)


class ModerationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(pattern="^(under_review|invalid|accepted)$")
    moderation_reason: str = Field(min_length=3, max_length=48)


@router.post("/admin/community/observations/{report_id}/moderate")
async def moderate_observation(
    report_id: uuid.UUID,
    body: ModerationBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Stop a demonstrably bad report from contributing. Closed reasons only."""
    if body.moderation_reason not in MODERATION_REASONS:
        raise ValidationFailedError(
            "That is not a moderation reason we accept.", field="moderation_reason",
        )
    report = await community.moderate_observation(
        session, report_id=report_id, status=body.status, moderation_reason=body.moderation_reason,
    )
    await session.commit()
    return {
        "id": str(report.id),
        "status": report.status,
        "moderation_reason": report.moderation_reason,
    }
