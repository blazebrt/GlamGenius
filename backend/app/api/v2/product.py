"""Scanning a packaged product.

These routes accept an anonymous device token (``X-Device-Token``) as well as a
signed-in account, because the camera opens on first launch with nothing set
up. A device token reaches product data and nothing else.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, VERIFICATION_USER_CONFIRMED, AIRun, AIRunOutput
from app.domains.alternatives import service as alternatives_service
from app.domains.community import service as community_service
from app.domains.media.storage import factory as storage_factory
from app.domains.nutrition.grading import from_scan, grade_product, presentation
from app.domains.nutrition.grading.production_rules import (
    enforce_published_required_rules,
    resolve_production_ruleset,
)
from app.domains.official_records import service as official_records_service
from app.domains.product import complaints, devices, extraction, service
from app.domains.product.confidence import ProductConfidence
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import FssaiComplaintHandoff, ScanDevice
from app.domains.value import service as value_service
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()

#: A photo of a pack, not a photo album. Bigger than this is a mistake.
MAX_REPORT_PHOTO_BYTES = 6 * 1024 * 1024


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
    ai_run_id: uuid.UUID
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
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
):
    """Register the phone. Called once on first launch, before anything else."""
    device, token = await devices.register(
        session, device_key=body.device_key, platform=body.platform, proof_token=x_device_token,
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
    result = await service.lookup(session, barcode)
    snapshot = await service.latest_label_snapshot(session, barcode)
    result["official_records"] = await official_records_service.official_records_envelope(
        session, snapshot.facts if snapshot else None,
    )
    return result


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
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Accept a transcribed label. One tap, the VC-07 draft-to-confirmed pattern."""
    run = await session.get(AIRun, body.ai_run_id)
    output = (await session.execute(select(AIRunOutput).where(AIRunOutput.ai_run_id == body.ai_run_id))).scalar_one_or_none()
    if (
        run is None or output is None or run.account_id != current.account_id
        or run.status != AI_STATUS_SUCCEEDED or run.validation_passed is not True
        or run.feature != extraction.FEATURE
        # Both the current schema and the one before it. A review a person
        # started before a deployment is still their work, and ``mrp_text``
        # being optional is what makes accepting the older payload safe.
        or run.schema_version not in extraction.CONFIRMABLE_SCHEMA_VERSIONS
        or output.schema_version not in extraction.CONFIRMABLE_SCHEMA_VERSIONS
    ):
        raise ValidationFailedError("This label transcription is not available for confirmation.", field="ai_run_id")
    try:
        facts = extraction.ExtractedLabel.model_validate(output.payload).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValidationFailedError("This label transcription is not available for confirmation.", field="ai_run_id") from exc
    # Protect the full Store-B confirmation transaction, including first-time
    # ProductRecord creation and semantic version allocation, across workers.
    await service.lock_label_version(session, body.barcode)
    event, created = await service.record_scan(
        session,
        barcode=body.barcode, outcome=service.OUTCOME_LABEL,
        client_scan_id=body.client_scan_id, device_id=device.id,
        account_id=current.account_id, label_facts=facts,
        ai_run_id=body.ai_run_id,
    )
    # Idempotency is established before changing any fact confidence.  A queued
    # replay therefore cannot create a second confirmation or snapshot.
    if created:
        record = await service.apply_confirmed_label(session, barcode=body.barcode, facts=facts)
        await service.store_label_snapshot(
            session, barcode=body.barcode, facts=facts, device_id=device.id, scan_event_id=event.id,
        )
        output.verification_status = VERIFICATION_USER_CONFIRMED
    else:
        record = await service._own_record(session, body.barcode)
        if record is None:  # defensive: an older malformed event must not gain confidence
            raise ValidationFailedError("The original confirmation has no stored label fact.")
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


@router.get("/scan/verdict/{barcode}")
async def read_product_verdict(
    barcode: str,
    physical_pack_context: bool = True,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """The graded verdict for one barcode, shaped for one screen.

    The two halves are paired here for the length of this response and are not
    written anywhere together — the ODbL wall, same as every other join.

    ``physical_pack_context=false`` is **reference mode**: the caller is reading
    about a product it is not holding, which is what happens when somebody opens
    the comparable alternative from another product's card. Reference mode only
    ever removes authority. The product science is unchanged — that is a fact
    about the product, not about who is looking — but the layers that speak
    about *this packet* fall silent, because the newest label snapshot for a
    barcode may be a stranger's photograph of a stranger's packet and reading it
    as the caller's own would attach somebody else's recall and somebody else's
    lot to a pack this device has never seen.
    """
    found = await service.lookup(session, barcode)
    snapshot = await service.latest_label_snapshot(session, barcode)
    # Store B is selected at query time and never copied into ODbL Store A.
    # Its schema is adapted explicitly; it is not disguised as an OFF record
    # and missing physical-pack values are never filled from Store A.
    source_half = snapshot.facts if snapshot is not None else found.get("open_food_facts")
    # One identity helper, shared with the alternative card, so the name a
    # shopper is offered and the name on the screen it opens cannot drift.
    name, brand = service.result_identity(barcode, source_half)
    product = (
        from_scan.build_confirmed_label(barcode=barcode, facts=snapshot.facts)
        if snapshot is not None
        else from_scan.build(barcode=barcode, name=name, off_half=source_half)
    )
    # The customer path asks the evidence domain which rules have finished the
    # lifecycle. Every row then states its own footing, so a number resting on
    # an unreviewed constant is never shown as though a reviewer stood behind it.
    ruleset = await resolve_production_ruleset(session)
    result = enforce_published_required_rules(grade_product(product), ruleset)
    payload = presentation.present(product, result, ruleset)
    # Identity is factual scan context, not a name inferred by the client.
    # Keep absent catalogue values absent instead of manufacturing a brand.
    payload["barcode"] = barcode
    payload["brand"] = brand
    payload["confidence"] = service.confidence_block(snapshot.confidence) if snapshot else found["confidence"]
    payload["facts_provenance"] = "confirmed_label_snapshot" if snapshot else "open_food_facts"
    payload["label_version"] = ({
        "id": str(snapshot.id), "version_number": snapshot.version_number,
        "observed_at": snapshot.created_at.isoformat(),
        "changed_fields": snapshot.changed_fields,
        "completeness": snapshot.completeness,
    } if snapshot else None)
    payload["attribution"] = found.get("attribution")
    # What the pack actually holds, so "one packet" on the screen means this
    # packet. Absent when neither source states a net quantity, and the screen
    # then says "in 100 g" rather than inventing a pack.
    quantity = (
        (source_half or {}).get("net_quantity")
        if snapshot is not None
        else (source_half or {}).get("quantity")
    )
    size = from_scan.pack_size_g(quantity)
    payload["pack_size_g"] = float(size) if size is not None else None
    # Solid or drink, so the screen can say "100 g" or "100 ml" honestly when
    # there is no pack size to work from.
    payload["basis"] = product.basis
    # Whether the caller is holding this packet. Stated in the response rather
    # than left to the client to remember, so every surface reads it from the
    # same place and a reference view cannot be styled as a scan by accident.
    payload["physical_pack_context"] = bool(physical_pack_context)
    # Official records are a separate, additive envelope. Matching is allowed
    # only against the confirmed physical-pack snapshot; OFF cannot supply a
    # licence or batch and therefore cannot manufacture a recall match. In
    # reference mode no pack facts are passed at all: an exact batch recall from
    # somebody else's capture is not this caller's record to be shown.
    payload["official_records"] = await official_records_service.official_records_envelope(
        session, snapshot.facts if (snapshot and physical_pack_context) else None,
    )
    # Shopper observations are a fourth, separate layer: not a label fact, not
    # a graded finding, not a government record. They are additive, they never
    # touch anything above, and a batch signal is assembled only for the lot
    # this device itself confirmed. In reference mode the viewer is holding no
    # pack at all, so no device context is supplied: product-scoped observations
    # are facts about the product and stay visible, while a batch signal is
    # about one lot this caller does not have.
    payload["community_observations"] = await community_service.community_observations_envelope(
        session, barcode=barcode, device_id=device.id if physical_pack_context else None,
    )
    # A fifth envelope, additive and last: at most one comparable alternative,
    # decided by the same ruleset that graded the product above it. Open Food
    # Facts supplies only which products are the same kind and sold here, read
    # at runtime on barcode and never written back; the candidate's science
    # comes from its own confirmed label, so the card cannot disagree with the
    # screen it opens. No account, no shopper observation, no official record,
    # no AI: free for an anonymous device and identical for everybody.
    payload["alternative"] = await alternatives_service.comparable_alternative_envelope(
        session,
        barcode=barcode,
        current_snapshot=snapshot,
        current_product=product,
        current_result=result,
        ruleset=ruleset,
    )
    # A sixth envelope, and deliberately the last thing computed: what two
    # confirmed pack labels recently stated as their MRP, per 100 g or per
    # 100 ml. It reads the alternative above rather than participating in it —
    # the candidate and the basis are already decided, so a price cannot promote
    # a product, break a tie, or send us looking for a cheaper one the science
    # did not choose. MRP is what a pack declares, never what a shop charges.
    payload["value"] = await value_service.pack_mrp_value_envelope(
        session, barcode=barcode, alternative=payload["alternative"],
    )
    return payload


class FssaiComplaintPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    reason: str = Field(pattern="^(food_safety|label_information|misleading_claim|packaging)$")
    photo_asset_id: uuid.UUID | None = None


@router.post("/reports/fssai/preview")
async def preview_fssai_complaint(
    body: FssaiComplaintPreviewBody,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Return a reviewable request from confirmed pack facts only.

    This does not file anything. Missing pack facts stay visibly missing rather
    than being inferred from an external catalogue.
    """
    del current
    snapshot = await service.latest_label_snapshot(session, body.barcode)
    facts = snapshot.facts if snapshot is not None else {}
    fields = complaints.prepared_fields(facts, str(body.photo_asset_id) if body.photo_asset_id else None)
    return {
        "ready_for_official_handoff": not complaints.missing_preparation_fields(fields),
        "missing_fields": complaints.missing_preparation_fields(fields),
        "reason": body.reason,
        "request_text": complaints.REQUEST_TEMPLATES[body.reason],
        "pack_fields": fields,
        "official_portal_url": complaints.FSSAI_CONSUMER_GRIEVANCE_URL,
        "filing_status": "not_filed",
    }


@router.post("/reports/fssai/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_fssai_complaint_handoff(
    body: FssaiComplaintPreviewBody,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Record that the person reviewed a request before opening FSSAI.

    The browser/app handoff is not a government submission; its status remains
    ``not_filed`` until the person completes the official portal themselves.
    """
    if body.photo_asset_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "photo_required"})
    from app.domains.media import service as media_service

    await media_service.get_owned_asset(session, account_id=current.account_id, asset_id=body.photo_asset_id)
    snapshot = await service.latest_label_snapshot(session, body.barcode)
    fields = complaints.prepared_fields(snapshot.facts if snapshot is not None else {}, str(body.photo_asset_id))
    missing = complaints.missing_preparation_fields(fields)
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "pack_fields_missing", "fields": missing})
    handoff = FssaiComplaintHandoff(
        account_id=current.account_id,
        barcode=body.barcode,
        reason=body.reason,
        product_name=str(fields["product_name"]),
        brand=str(fields["brand"]),
        batch_number=str(fields["batch_number"]),
        fssai_licence=str(fields["fssai_licence"]),
        photo_asset_id=body.photo_asset_id,
        status="official_portal_opened",
        official_portal_opened_at=datetime.now(UTC),
    )
    session.add(handoff)
    await session.commit()
    return {"id": str(handoff.id), "filing_status": "not_filed", "official_portal_url": complaints.FSSAI_CONSUMER_GRIEVANCE_URL}


@router.get("/reports/fssai/public-count")
async def public_fssai_handoff_count(session: AsyncSession = Depends(get_session)):
    """A privacy-preserving count of reviewed official-portal handoffs."""
    count = await session.scalar(select(func.count(FssaiComplaintHandoff.id)))
    return {"reviewed_official_handoffs": int(count or 0), "filed_count_known": False}


@router.post("/reports/label-error", status_code=status.HTTP_201_CREATED)
async def report_label_error(
    client_report_id: str = Form(..., min_length=6, max_length=64),
    subject: str = Form(..., min_length=1, max_length=200),
    reason: str = Form(...),
    barcode: str | None = Form(default=None),
    photo: UploadFile | None = File(default=None),
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Record one error report, with the photo attached inline.

    Multipart rather than JSON because the photo is the point: a person who has
    spotted a wrong number is holding the pack that proves it, and asking them
    to upload it separately loses most of them.

    Reachable with a device token and no account. The person best placed to
    notice a wrong number is somebody standing in a shop who has never signed
    up, and an email address would simply mean never hearing from them.
    """
    if reason not in service.REPORT_REASONS:
        raise ValidationFailedError("That is not a reason we recognise.", field="reason")

    photo_key: str | None = None
    if photo is not None:
        data = await photo.read()
        if data:
            if len(data) > MAX_REPORT_PHOTO_BYTES:
                raise ValidationFailedError(
                    "That photo is too large. Take it again at a smaller size.", field="photo",
                )
            photo_key = f"{service.LABEL_REPORT_PREFIX}/{client_report_id}.jpg"
            await storage_factory.get_storage().put(
                photo_key, data, photo.content_type or "image/jpeg",
            )

    report, created = await service.record_label_error(
        session,
        client_report_id=client_report_id, subject=subject, reason=reason,
        barcode=barcode, photo_key=photo_key,
        device_id=device.id, account_id=device.claimed_by_account_id,
    )
    await session.commit()
    return {"report_id": str(report.id), "created": created}
