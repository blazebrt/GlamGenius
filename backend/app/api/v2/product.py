
"""Scanning a packaged product.

These routes accept an anonymous device token (``X-Device-Token``) as well as a
signed-in account, because the camera opens on first launch with nothing set
up. A device token reaches product data and nothing else.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, VERIFICATION_USER_CONFIRMED, AIRun, AIRunOutput
from app.domains.alternatives import service as alternatives_service
from app.domains.community import service as community_service
from app.domains.media.storage.base import StorageError, StorageMisconfigured
from app.domains.nutrition.grading import from_scan, grade_product, presentation
from app.domains.nutrition.grading.production_rules import (
    enforce_published_required_rules,
    resolve_production_ruleset,
)
from app.domains.official_records import service as official_records_service
from app.domains.product import (
    change_projection,
    complaints,
    devices,
    extraction,
    label_evidence,
    pack_context,
    service,
)
from app.domains.product import watch as product_watch
from app.domains.product.confidence import ProductConfidence
from app.domains.product.fssai import find_licence, is_valid_licence
from app.domains.product.models import FssaiComplaintHandoff, LabelSnapshot, ScanDevice
from app.domains.value import service as value_service
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import (
    StorageMisconfiguredError,
    StorageUnavailableError,
    ValidationFailedError,
)
from app.shared.security.deps import (
    AccountInactiveError,
    CurrentAccount,
    get_current_account,
    get_optional_account,
)
from app.shared.security.network import client_ip
from app.shared.security.rate_limit import FixedWindowLimiter

logger = logging.getLogger(__name__)

router = APIRouter()

#: A photo of a pack, not a photo album. Bigger than this is a mistake.
MAX_REPORT_PHOTO_BYTES = 6 * 1024 * 1024


# The body schemas have always bounded a barcode at 6-64 characters. The
# address did not, so the same value arriving in the path went unchecked into a
# database lookup and an outbound Open Food Facts request. Same product, same
# rule, wherever it arrives.
BARCODE_PATH = Path(..., min_length=6, max_length=64)


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


# Device registration is the one write in this file that takes no credential of
# any kind — it is where a phone gets its first one. Each unknown device_key
# inserts a row, and device_key is chosen by the caller, so without a bound
# anyone can insert rows until the database is full. On the free tier that is a
# cheap outage.
#
# The limit is per address and deliberately loose. Mobile India is largely
# behind carrier-grade NAT: thousands of real people share one public address,
# and a tight limit would lock out a whole carrier to inconvenience one
# attacker. Twenty first launches a minute from a single address is far more
# than a carrier produces and far less than a flood, and it turns "unbounded"
# into a number.
_DEVICE_REGISTRATION_WINDOW_SECONDS = 60.0
_DEVICE_REGISTRATIONS_PER_WINDOW = 20

_device_registration_limiter = FixedWindowLimiter(
    window_seconds=_DEVICE_REGISTRATION_WINDOW_SECONDS,
    max_per_window=_DEVICE_REGISTRATIONS_PER_WINDOW,
)


@router.post("/scan/device", status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegisterBody,
    request: Request,
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
):
    """Register the phone. Called once on first launch, before anything else."""
    # Re-registration of a known device_key proves possession of the current
    # token further down, so only the row-creating path needs bounding — but
    # the check is here, before the lookup, because distinguishing the two
    # would tell an unauthenticated caller which device keys exist.
    if _device_registration_limiter.hit(f"device-register:{client_ip(request) or 'unknown'}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Too many registration attempts. Please wait a moment and try again.",
                "retryable": True,
            },
        )
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
    barcode: str = BARCODE_PATH,
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Ours, then Open Food Facts, then an honest 'not found'.

    Always answers, always with a confidence level.
    """
    result = await service.lookup(session, barcode)
    # Matched against this device's own confirmed capture, never against the
    # newest snapshot for the barcode. That row may be a stranger's photograph
    # of a stranger's packet, and an exact batch recall matched from it would
    # attach somebody else's lot to whoever happened to look the barcode up.
    pack = await pack_context.current_pack(session, barcode=barcode, device_id=device.id)
    result["official_records"] = await official_records_service.official_records_envelope(
        session, pack.label_facts,
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
        # The run and its stored output must agree on which schema produced
        # them, and that one agreed schema must be confirmable. Accepting the
        # previous schema is a kindness to a review a person started before a
        # deployment; accepting a *mismatched pair* would be something else --
        # a payload whose provenance we cannot actually state. Equality first,
        # membership second, so a v1 run carrying a v2 output is refused even
        # though both versions are individually confirmable.
        or run.schema_version != output.schema_version
        or run.schema_version not in extraction.CONFIRMABLE_SCHEMA_VERSIONS
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
    barcode: str = BARCODE_PATH,
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

    The parameter is a **ceiling, not a grant**. A client sets it by default, an
    old build sets it always, and a hostile one sets it deliberately, so it can
    only ever withhold pack authority. The authority itself is established from
    rows this server wrote: this device's own newest scan of this barcode, and
    only when that scan captured the label. The response reports what was
    actually in force rather than what was asked for, so no surface has to
    reconstruct the difference.
    """
    found = await service.lookup(session, barcode)
    snapshot = await service.latest_label_snapshot(session, barcode)
    # A snapshot whose stored facts are not an object is not a readable
    # observation. It is still a real version — decision memory and shelf links
    # are pinned to its id and fingerprint, and hiding it would detach them —
    # so the version is still reported and Step 12A still says its history is
    # unavailable. But nothing is graded from facts that cannot be read: the
    # verdict falls back to what can be, exactly as it does for a product
    # nobody has photographed. No supported write path produces such a row.
    #
    # One boundary, asked once and then carried. Every consumer of this
    # snapshot below — the graded product, the confidence, the pack quantity
    # and the alternative — reads ``readable`` rather than re-deciding, because
    # a second answer to "are these facts readable" is how a corrupt row
    # reaches a ``.get()`` somewhere nobody was looking.
    readable = service.readable_label_snapshot(snapshot)
    # What this server can prove about the packet in this caller's hand, as
    # opposed to what the caller asked to be treated as. The request can only
    # withhold authority; it cannot create it.
    pack = await pack_context.current_pack(session, barcode=barcode, device_id=device.id)
    pack_authority = bool(physical_pack_context) and pack.is_proven
    # Store B is selected at query time and never copied into ODbL Store A.
    # Its schema is adapted explicitly; it is not disguised as an OFF record
    # and missing physical-pack values are never filled from Store A.
    source_half = readable.facts if readable is not None else found.get("open_food_facts")
    # One identity helper, shared with the alternative card, so the name a
    # shopper is offered and the name on the screen it opens cannot drift.
    name, brand = service.result_identity(barcode, source_half)
    product = (
        from_scan.build_confirmed_label(barcode=barcode, facts=readable.facts)
        if readable is not None
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
    # Confidence describes the facts THIS response was graded from, not the
    # best thing known about the barcode. A ``ProductRecord`` marked verified
    # says a reviewer checked a pack; it does not make the catalogue row we
    # fell back to pack-checked, and "Checked by us against the pack" printed
    # over Open Food Facts facts is simply false. So the two fields are decided
    # together and can never contradict each other.
    if readable is not None:
        payload["confidence"] = service.confidence_block(readable.confidence)
        payload["facts_provenance"] = "confirmed_label_snapshot"
    else:
        payload["facts_provenance"] = "open_food_facts"
        payload["confidence"] = service.confidence_block(
            ProductConfidence.UNVERIFIED.value
            if source_half
            else ProductConfidence.NOT_ENOUGH_INFORMATION.value
        )
    # Step 12A history. Two authorities answer two different questions, and the
    # order they run in is the whole design:
    #
    # 1. ``change_projection`` — is the stored history internally valid, and
    #    what do these two observations mean? It runs **first, and always**,
    #    for every request that has a snapshot.
    # 2. ``label_evidence`` — may a valid answer leave this server? A claim
    #    about a manufacturer needs a named, openable source for each
    #    observation it rests on. The confirmed-observation chain persists no
    #    such locator and the only stored image is a private ``MediaAsset``, so
    #    today the answer is always no.
    #
    # A correct internal result may be withheld. A corrupt one must never slip
    # past validation just because publication was going to be withheld anyway:
    # that would leave the integrity boundary behind a gate that is currently
    # always closed, and the day a locator field is added it would open with an
    # unexamined chain behind it. So the projection is never conditioned on the
    # evidence decision, in either direction.
    if snapshot is None:
        # No confirmed pack observation at all — an Open Food Facts record
        # alone never produces one. Deliberately not the same answer as history
        # that exists and is not being characterised; that is ``unavailable``.
        payload["label_version"] = None
        payload["label_change"] = None
    else:
        # Version selection stays explicit: the projection never performs a
        # "latest" lookup, and the predecessor is always the one the row names.
        previous_snapshot = (
            await session.get(LabelSnapshot, snapshot.previous_snapshot_id)
            if snapshot.previous_snapshot_id is not None
            else None
        )
        # First authority: integrity. Unconditional.
        try:
            projection = change_projection.project_label_change(
                current=snapshot, previous=previous_snapshot,
            )
        except change_projection.LabelHistoryInvariantError as broken:
            # An addition may not take the page down with it. Everything below
            # — grade, band, negatives, positives, evidence, official records,
            # alternatives, value — was established without this envelope and
            # is still true, so the history goes quiet and the verdict stands.
            # The broken invariant is named to the log and to nobody else.
            logger.warning(
                "label_history_invariant_failed barcode=%s reason=%s",
                barcode,
                broken.reason,
            )
            projection = None
        # Second authority, asked separately: may a valid result be published?
        publishable = label_evidence.comparison_is_publishable(
            current=snapshot, previous=previous_snapshot,
        )
        # Both, or nothing. Integrity alone is not permission to speak, and
        # evidence alone can never make a corrupt chain publishable.
        disclosed = projection is not None and publishable

        # Version identity, which decision memory and the shelf are pinned to,
        # and which is ours to state: an id, our own counter, our own integrity
        # hash, when we recorded it, and how complete it was. None of that
        # depends on either authority, so none of it is withheld.
        #
        # ``changed_fields`` is not identity. It is the same "this pack differs
        # from that one" assertion ``label_change`` makes, published beside it
        # under a quieter name, so it answers to both authorities too. Withheld
        # as ``None`` — never ``[]``, which would positively assert that no
        # field changed. The stored value is untouched; this is publication.
        payload["label_version"] = {
            "id": str(snapshot.id), "version_number": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "observed_at": snapshot.created_at.isoformat(),
            "changed_fields": snapshot.changed_fields if disclosed else None,
            "completeness": snapshot.completeness,
        }
        payload["label_change"] = (
            projection.as_payload() if disclosed
            else change_projection.UNAVAILABLE_PROJECTION.as_payload()
        )
    payload["attribution"] = found.get("attribution")
    # What the pack actually holds, so "one packet" on the screen means this
    # packet. Absent when neither source states a net quantity, and the screen
    # then says "in 100 g" rather than inventing a pack.
    quantity = (
        (source_half or {}).get("net_quantity")
        if readable is not None
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
    payload["physical_pack_context"] = pack_authority
    # Official records are a separate, additive envelope. Matching is allowed
    # only against this device's own confirmed capture of this pack; OFF cannot
    # supply a licence or batch and therefore cannot manufacture a recall match.
    # The facts come from the pack context rather than from the global newest
    # snapshot, because an exact batch recall matched from a stranger's capture
    # is not this caller's record to be shown — in reference mode, and equally
    # when this device has never photographed this pack at all.
    payload["official_records"] = await official_records_service.official_records_envelope(
        session, pack.label_facts if pack_authority else None,
    )
    # Shopper observations are a fourth, separate layer: not a label fact, not
    # a graded finding, not a government record. They are additive, they never
    # touch anything above, and a batch signal is assembled only for the lot
    # this device itself confirmed. In reference mode the viewer is holding no
    # pack at all, so no device context is supplied: product-scoped observations
    # are facts about the product and stay visible, while a batch signal is
    # about one lot this caller does not have.
    payload["community_observations"] = await community_service.community_observations_envelope(
        session, barcode=barcode, device_id=device.id if pack_authority else None,
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
        # The SAME readable-snapshot authority the verdict above used. Handing
        # the raw row over would put a JSONB array behind a ``.get()`` in the
        # alternative engine and turn a recoverable page into a 500 — and the
        # engine has no business inventing a weaker rule of its own. With no
        # readable confirmed pack it reports not_enough_information, which is
        # the honest answer when the comparison's own requirements are absent.
        current_snapshot=readable,
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
    # Pack facts come from a confirmed observation or from nowhere. A snapshot
    # nobody can read is the second case: every field stays visibly missing,
    # and none of it is filled from Open Food Facts, which cannot state a
    # batch, a licence or what this particular pack said.
    facts = service.readable_label_facts(snapshot)
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
    # Same boundary as the preview: an unreadable observation supplies no pack
    # facts, so the missing-field refusal below is reached rather than a 500,
    # and no handoff is recorded from facts nobody can read.
    fields = complaints.prepared_fields(
        service.readable_label_facts(snapshot), str(body.photo_asset_id),
    )
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

    The photo is read here, before any lock, so a slow upload holds nothing.
    Where it is stored, and whether it is stored at all, is decided by
    :func:`service.file_label_error_report`: idempotency first, the account's
    lifecycle next, then one write under a key the server builds. The caller's
    ``client_report_id`` never names an object.
    """
    if reason not in service.REPORT_REASONS:
        raise ValidationFailedError("That is not a reason we recognise.", field="reason")

    data: bytes | None = None
    content_type: str | None = None
    if photo is not None:
        data = await photo.read()
        if data and len(data) > MAX_REPORT_PHOTO_BYTES:
            raise ValidationFailedError(
                "That photo is too large. Take it again at a smaller size.", field="photo",
            )
        content_type = photo.content_type

    try:
        report, created, written_key = await service.file_label_error_report(
            session,
            device_id=device.id, account_id=device.claimed_by_account_id,
            client_report_id=client_report_id, subject=subject, reason=reason,
            barcode=barcode, photo=data or None, photo_content_type=content_type,
        )
    except service.ReportAccountNotActive:
        # The device's account asked to be deleted first. Nothing was stored.
        raise AccountInactiveError() from None
    except StorageMisconfigured as exc:
        raise StorageMisconfiguredError() from exc
    except StorageError as exc:
        # Before any row: the report is simply not filed, and the phone retries.
        raise StorageUnavailableError() from exc
    # Read before the commit: after a failed one, the session's rows may be
    # expired, and reloading them is not something a failure path should do.
    report_id = report.id
    report_device_id = device.id
    report_photo_key = report.photo_key
    try:
        # Ends the idempotency lock and, for a claimed device, the account
        # hold. A deletion request that arrived meanwhile has been waiting.
        await session.commit()
    except Exception:
        # A raised commit is not a rolled-back one. Ask the database, then act
        # only on what it proves. See service.label_report_commit_outcome.
        outcome = await service.label_report_commit_outcome(
            session, report_id=report_id, device_id=report_device_id,
            client_report_id=client_report_id, photo_key=report_photo_key,
        )
        if outcome is service.ReportCommitOutcome.COMMITTED:
            # Only the acknowledgement was lost. The report, and its photo, are
            # durable: this is the report this request filed.
            return {"report_id": str(report_id), "created": created}
        compensated = False
        if outcome is service.ReportCommitOutcome.NOT_COMMITTED and written_key is not None:
            compensated = await service.discard_unfiled_report_photo(written_key)
        logger.warning(
            "label_report_commit_failed outcome=%s photo_written=%s photo_removed=%s",
            outcome.value, written_key is not None, compensated,
        )
        # The original failure, unchanged: a retryable error, and a retry with
        # the same client_report_id reconciles to whatever is durable.
        raise
    return {"report_id": str(report_id), "created": created}

class ScanDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal['BUY', 'WAIT', 'SKIP']
    label_snapshot_id: uuid.UUID
    label_version: int
    content_fingerprint: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


async def _scan_decision_subject(
    session: AsyncSession,
    current: CurrentAccount,
    subject_id: uuid.UUID | None,
    *,
    for_write: bool = False,
):
    """Turn the optional query parameter into a checked subject, or refuse.

    Omitting it means "me" — the shape every client used before households
    existed. A named subject is a claim, checked against the authenticated
    account: another household's member, a deactivated member and an invented id
    are all the same 404, because telling them apart would confirm that the id
    names somebody real.
    """
    from app.domains.family.decision_subject import (
        canonical_decision_subject,
        decision_subject_for_write,
    )
    from app.domains.family.subject import (
        SubjectNotFound,
        account_holder_subject,
        resolve_subject,
    )
    from app.shared.errors.exceptions import NotFoundError

    try:
        claim = (
            account_holder_subject(current.account_id)
            if subject_id is None
            else await resolve_subject(
                session, account_id=current.account_id, subject_id=subject_id,
            )
        )
        resolve = decision_subject_for_write if for_write else canonical_decision_subject
        return await resolve(
            session, principal_account_id=current.account_id, subject=claim,
        )
    except SubjectNotFound as exc:
        raise NotFoundError("That person is not on this account.") from exc


@router.get("/scan/verdict/{barcode}/memory")
async def get_scan_decision_memory(
    barcode: str = BARCODE_PATH,
    subject_id: uuid.UUID | None = Query(None, description="Whose memory to read; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Read one human's memory for the exact current scanned product version.

    The same label snapshot can carry different answers for different people —
    the account holder waiting, one member buying, another skipping — and this
    returns only the one asked for.
    """
    from fastapi import HTTPException

    from app.domains.product import scan_memory

    snapshot = await service.latest_label_snapshot(session, barcode)
    if not snapshot:
        # Fail closed
        raise HTTPException(status_code=409, detail="conflict")

    decision_subject = await _scan_decision_subject(session, current, subject_id)
    envelope = await scan_memory.read_scan_memory(
        session,
        principal_account_id=current.account_id,
        decision_subject=decision_subject,
        barcode=barcode,
        label_snapshot_id=snapshot.id,
        label_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
    )
    return envelope

@router.post("/scan/verdict/{barcode}/memory")
async def record_scan_decision_event(
    body: ScanDecisionInput,
    barcode: str = BARCODE_PATH,
    subject_id: uuid.UUID | None = Query(None, description="Whose decision this is; omit for yourself"),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Save one human's BUY/WAIT/SKIP for the exact current scanned version.

    Recording that somebody decided to buy something is not recording that they
    own it. Nothing here touches the shelf, for any subject.
    """
    from fastapi import HTTPException

    from app.domains.product import scan_memory

    snapshot = await service.latest_label_snapshot(session, barcode)
    if not snapshot:
        raise HTTPException(status_code=409, detail="conflict")

    if snapshot.id != body.label_snapshot_id or snapshot.version_number != body.label_version or snapshot.content_fingerprint != body.content_fingerprint:
        raise HTTPException(status_code=409, detail="conflict")

    # The subject authority first, in the documented lock order, before
    # anything is written.
    decision_subject = await _scan_decision_subject(
        session, current, subject_id, for_write=True,
    )
    try:
        event = await scan_memory.record_scan_decision(
            session,
            principal_account_id=current.account_id,
            decision_subject=decision_subject,
            barcode=barcode,
            label_snapshot_id=snapshot.id,
            label_version=snapshot.version_number,
            content_fingerprint=snapshot.content_fingerprint,
            decision=body.decision,
            idempotency_key=body.idempotency_key,
            note=body.note,
        )
        await session.commit()
    except scan_memory.ScanDecisionConflict:
        # Deliberately says nothing about which subject used the key, or
        # whether that subject exists.
        raise HTTPException(status_code=409, detail="idempotency_conflict") from None

    return scan_memory.serialize_scan_decision(event)


# ---------------------------------------------------------------------------
# Step 14 — Purchase Operating System (scan)
# ---------------------------------------------------------------------------
@router.get("/scan/verdict/{barcode}/purchase-check")
async def read_scan_purchase_check(
    barcode: str = BARCODE_PATH,
    physical_pack_context: bool = True,
    subject_id: uuid.UUID | None = Query(None, description="Whose purchase context to read; omit for yourself"),
    device: ScanDevice = Depends(current_device),
    current: CurrentAccount | None = Depends(get_optional_account),
    session: AsyncSession = Depends(get_session),
):
    """One purchase answer for this barcode: the Product Result decision, composed.

    The Product Result is built by its own route, in this request, with the
    same pack ceiling — so the base decision, the ruleset, the effective pack
    authority and the official-records envelope are exactly the screen's.
    Nothing here re-grades, re-matches or re-derives any of them.

    Anonymous devices get the decision, the governed official-record ceiling
    and the one alternative, which are free. Memory and ownership are about a
    person, so they need a signed-in account; a named ``subject_id`` without
    one is refused rather than read as the device owner.

    Read-only: no row is created, changed or locked.
    """
    from app.domains.purchase import operating_system
    from app.shared.security.supabase_auth import AuthError

    if current is None and subject_id is not None:
        raise AuthError()
    product_result = await read_product_verdict(
        barcode=barcode, physical_pack_context=physical_pack_context, device=device, session=session,
    )
    decision_subject = (
        await _scan_decision_subject(session, current, subject_id) if current is not None else None
    )
    return await operating_system.scan_purchase_check(
        session,
        barcode=barcode,
        product_result=product_result,
        device=device,
        requested_physical_pack_context=physical_pack_context,
        principal_account_id=current.account_id if current is not None else None,
        decision_subject=decision_subject,
    )


# ---------------------------------------------------------------------------
# Step 12C — Product Watch
# ---------------------------------------------------------------------------
class ProductWatchInput(BaseModel):
    """The one thing the customer tells us: the version they saw.

    Everything else — which capture, which snapshot, which records are already
    known — is resolved here from rows this server wrote. ``label_version`` is
    a compare-and-set against the pack this device proves, so a watch is never
    anchored to a version the customer was not looking at.
    """

    model_config = ConfigDict(extra="forbid")

    label_version: int = Field(ge=1)


async def optional_device(
    x_device_token: str | None = Header(default=None, alias="X-Device-Token"),
    session: AsyncSession = Depends(get_session),
) -> ScanDevice | None:
    """The device, when one is presented. Reading watch state needs no device."""
    return await devices.resolve(session, x_device_token)


def _watch_refused(refusal: product_watch.WatchRefused) -> HTTPException:
    return HTTPException(
        status_code=refusal.status_code,
        detail={"code": refusal.code, "message": refusal.message},
    )


@router.get("/scan/verdict/{barcode}/watch")
async def read_product_watch(
    barcode: str = BARCODE_PATH,
    current: CurrentAccount = Depends(get_current_account),
    device: ScanDevice | None = Depends(optional_device),
    session: AsyncSession = Depends(get_session),
):
    """Whether this account watches this product, and whether this device can.

    Reading changes nothing and sends nothing.
    """
    if not product_watch.watchable_barcode(barcode):
        raise _watch_refused(product_watch.WatchRefused(
            "barcode_not_watchable", "This product cannot be watched.", status_code=422,
        ))
    return await product_watch.watch_state(
        session, account_id=current.account_id, barcode=barcode, device=device,
    )


@router.put("/scan/verdict/{barcode}/watch")
async def start_product_watch(
    body: ProductWatchInput,
    barcode: str = BARCODE_PATH,
    current: CurrentAccount = Depends(get_current_account),
    device: ScanDevice = Depends(current_device),
    session: AsyncSession = Depends(get_session),
):
    """Watch the exact confirmed pack this device holds. Idempotent.

    An explicit request, and the only way a watch begins: a scan, a shelf item,
    a purchase or a saved decision never starts one. Starting a watch sends
    nothing and does not ask for notification permission — everything already
    true of the pack becomes the baseline.
    """
    try:
        state = await product_watch.start_watch(
            session, account_id=current.account_id, barcode=barcode,
            label_version=body.label_version, device=device,
        )
    except product_watch.WatchRefused as refusal:
        raise _watch_refused(refusal) from None
    await session.commit()
    return state


@router.delete("/scan/verdict/{barcode}/watch")
async def stop_product_watch(
    barcode: str = BARCODE_PATH,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Stop watching this product. Idempotent; needs no device."""
    state = await product_watch.stop_watch(session, account_id=current.account_id, barcode=barcode)
    await session.commit()
    return state
