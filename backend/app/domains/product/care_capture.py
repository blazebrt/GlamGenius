"""Turning a reviewed skin-care transcription into a confirmed, category-bound label.

This is the bridge between the phone camera and the governed Step 7/8 chain,
and everything in it exists to make one sentence true: *the facts this snapshot
carries were seen on a physical pack by the person holding it, and the category
they are to be interpreted under was fixed at that moment.*

**The request is never the evidence.** A confirmation body carries three
identifiers and nothing else — no ingredients, no corrections, no category. The
facts come from the stored :class:`AIRun` / :class:`AIRunOutput` pair, re-read
and re-validated here, so a client cannot edit a transcription on its way
through and a corrupted row cannot be confirmed. A person who thinks the
transcription is wrong recaptures the photo; they do not hand us a different
answer and call it a confirmation.

**Category is bound here, once.** ``product_category`` is not a request field,
not a query parameter and not something the model was asked. It is a constant
attached by *this* service, and the only reason it may be attached is that the
caller reached this service at all: the dedicated skin-care confirmation route
is the structured form of a person choosing "Skin Care" and confirming what
they photographed. Every later layer reads that value; none of them may ask to
be given a different one. ``docs/architecture/SKIN_CARE_LABEL_CAPTURE.md``
explains why that asymmetry matters.

**Nothing here decides anything.** No Buy, no Wait, no Skip, no score, no
reason, no release lookup. Capture works identically whether zero or one
personal-decision release is active, because a person confirming what a pack
says is not yet asking what it means for them.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import (
    AI_STATUS_SUCCEEDED,
    VERIFICATION_USER_CONFIRMED,
    AIRun,
    AIRunOutput,
)
from app.domains.personal_applicability.enums import PersonalApplicabilityCategory
from app.domains.product import care_extraction, service
from app.domains.product.care_extraction import ExtractedSkinCareLabel
from app.domains.product.models import LabelSnapshot, ProductRecord, ScanDevice, ScanEvent
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError, ValidationFailedError

#: The label-facts key that carries the decision category, and the one value
#: this milestone may ever write into it.
#:
#: A constant rather than a literal at each use site, because the whole point
#: of the design is that exactly one place in the system decides this.
CATEGORY_FACT_KEY = "product_category"
SKIN_CARE_CATEGORY = "skin_care"

#: What a confirmed skin-care capture may persist, and in what order.
#:
#: Identity, the analytical content, and the category. Deliberately not the
#: model's ``confidence``, ``uncertain_fields``, ``photo_quality_notes`` or
#: ``product_type``: those describe the *photograph and the model's own
#: hesitancy*, not the pack, and a canonical label version is a statement about
#: the pack. They are shown to the person while they review the draft, which is
#: where they are useful, and are left in the AI ledger, which is where they are
#: auditable.
CONFIRMED_FACT_FIELDS = ("product_name", "brand", "ingredients_text")

#: Said to a person whose photograph did not yield a readable ingredient list.
#:
#: Operational, about the photo. It says nothing about the product, because at
#: this point nothing about the product is known.
UNREADABLE_INGREDIENTS_MESSAGE = (
    "We couldn't read the ingredient list clearly. Take another photo and try again."
)

_NOT_CONFIRMABLE = "This skin-care label transcription is not available for confirmation."


@dataclass(frozen=True, slots=True)
class ConfirmedSkinCareCapture:
    """One confirmed skin-care capture, and whether this call is what created it."""

    scan_event: ScanEvent
    created: bool
    product_record: ProductRecord
    label_snapshot: LabelSnapshot
    facts: dict[str, Any]


def confirmed_label_facts(extracted: ExtractedSkinCareLabel) -> dict[str, Any]:
    """The exact facts a confirmed skin-care capture stores.

    Absent identity stays absent rather than becoming an empty string: a pack
    whose brand could not be read has no brand here, and a later layer can tell
    that apart from a pack that declares one.
    """
    facts: dict[str, Any] = {}
    for field in CONFIRMED_FACT_FIELDS:
        value = getattr(extracted, field)
        if value is not None:
            facts[field] = value
    facts[CATEGORY_FACT_KEY] = SKIN_CARE_CATEGORY
    return facts


def has_readable_ingredients(extracted: ExtractedSkinCareLabel) -> bool:
    """Did the photograph yield an ingredient list worth confirming?

    A draft without one is still a legitimate draft — the person tried, and
    telling them so is better than an error. It simply cannot become a
    Step 7/8-capable snapshot, because a formula is the whole analytical
    content of a skin-care label and there is nothing to invent it from.
    """
    value = extracted.ingredients_text
    return isinstance(value, str) and bool(value.strip())


def require_owned_device(device: ScanDevice, *, account_id: uuid.UUID) -> None:
    """A signed-in person may only write a capture onto a device they own.

    Two failures, told apart because the remedies differ. An unclaimed device
    is claimed through the existing claim flow; this route deliberately does not
    auto-claim, because silently attaching a phone to whoever first confirmed a
    label on it is how a shared or borrowed handset acquires the wrong owner.
    A device claimed by somebody else is refused outright: a physical-pack
    capture is an assertion about what *that* device's holder was looking at,
    and letting a second account write one would let a stranger put facts into
    somebody else's pack context.
    """
    if device.claimed_by_account_id is None:
        raise AppError(
            "Attach this phone to your account before confirming a label on it.",
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            extra={"reason": "device_unclaimed"},
        )
    if device.claimed_by_account_id != account_id:
        raise AppError(
            "This phone belongs to a different account.",
            status_code=403,
            code=ErrorCode.FORBIDDEN,
            extra={"reason": "device_claimed_by_another_account"},
        )


async def load_confirmable_transcription(
    session: AsyncSession, *, ai_run_id: uuid.UUID, account_id: uuid.UUID,
) -> ExtractedSkinCareLabel:
    """Re-read and re-validate the stored transcription. Fails closed on every count.

    The run must exist, its output must exist, both must belong to the caller,
    the run must have succeeded and passed validation, it must be a *skin-care*
    transcription, run and output must agree on which schema produced them, and
    that agreed schema must be confirmable.

    Equality before membership, deliberately: a run at one schema carrying an
    output at another is refused even when both versions are individually
    confirmable, because a payload whose provenance we cannot state is not a
    payload we can confirm.

    The payload is then parsed through the schema again rather than trusted as
    stored. Direct database corruption, a hand-written row, or a payload written
    by some other feature all fail here rather than becoming a confirmed pack
    fact.
    """
    run = await session.get(AIRun, ai_run_id)
    output = (await session.execute(
        select(AIRunOutput).where(AIRunOutput.ai_run_id == ai_run_id)
    )).scalar_one_or_none()
    if (
        run is None
        or output is None
        or run.account_id != account_id
        or run.status != AI_STATUS_SUCCEEDED
        or run.validation_passed is not True
        or run.feature != care_extraction.FEATURE
        or run.schema_version != output.schema_version
        or run.schema_version not in care_extraction.CONFIRMABLE_SCHEMA_VERSIONS
    ):
        raise ValidationFailedError(_NOT_CONFIRMABLE, field="ai_run_id")
    try:
        return ExtractedSkinCareLabel.model_validate(output.payload)
    except ValidationError as exc:
        raise ValidationFailedError(_NOT_CONFIRMABLE, field="ai_run_id") from exc


def _material_mismatch(
    event: ScanEvent, *, barcode: str, ai_run_id: uuid.UUID, facts: dict[str, Any],
) -> str | None:
    """Is a replayed idempotency key carrying different physical evidence?

    ``client_scan_id`` exists so an offline queue can send the same confirmation
    twice without writing it twice. It does not exist to let a second, different
    confirmation inherit the first one's identity. A key that has been reused
    for a different barcode, a different transcription, or different facts is
    not a replay of anything — it is a new claim wearing an old name, and
    returning the old row would quietly discard it.
    """
    if event.barcode != barcode:
        return "barcode"
    if event.ai_run_id != ai_run_id:
        return "ai_run_id"
    if event.label_facts != facts:
        return "label_facts"
    return None


async def _effective_snapshot(
    session: AsyncSession, *, event: ScanEvent, barcode: str, facts: dict[str, Any],
) -> LabelSnapshot | None:
    """The label version a replay should report, without writing anything.

    Its own event first, which is exact. Then the same content under another
    event, because Step 3 deduplicates identical label content into a single
    snapshot owned by whoever captured it first, so a capture that arrived
    second legitimately has no row of its own pointing back at it.
    """
    own = (await session.execute(
        select(LabelSnapshot).where(LabelSnapshot.scan_event_id == event.id)
    )).scalar_one_or_none()
    if own is not None:
        return own
    fingerprint = service.label_content_fingerprint(facts)
    return (await session.execute(
        select(LabelSnapshot)
        .where(
            LabelSnapshot.barcode == barcode,
            LabelSnapshot.content_fingerprint == fingerprint,
        )
        .order_by(LabelSnapshot.version_number.desc())
        .limit(1)
    )).scalars().first()


async def confirm_skin_care_label(
    session: AsyncSession,
    *,
    barcode: str,
    ai_run_id: uuid.UUID,
    client_scan_id: str,
    account_id: uuid.UUID,
    device: ScanDevice,
) -> ConfirmedSkinCareCapture:
    """Record one confirmed skin-care capture, once.

    Uses the existing product authorities throughout — ``record_scan``,
    ``apply_confirmed_label`` and ``store_label_snapshot`` — so there is one
    physical-label persistence layer in this product rather than two that
    slowly disagree. The only thing this path adds is the category, and that it
    adds by writing one more key into the facts those authorities already store.

    Does not commit. The caller owns the transaction boundary.
    """
    require_owned_device(device, account_id=account_id)
    extracted = await load_confirmable_transcription(
        session, ai_run_id=ai_run_id, account_id=account_id,
    )
    if not has_readable_ingredients(extracted):
        raise ValidationFailedError(UNREADABLE_INGREDIENTS_MESSAGE, field="ingredients_text")
    facts = confirmed_label_facts(extracted)

    # Protect the whole Store-B confirmation transaction — first-time
    # ProductRecord creation included — across workers, exactly as the food
    # confirmation route does.
    await service.lock_label_version(session, barcode)
    event, created = await service.record_scan(
        session,
        barcode=barcode,
        outcome=service.OUTCOME_LABEL,
        client_scan_id=client_scan_id,
        device_id=device.id,
        account_id=account_id,
        label_facts=facts,
        ai_run_id=ai_run_id,
    )
    if not created:
        mismatch = _material_mismatch(event, barcode=barcode, ai_run_id=ai_run_id, facts=facts)
        if mismatch is not None:
            # A generic 409 rather than :class:`ConflictError`, which is the
            # optimistic-locking error and demands a ``current_version`` that
            # means nothing here. Not retryable: sending the same mismatched
            # request again cannot succeed, and saying otherwise would send a
            # client into a loop.
            raise AppError(
                "This capture id has already been used for a different label.",
                status_code=409,
                code=ErrorCode.CONFLICT,
                retryable=False,
                extra={"conflicting_field": mismatch},
            )
        record = await service._own_record(session, barcode)
        snapshot = await _effective_snapshot(session, event=event, barcode=barcode, facts=facts)
        if record is None or snapshot is None:
            # Defensive: an earlier malformed event must not be replayed into a
            # result that claims a stored label version it does not have.
            raise ValidationFailedError("The original confirmation has no stored label version.")
        return ConfirmedSkinCareCapture(
            scan_event=event, created=False, product_record=record,
            label_snapshot=snapshot, facts=facts,
        )

    record = await service.apply_confirmed_label(session, barcode=barcode, facts=facts)
    snapshot = await service.store_label_snapshot(
        session, barcode=barcode, facts=facts, device_id=device.id, scan_event_id=event.id,
    )
    output = (await session.execute(
        select(AIRunOutput).where(AIRunOutput.ai_run_id == ai_run_id)
    )).scalar_one()
    output.verification_status = VERIFICATION_USER_CONFIRMED
    return ConfirmedSkinCareCapture(
        scan_event=event, created=True, product_record=record,
        label_snapshot=snapshot, facts=facts,
    )


def personal_applicability_category_from_label(
    snapshot: LabelSnapshot,
) -> PersonalApplicabilityCategory | None:
    """The Step 8B category this label version was captured under, or nothing.

    Exact string, no aliases, no fuzzy match, no default. A snapshot that does
    not carry :data:`SKIN_CARE_CATEGORY` in :data:`CATEGORY_FACT_KEY` yields
    ``None``, which is the caller's cue that it is not entitled to interpret
    this formula under any category — not a cue to pick one.

    The value is never inferred from ``product_name`` or from the ingredients.
    Reading "cream" in a product name and concluding skin care is exactly the
    reinterpretation this milestone exists to make impossible; the category is
    a fact about what a person confirmed, and a fact that was not recorded is
    absent, not guessable.

    Legacy food captures predate the category and carry none. They are left
    exactly as they are: absence here means "not established", and reading it
    as ``packaged_food`` would be inventing the very thing this function
    refuses to invent.
    """
    facts = snapshot.facts
    if not isinstance(facts, Mapping):
        return None
    if facts.get(CATEGORY_FACT_KEY) != SKIN_CARE_CATEGORY:
        return None
    return PersonalApplicabilityCategory.SKIN_CARE


__all__ = [
    "CATEGORY_FACT_KEY",
    "CONFIRMED_FACT_FIELDS",
    "SKIN_CARE_CATEGORY",
    "UNREADABLE_INGREDIENTS_MESSAGE",
    "ConfirmedSkinCareCapture",
    "confirm_skin_care_label",
    "confirmed_label_facts",
    "has_readable_ingredients",
    "load_confirmable_transcription",
    "personal_applicability_category_from_label",
    "require_owned_device",
]
