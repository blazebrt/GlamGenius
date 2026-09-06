"""Photographing a skin-care ingredient label.

A separate module from ``product.py`` on purpose. That file is the packaged-food
scanning surface and is already long; more importantly the two paths mean
different things, and keeping them apart is what stops a future change to one
quietly widening the other. This is an API module, not a new domain — every
decision below is made in :mod:`app.domains.product.care_capture`.

Two routes, in the order a person meets them:

* **transcribe** — a draft. Signed in, because reading a label costs a model
  call. Nothing is written: no scan event, no product record, no label
  snapshot, no category. The person is shown what was read and decides.
* **confirm** — the write. Signed in *and* on a device they own, because a
  confirmed capture is an assertion about what the holder of that phone was
  looking at. It carries three identifiers and no facts: the facts are re-read
  from the stored transcription.

There is no category parameter on either route, and there is no route here that
accepts one for an already-confirmed snapshot. Reaching the confirmation route
*is* the category: it is the structured form of a person choosing "Skin Care".
``docs/architecture/SKIN_CARE_LABEL_CAPTURE.md`` explains why the category may
only ever enter once, and never at read time.

Nothing here returns a verdict. No Buy, no Wait, no Skip, no reason, no
release. Capture makes the input trustworthy; the governed Step 7/8 chain is
what will eventually decide anything from it.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.product import current_device
from app.domains.product import care_capture, care_extraction, service
from app.domains.product.confidence import ProductConfidence
from app.domains.product.models import ScanDevice
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()


class SkinCareLabelTranscribeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    media_asset_id: uuid.UUID


class ConfirmSkinCareLabelBody(BaseModel):
    """Three identifiers. Deliberately nothing else.

    No category, because the route supplies it. No ingredients and no
    corrections, because the facts are re-read from the stored transcription:
    a body that could carry them would be a body that could rewrite what the
    camera saw. No verdict field, because nothing here decides anything.

    ``extra="forbid"`` makes each of those a rejection rather than a silently
    ignored key.
    """

    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    ai_run_id: uuid.UUID
    client_scan_id: str = Field(min_length=6, max_length=64)


@router.post("/scan/skin-care/label/transcribe")
async def transcribe_skin_care_label(
    body: SkinCareLabelTranscribeBody,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read a skin-care label photo and hand back what was on it. Nothing is stored.

    The draft is returned for the person to check against the pack in their
    hand. A photograph whose ingredient list could not be read is still
    returned — they tried, and saying so is more useful than an error — but it
    is marked unreadable, and confirmation will refuse it.
    """
    result = await care_extraction.transcribe_label(
        session,
        account_id=current.account_id,
        account_id_str=current.account_id_str,
        media_asset_id=body.media_asset_id,
    )
    await session.commit()
    extracted = result.data
    facts = {
        field: getattr(extracted, field)
        for field in ("product_name", "brand", "product_type", "ingredients_text")
        if getattr(extracted, field) is not None
    }
    readable = care_capture.has_readable_ingredients(extracted)
    return {
        "barcode": body.barcode,
        "facts": facts,
        # Read, shown, not kept. A person confirms before anything is written.
        "stored": False,
        "ingredients_readable": readable,
        "message": None if readable else care_capture.UNREADABLE_INGREDIENTS_MESSAGE,
        # How well the photograph read, kept apart from the facts themselves:
        # this describes the image and the model's own hesitancy, and none of it
        # is persisted with the confirmed label.
        "capture_quality": {
            "confidence": extracted.confidence,
            "uncertain_fields": list(extracted.uncertain_fields),
            "photo_quality_notes": extracted.photo_quality_notes,
        },
        "confidence": service.confidence_block(ProductConfidence.UNVERIFIED.value),
        "provenance": result.provenance(),
    }


@router.post("/scan/skin-care/label/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_skin_care_label(
    body: ConfirmSkinCareLabelBody,
    device: ScanDevice = Depends(current_device),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Accept a reviewed skin-care transcription as this pack's confirmed label.

    Narrowly operational by design. It reports what was written and where to
    find it, and nothing about what any of it means for the person: no verdict,
    no reason, no release, no personal decision. That is a later milestone, and
    a capture route that hinted at it would be the first place the two got
    tangled.
    """
    capture = await care_capture.confirm_skin_care_label(
        session,
        barcode=body.barcode,
        ai_run_id=body.ai_run_id,
        client_scan_id=body.client_scan_id,
        account_id=current.account_id,
        device=device,
    )
    await session.commit()
    snapshot = capture.label_snapshot
    return {
        "barcode": capture.product_record.barcode,
        "scan_id": str(capture.scan_event.id),
        "created": capture.created,
        "product_category": capture.facts[care_capture.CATEGORY_FACT_KEY],
        "label_snapshot": {
            "id": str(snapshot.id),
            "version_number": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "completeness": snapshot.completeness,
        },
        "confidence": service.confidence_block(capture.product_record.confidence),
        "confirmations": capture.product_record.confirmation_count,
    }
