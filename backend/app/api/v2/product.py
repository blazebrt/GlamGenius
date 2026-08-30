"""Scanning a packaged product.

These routes accept an anonymous device token (``X-Device-Token``) as well as a
signed-in account, because the camera opens on first launch with nothing set
up. A device token reaches product data and nothing else.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product import devices, extraction, service
from app.domains.product.confidence import ProductConfidence
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import ScanDevice
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()


class DeviceRegisterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_key: str = Field(min_length=8, max_length=64)
    platform: str | None = Field(default=None, max_length=24)


class ScanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    client_scan_id: str = Field(min_length=6, max_length=64)
    scanned_at: datetime | None = None
    queued_offline: bool = False


class TranscribeLabelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    media_asset_id: uuid.UUID


class ConfirmLabelBody(BaseModel):
    """One tap to accept, the VC-07 shape: a draft becomes confirmed."""

    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    facts: dict[str, Any] = Field(default_factory=dict)
    client_scan_id: str = Field(min_length=6, max_length=64)


async def current_device(
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
) -> ScanDevice:
    """Resolve the device token. The only identity the scan needs."""
    device = await devices.resolve(session, x_device_token)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "DEVICE_UNKNOWN",
                "message": "This device is not registered. Register it and try again.",
            },
        )
    return device


@router.post("/scan/device", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegisterBody,
    session: AsyncSession = Depends(get_session),
):
    """Register the phone. Called once on first launch, before anything else."""
    device, token = await devices.register(
        session, device_key=body.device_key, platform=body.platform,
    )
    await session.commit()
    return {"device_id": str(device.id), "token": token}


@router.post("/scan/device/claim")
async def claim_device(
    device: ScanDevice = Depends(current_device),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Attach this phone to the account that has just signed in.

    Scans made before signing up then belong to that person, and appear in
    their data export. Scans made on a phone nobody ever claims belong to
    nobody.
    """
    await devices.claim(session, device=device, account_id=current.account_id)
    moved = await service.attach_scans_to_account(
        session, device_id=device.id, account_id=current.account_id,
    )
    await session.commit()
    return {"claimed": True, "scans_attached": moved}


@router.get("/scan/lookup/{barcode}")
async def lookup_barcode(
    barcode: str,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Ours, then Open Food Facts, then an honest 'not found'.

    Always answers, always with a confidence level.
    """
    return await service.lookup(session, barcode)


@router.post("/scan/events", status_code=status.HTTP_201_CREATED)
async def record_scan_event(
    body: ScanBody,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Record a scan. Safe to replay: an offline queue can send the same one twice."""
    result = await service.lookup(session, body.barcode)
    event, created = await service.record_scan(
        session,
        barcode=body.barcode,
        outcome=result["outcome"],
        client_scan_id=body.client_scan_id,
        device_id=device.id,
        account_id=device.claimed_by_account_id,
        queued_offline=body.queued_offline,
        scanned_at=body.scanned_at,
    )
    if created:
        device.scan_count += 1
    await session.commit()
    return {"scan_id": str(event.id), "created": created, "product": result}


@router.post("/scan/label/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_label(
    body: ConfirmLabelBody,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Accept a transcribed label. One tap, the VC-07 draft-to-confirmed pattern."""
    if not body.facts:
        raise ValidationFailedError("There is nothing to confirm.", field="facts")
    record = await service.apply_confirmed_label(session, barcode=body.barcode, facts=body.facts)
    await service.record_scan(
        session,
        barcode=body.barcode, outcome=service.OUTCOME_LABEL,
        client_scan_id=body.client_scan_id, device_id=device.id,
        account_id=device.claimed_by_account_id, label_facts=body.facts,
    )
    await session.commit()
    return {
        "barcode": record.barcode,
        "confidence": service.confidence_block(record.confidence),
        "fssai_licence": record.fssai_licence,
        "confirmations": record.confirmation_count,
    }


@router.post("/scan/label/transcribe")
async def transcribe_label(
    body: TranscribeLabelBody,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read a label photo and hand back what was on it. Nothing is stored yet.

    Signed in, unlike the rest of scanning. Reading a label costs a model call,
    so it is attached to an account and counted, while looking a barcode up
    stays open to any device. The transcription is returned for the person to
    check; ``/scan/label/confirm`` is what writes it.
    """
    result = await extraction.transcribe_label(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        media_asset_id=body.media_asset_id,
    )
    await session.commit()
    facts = result.data.model_dump(exclude_none=True)
    licence = facts.get("fssai_licence") or find_licence(facts.get("ingredients_text"))
    return {
        "barcode": body.barcode,
        "facts": facts,
        "fssai_licence": licence if licence and is_valid_licence(licence) else None,
        # Read, shown, not kept. A person confirms before anything is written.
        "stored": False,
        "confidence": service.confidence_block(ProductConfidence.UNVERIFIED.value),
        "provenance": result.provenance(),
    }
