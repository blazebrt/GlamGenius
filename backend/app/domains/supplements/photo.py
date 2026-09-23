"""Reading an owned supplement's label from a photo — into drafts, and nothing more.

Step 13's photo bridge. The customer photographs the label of a supplement they
already recorded; a model transcribes the printed components; each one lands on
that item as a **draft** label fact (``source=photo_extracted``,
``verification_state=draft``) with server-owned provenance. A draft drives
nothing — no overlap, no form, no chemistry, no knowledge — until the customer
confirms it, and confirming never changes its source.

Why not the product label scan
------------------------------
The confirmed product label (``LabelSnapshot``) is a food and skin-care
authority: its schema has no supplement components, and a snapshot feeds food
grading and Product Watch. Forcing supplement components into it would claim a
confirmed pack that was never captured as one and feed a supplement into food
decisions. This bridge therefore reuses only what is generic — the account's
own media, the AI gateway and its run ledger — and writes only to the
account-owned ``supplement_label_components`` table. It creates no scan event,
no snapshot, no product record, no decision and no watch, and reads nothing
from Open Food Facts.

What the model may do
---------------------
Transcribe printed text into four fields per component: ``raw_name``,
``amount``, ``unit``, ``serving_text``. Nothing else exists in the schema, and
the schema forbids extra fields, so an output carrying a nutrient, a compound
form, a hydration state, a benefit, a dose, a use instruction or any other
judgement is refused whole and nothing is written. Unreadable values are
absent, never estimated. The server alone decides the nutrient identity (the
reviewed authority, from the printed name) and the provenance.

Amounts are kept only when the printed text is a plain decimal number. "1,000"
could be one thousand or one; it is recorded as missing, not guessed.

Idempotency
-----------
The request carries a ``client_request_id``. The logical operation is the
triple (item, photo, request id); its drafts are keyed
``photo:<digest>:<index>`` under the existing per-account ``client_mutation_id``
uniqueness, and the whole operation runs under a transaction-scoped advisory
lock on that key. A retry of the same operation returns the drafts the first
one created without calling the model again.

Only an operation that wrote at least one draft is a completed operation. When
the model's valid answer holds no usable component — an empty list, or names
that are blank once trimmed — nothing is written and the route answers
``no_label_details`` (422, retryable), never ``created``: with no durable
receipt, calling it a success would let a lost response turn into a different
result on retry. A retry of that attempt reads the photo again, which is one
more model call and counts against the hourly cap like any other; the gateway's
run ledger keeps the first call as it happened.

Customer copy comes from ``strings.py``; the prompt below is model-facing and
never shown to anyone.
"""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway import gateway
from app.domains.ai_gateway.models import AIRun
from app.domains.media import service as media_service
from app.domains.supplements import strings as copy
from app.domains.supplements.identity import component_identity
from app.domains.supplements.models import SupplementLabelComponent
from app.domains.supplements.service import owned_supplement_item
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import (
    AnalysisUnavailableError,
    AppError,
    UnsupportedMediaTypeError,
    ValidationFailedError,
)
from app.shared.validation.media import sniff_mime

FEATURE = "supplement_label_transcribe"
PROMPT_VERSION = "supplement-label.v1"
SCHEMA_VERSION = "supplement-label.v1"
#: Every draft this bridge writes is keyed under this reserved prefix.
KEY_PREFIX = "photo:"
MAX_COMPONENTS = 40
#: The stable reason for a transcription that found nothing usable.
NO_LABEL_DETAILS = "no_label_details"

SYSTEM = """You transcribe printed text from one photograph of a dietary supplement label.
Copy only what is visibly printed. For each listed component return raw_name, amount,
unit and serving_text exactly as printed, and nothing else. Never add a component that is
not printed. Never estimate or complete a number you cannot read. Never convert units.
Never replace a printed name with a more specific chemical name, salt, hydrate or form,
and never add one that is not printed. Never state what a component is for, what it does,
whether it is safe, how much anyone should take, when to take it, or who should take it.
If a field is unreadable, leave it out."""


def prompt() -> str:
    return """Transcribe the supplement facts or composition table on this label.
Return one JSON object with exactly two keys: components and confidence.
components is a list; each item has raw_name (the component name exactly as printed),
and, only where printed and legible, amount (the number exactly as printed), unit (the
unit exactly as printed) and serving_text (the serving statement exactly as printed, for
example "Each tablet contains"). Do not return any other key anywhere. confidence is a
number from 0 to 1 describing how legible the label was."""


class TranscribedComponent(BaseModel):
    """One printed component. Four fields, all transcriptions; nothing else is allowed."""

    model_config = ConfigDict(extra="forbid")

    raw_name: str = Field(min_length=1, max_length=160)
    amount: str | None = Field(default=None, max_length=32)
    unit: str | None = Field(default=None, max_length=32)
    serving_text: str | None = Field(default=None, max_length=160)


class SupplementLabelTranscription(BaseModel):
    """The whole transcription. An extra key anywhere refuses the result."""

    model_config = ConfigDict(extra="forbid")

    components: list[TranscribedComponent] = Field(default_factory=list, max_length=MAX_COMPONENTS)
    confidence: float | None = Field(default=None, ge=0, le=1)


class NoLabelDetailsError(AppError):
    """The photo was read and held no usable label detail. Not a completed write.

    Retryable: the same photo and request id may be sent again, and that reads
    the photo again. Nothing was written, so there is nothing to replay.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_FAILED
    retryable = True

    def __init__(self) -> None:
        super().__init__(
            copy.text("supplement.photo.no_label_details"),
            extra={"reason": NO_LABEL_DETAILS, "field": "media_asset_id"},
        )


_PLAIN_DECIMAL = re.compile(r"^\d{1,12}(\.\d{1,6})?$")


def printed_amount(value: str | None) -> Decimal | None:
    """The printed number, only when it is unambiguously a plain decimal."""
    text_value = (value or "").strip()
    if not _PLAIN_DECIMAL.fullmatch(text_value):
        return None
    return Decimal(text_value)


def _clean(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


def operation_key(item_id: uuid.UUID, media_asset_id: uuid.UUID, client_request_id: str) -> str:
    """The stable key of one logical transcription: this item, this photo, this request."""
    digest = hashlib.sha256(f"{item_id}|{media_asset_id}|{client_request_id}".encode()).hexdigest()[:40]
    return f"{KEY_PREFIX}{digest}"


async def _existing_drafts(
    session: AsyncSession, account_id: uuid.UUID, key: str,
) -> list[SupplementLabelComponent]:
    return list((await session.execute(
        select(SupplementLabelComponent)
        .where(
            SupplementLabelComponent.account_id == account_id,
            SupplementLabelComponent.client_mutation_id.like(f"{key}:%"),
        )
        .order_by(SupplementLabelComponent.client_mutation_id.asc())
    )).scalars().all())


async def transcribe(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    account_id_str: str,
    item_id: uuid.UUID,
    media_asset_id: uuid.UUID,
    client_request_id: str,
) -> tuple[str, list[SupplementLabelComponent]]:
    """Transcribe one owned photo onto one owned supplement, as drafts.

    Returns ``("created" | "replayed", rows)`` with at least one row. Raises
    404 for an item or photo this account does not own, 415 for bytes that are
    not an image, the gateway's own refusal (without its internal run id) when
    the model's output is malformed, and ``NoLabelDetailsError`` when it is
    well-formed but holds no usable component. Nothing is written in any of
    those cases.
    """
    item = await owned_supplement_item(session, account_id, item_id)
    key = operation_key(item.id, media_asset_id, client_request_id)
    # One logical operation at a time, per key, across processes.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})

    existing = await _existing_drafts(session, account_id, key)
    if existing:
        if any(row.item_id != item.id or row.source != "photo_extracted" for row in existing):
            raise ValidationFailedError(copy.text("supplement.photo.request_reused"), field="client_request_id")
        return "replayed", existing

    asset = await media_service.get_owned_asset(session, account_id=account_id, asset_id=media_asset_id)
    data = await media_service.read_bytes(asset)
    if sniff_mime(data) is None:
        raise UnsupportedMediaTypeError(
            copy.text("supplement.photo.not_an_image"), allowed=["image/jpeg", "image/png", "image/webp"],
        )

    try:
        result = await gateway.run_structured(
            feature=FEATURE,
            prompt=prompt(),
            system=SYSTEM,
            schema=SupplementLabelTranscription,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            account_id_str=account_id_str,
            image_base64=base64.b64encode(data).decode("ascii"),
        )
    except AnalysisUnavailableError as exc:
        # Same refusal, minus the internal run id: the customer payload carries
        # no AI identifier.
        raise AnalysisUnavailableError(
            str(exc),
            failure_type=exc.failure_type,
            guidance=list(exc.extra.get("guidance") or []),
            retryable=exc.retryable,
            ai_run_id=None,
        ) from exc

    usable = [
        (index, component, component.raw_name.strip())
        for index, component in enumerate(result.data.components)
        if component.raw_name.strip()
    ]
    if not usable:
        # A valid answer with nothing usable in it. Not "created": there would be
        # no receipt, so a lost response and its retry could disagree.
        raise NoLabelDetailsError()

    ai_run_id = result.run_id if await session.get(AIRun, result.run_id) else None
    for index, component, raw_name in usable:
        canonical, _display = component_identity(raw_name)
        values: dict[str, Any] = {
            "account_id": account_id,
            "item_id": item.id,
            "raw_name": raw_name[:160],
            "normalized_name": canonical,
            "canonical_component_key": canonical or None,
            "amount": printed_amount(component.amount),
            "unit": _clean(component.unit),
            "serving_text": _clean(component.serving_text),
            # Server-owned provenance. A transcription is a draft until the
            # customer confirms it, whatever the model's confidence.
            "source": "photo_extracted",
            "verification_state": "draft",
            "confidence": result.data.confidence,
            "source_ai_run_id": ai_run_id,
            "model_version": (result.model or "")[:64] or None,
            "prompt_version": result.prompt_version[:32],
            "schema_version": result.schema_version[:32],
            "client_mutation_id": f"{key}:{index:02d}",
        }
        await session.execute(
            pg_insert(SupplementLabelComponent).values(**values).on_conflict_do_nothing(
                index_elements=["account_id", "client_mutation_id"],
            )
        )
    await session.flush()
    return "created", await _existing_drafts(session, account_id, key)


__all__ = [
    "FEATURE",
    "KEY_PREFIX",
    "MAX_COMPONENTS",
    "NO_LABEL_DETAILS",
    "NoLabelDetailsError",
    "PROMPT_VERSION",
    "SCHEMA_VERSION",
    "SupplementLabelTranscription",
    "TranscribedComponent",
    "operation_key",
    "printed_amount",
    "transcribe",
]
