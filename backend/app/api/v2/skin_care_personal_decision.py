"""The FOR YOU answer for the skin-care pack in somebody's hand.

The first customer surface over the governed Step 8A–8H chain, and deliberately
the narrowest one that can exist. It answers a single question — *what does the
reviewed knowledge say about the pack this device just confirmed, for this
person* — and it derives every input to that question itself.

**The client chooses nothing.** Not the category, not the label version, not the
evidence, not the release, not the reason, not the action, not the citation. It
names a barcode and, optionally, structured safety state. Everything else comes
from rows this server wrote. That is not defensiveness for its own sake: each of
those inputs is an authority somebody reviewed, and a request field that could
override one would let a client assemble a decision nobody approved.

**Authority resolves in one order,** and nothing lower may overrule anything
higher:

```
authenticated account → registered device → device owned by this account
→ this device's current pack → the pack is a confirmed capture
→ the exact label version behind that capture → the category recorded on it
→ Step 8A personal context → Step 8B governed evidence
→ the one active Step 8H release → Steps 8C/8D/8E/8F → reviewed customer copy
```

**Two gates, not one.** Step 8F proves a decision is governed: a reviewed rule,
a reviewed reason, a named openable source. This module adds the second proof —
that the *sentence* has been reviewed too. A key with no approved wording
withholds the whole decision, action and citation together, because a citation
beside a hidden verdict still leaks the shape of an unreviewed claim.

**Nothing here is written.** No decision history, no analytics row, no safety
state. The endpoint reads.

``docs/architecture/CURRENT_PACK_PERSONAL_DECISION_API.md`` has the reasoning at
length.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.product import current_device
from app.content import for_you_copy
from app.domains.personal_applicability.service import interpret_label_snapshot_for_account
from app.domains.personal_decision_explanation import PersonalDecisionPresentationStatus
from app.domains.personal_decision_release.runtime import (
    ReleasedPersonalDecisionResult,
    evaluate_personal_decision_with_release,
    load_active_personal_decision_release,
)
from app.domains.personal_decision_release.validation import (
    PersonalDecisionReleaseInvariantError,
)
from app.domains.personal_lens.enums import PersonalLensStatus
from app.domains.personal_lens.service import PersonalLensSafetyInput
from app.domains.product import care_capture, pack_context
from app.domains.product.models import LabelSnapshot, ScanDevice
from app.domains.product.personal_decision import (
    CurrentPackSnapshotUnresolved,
    resolve_current_pack_label_snapshot,
)
from app.shared.database.sql import get_session
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError
from app.shared.observability import operational_events
from app.shared.security.deps import CurrentAccount, get_current_account

logger = logging.getLogger(__name__)

router = APIRouter()

#: States this API owns, because Step 8F cannot know about them. Everything
#: else in a response's ``status`` is Step 8F's own vocabulary, unrenamed.
STATUS_PACK_NOT_CONFIRMED = "pack_not_confirmed"
STATUS_PACK_CATEGORY_NOT_SUPPORTED = "pack_category_not_supported"
STATUS_NOT_ENOUGH_COPY = "not_enough_copy"

#: True structured flags become these canonical words for the existing
#: hard-handoff authority.
#:
#: The authority in ``routines/hard_handoff.py`` reads language, and it stays
#: the only implementation — a second one would drift, and a safety gate that
#: disagrees with itself is worse than either version alone. This turns
#: structured customer state into the smallest input that authority already
#: recognises, and nothing else. It cannot name a medicine or a diagnosis
#: because the request schema has nowhere to put one.
_SAFETY_TOKENS: tuple[tuple[str, str], ...] = (
    ("pregnancy", "pregnancy"),
    ("breastfeeding", "breastfeeding"),
    ("medication_involved", "medication"),
    ("diagnosed_condition_involved", "diagnosed condition"),
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
class StructuredSafetyContext(BaseModel):
    """Ephemeral safety state, as flags and nothing else.

    Strict booleans throughout: ``"true"``, ``1`` and ``"yes"`` are rejected
    rather than coerced. Whether somebody is pregnant is not a field to be
    lenient about, and a client sending a string where a boolean belongs has a
    bug that should surface here rather than quietly decide a safety gate.

    There is deliberately no field for a medicine name, a diagnosis, a note or
    any free text. The product does not need to know *which* medicine to hand
    over to a clinician, and collecting it would create a health record this
    milestone has no business holding. ``extra="forbid"`` makes an attempt to
    add one a rejection.

    None of this is stored, logged, echoed or counted.
    """

    model_config = ConfigDict(extra="forbid")

    pregnancy: StrictBool | None = None
    breastfeeding: StrictBool | None = None
    medication_involved: StrictBool | None = None
    diagnosed_condition_involved: StrictBool | None = None
    subject_is_child: StrictBool | None = None
    stated_age: Annotated[StrictInt, Field(ge=0, le=120)] | None = None


class SkinCareForYouBody(BaseModel):
    """A barcode, and optionally who is asking about it.

    No category, no label snapshot id, no scan event id, no release id or
    version, no content hash, no ingredients, no action, no reason key. The
    question is about the pack currently in this device's hand; a field that
    could point somewhere else would be a different question with the same
    name.
    """

    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=6, max_length=64)
    safety: StructuredSafetyContext | None = None


def personal_lens_safety_input(
    safety: StructuredSafetyContext | None,
) -> PersonalLensSafetyInput | None:
    """Structured flags in, the existing authority's input out.

    Only ``True`` produces a token: ``False`` and ``None`` are both "nothing
    stated", and inventing a negative assertion from an unanswered question
    would be worse than silence. ``stated_age`` and ``subject_is_child`` pass
    through as the structured fields that authority already trusts over text.
    """
    if safety is None:
        return None
    tokens = [
        token for field, token in _SAFETY_TOKENS if getattr(safety, field) is True
    ]
    return PersonalLensSafetyInput(
        text=". ".join(tokens) if tokens else None,
        stated_age=safety.stated_age,
        subject_is_child=safety.subject_is_child is True,
    )


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class PackProvenance(BaseModel):
    """What was in this device's hand, and which label version speaks for it.

    Two scan ids, because they are two different facts. ``current_pack_scan_id``
    is the confirmation that proves physical possession;
    ``label_snapshot_source_scan_id`` is the capture whose content became the
    semantic version. Deduplication can legitimately make them differ, and
    collapsing them would misreport provenance.
    """

    model_config = ConfigDict(extra="forbid")

    is_proven: bool
    current_pack_scan_id: str | None = None
    label_snapshot_id: str | None = None
    label_snapshot_source_scan_id: str | None = None
    label_snapshot_version: int | None = None
    content_fingerprint: str | None = None


class SourceCitation(BaseModel):
    """The reviewed source, exactly as Step 8F selected it."""

    model_config = ConfigDict(extra="forbid")

    source_key: str
    title: str
    publisher: str
    canonical_url: str
    locator: str | None = None
    publication_date: str | None = None
    version_or_revision: str | None = None
    jurisdiction: str | None = None


class HandoffEnvelope(BaseModel):
    """The canonical hand-over, from the safety authority and nowhere else."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    message: str


class ForYouResult(BaseModel):
    """The answer, or the reviewed reason there is not one.

    Every field that could carry a decision is ``None`` unless the status is
    ``decision_presentable``. A handoff carries its envelope and still no
    action and no citation.
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None
    action: str | None = None
    verdict_key: str | None = None
    verdict_text: str | None = None
    reason_key: str
    reason_text: str
    citation: SourceCitation | None = None
    handoff: HandoffEnvelope | None = None


class ReleaseProvenance(BaseModel):
    """Which reviewed bundle answered. All three together, or all three null."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    version: int | None = None
    content_hash: str | None = None


class SkinCareForYouResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str
    product_category: str | None
    copy_version: str
    pack: PackProvenance
    result: ForYouResult
    release: ReleaseProvenance | None = None


def _pack_provenance(
    pack: pack_context.CurrentPack | None, snapshot: LabelSnapshot | None,
) -> PackProvenance:
    if pack is None or not pack.is_proven or pack.scan_event is None:
        return PackProvenance(is_proven=False)
    if snapshot is None:
        return PackProvenance(
            is_proven=True, current_pack_scan_id=str(pack.scan_event.id),
        )
    return PackProvenance(
        is_proven=True,
        current_pack_scan_id=str(pack.scan_event.id),
        label_snapshot_id=str(snapshot.id),
        label_snapshot_source_scan_id=str(snapshot.scan_event_id),
        label_snapshot_version=snapshot.version_number,
        content_fingerprint=snapshot.content_fingerprint,
    )


def _blocked_result(status: str, reason_key: str) -> ForYouResult:
    """A state with no decision in it. Copy must exist, or this is a defect."""
    text = for_you_copy.reason_text(reason_key)
    if text is None:  # pragma: no cover - the catalogue owns every key used here
        raise AppError(
            "This result is not available right now.",
            status_code=503,
            code=ErrorCode.FEATURE_UNAVAILABLE,
        )
    return ForYouResult(status=status, reason_key=reason_key, reason_text=text)


def build_for_you_response(
    *,
    barcode: str,
    pack: pack_context.CurrentPack | None,
    snapshot: LabelSnapshot | None,
    product_category: str | None,
    released: ReleasedPersonalDecisionResult | None,
) -> SkinCareForYouResponse:
    """Turn a governed result into the customer contract. Pure and synchronous.

    Kept free of the database and of the request so its invariants can be
    exercised directly: that a blocked state never carries an action, that a
    missing sentence withholds the whole decision, and that a citation never
    outlives the verdict it supports.
    """
    provenance = _pack_provenance(pack, snapshot)

    if released is None:
        # A state reached before the engine ran at all: no pack, or a pack
        # whose category this milestone does not answer for.
        reason_key = (
            for_you_copy.REASON_KEY_PACK_CATEGORY
            if provenance.is_proven
            else for_you_copy.REASON_KEY_CONFIRMED_PACK
        )
        status = (
            STATUS_PACK_CATEGORY_NOT_SUPPORTED
            if provenance.is_proven
            else STATUS_PACK_NOT_CONFIRMED
        )
        return SkinCareForYouResponse(
            barcode=barcode,
            product_category=None,
            copy_version=for_you_copy.FOR_YOU_COPY_VERSION,
            pack=provenance,
            result=_blocked_result(status, reason_key),
            release=None,
        )

    presentation = released.presentation
    release = (
        None
        if released.release_id is None
        else ReleaseProvenance(
            id=str(released.release_id),
            version=released.release_version,
            content_hash=released.release_content_hash,
        )
    )

    if presentation.status is PersonalDecisionPresentationStatus.HANDOFF_REQUIRED:
        # The safety authority's own sentence, byte for byte. Not paraphrased,
        # not replaced by a catalogue entry, and carrying none of the flags
        # that triggered it.
        return SkinCareForYouResponse(
            barcode=barcode,
            product_category=product_category,
            copy_version=for_you_copy.FOR_YOU_COPY_VERSION,
            pack=provenance,
            result=ForYouResult(
                status=presentation.status.value,
                reason=presentation.reason.value,
                reason_key=presentation.reason_key,
                reason_text=presentation.handoff_message or "",
                handoff=HandoffEnvelope(
                    reason=presentation.handoff_reason or "",
                    message=presentation.handoff_message or "",
                ),
            ),
            release=release,
        )

    if presentation.status is not PersonalDecisionPresentationStatus.DECISION_PRESENTABLE:
        return SkinCareForYouResponse(
            barcode=barcode,
            product_category=product_category,
            copy_version=for_you_copy.FOR_YOU_COPY_VERSION,
            pack=provenance,
            result=ForYouResult(
                status=presentation.status.value,
                reason=presentation.reason.value,
                reason_key=presentation.reason_key,
                reason_text=for_you_copy.reason_text(presentation.reason_key)
                or for_you_copy.FOR_YOU_REASON_COPY[for_you_copy.REASON_KEY_NO_COPY],
            ),
            release=release,
        )

    # Governed and presentable. Now the second gate: is the wording reviewed?
    verdict = for_you_copy.verdict_text(presentation.verdict_key)
    reason = for_you_copy.reason_text(presentation.reason_key)
    if verdict is None or reason is None:
        # A complete block, citation included. A source beside a withheld
        # verdict still tells the reader what we were about to say.
        return SkinCareForYouResponse(
            barcode=barcode,
            product_category=product_category,
            copy_version=for_you_copy.FOR_YOU_COPY_VERSION,
            pack=provenance,
            result=_blocked_result(
                STATUS_NOT_ENOUGH_COPY, for_you_copy.REASON_KEY_NO_COPY,
            ),
            release=release,
        )

    citation = presentation.citation
    return SkinCareForYouResponse(
        barcode=barcode,
        product_category=product_category,
        copy_version=for_you_copy.FOR_YOU_COPY_VERSION,
        pack=provenance,
        result=ForYouResult(
            status=presentation.status.value,
            reason=presentation.reason.value,
            action=presentation.action.value if presentation.action else None,
            verdict_key=presentation.verdict_key,
            verdict_text=verdict,
            reason_key=presentation.reason_key,
            reason_text=reason,
            citation=None
            if citation is None
            else SourceCitation(
                source_key=citation.source_key,
                title=citation.title,
                publisher=citation.publisher,
                canonical_url=citation.canonical_url,
                locator=citation.locator,
                publication_date=(
                    citation.publication_date.isoformat()
                    if citation.publication_date is not None
                    else None
                ),
                version_or_revision=citation.version_or_revision,
                jurisdiction=citation.jurisdiction,
            ),
        ),
        release=release,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.post("/scan/skin-care/for-you", response_model=SkinCareForYouResponse)
async def read_skin_care_for_you(
    body: SkinCareForYouBody,
    device: ScanDevice = Depends(current_device),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
) -> SkinCareForYouResponse:
    """What the reviewed knowledge says about the pack this device confirmed.

    Reads only. Nothing about this call is written down, including the safety
    flags, which exist for the length of the request and are never stored,
    logged or echoed back.
    """
    # Endpoint health only: did this request finish, and how long did it
    # take. Not the safety flags, not the decision, not the verdict, not
    # the reason, not the citation, not the release, not whether a
    # hand-over to a clinician was required. Counting hand-overs would
    # break the same contract as storing them.
    async with operational_events.observe_for_you():
        care_capture.require_owned_device(device, account_id=current.account_id)

        pack = await pack_context.current_pack(
            session, barcode=body.barcode, device_id=device.id,
        )
        if not pack.is_proven or pack.scan_event is None:
            # Not an error: the person simply has not confirmed what they are
            # holding yet. No release is consulted and no global label is reached
            # for — an answer about some other packet is not a better answer.
            return build_for_you_response(
                barcode=body.barcode, pack=pack, snapshot=None,
                product_category=None, released=None,
            )

        event = pack.scan_event
        if event.account_id is None or event.account_id != current.account_id:
            # Owning the device is not the same as owning the capture on it. A
            # personalised decision must not consume somebody else's confirmation,
            # and this route does not repair the attribution either.
            raise AppError(
                "This pack was confirmed by a different account.",
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                extra={"reason": "current_pack_not_owned"},
            )

        try:
            snapshot = await resolve_current_pack_label_snapshot(session, pack=pack)
        except CurrentPackSnapshotUnresolved:
            # The server said the pack is proven and then could not produce the
            # label version behind it. That is a broken invariant, not a customer
            # state, so it fails closed rather than pretending nothing was
            # confirmed.
            # A fixed event name and nothing else. Removing the identifiers from
            # the format string was not enough: `logger.exception` attaches
            # `exc_info`, and `CurrentPackSnapshotUnresolved` says things like
            # "label snapshot <uuid> does not match the capture that created
            # it" -- so the snapshot id reached the log anyway, through the
            # traceback rather than through the message. The customer already
            # gets a governed fixed 503 here, so the exception text buys nothing
            # that the request id does not. The root logger stamps that id on
            # every line, which is how an operator finds this one.
            logger.error("for_you_snapshot_unresolved")
            raise AppError(
                "This result is not available right now.",
                status_code=503,
                code=ErrorCode.FEATURE_UNAVAILABLE,
            ) from None

        category = care_capture.personal_applicability_category_from_label(snapshot)
        if category is None:
            return build_for_you_response(
                barcode=body.barcode, pack=pack, snapshot=snapshot,
                product_category=None, released=None,
            )

        personal = await interpret_label_snapshot_for_account(
            session,
            snapshot,
            account_id=current.account_id,
            category=category,
            safety=personal_lens_safety_input(body.safety),
        )

        if personal.context_status is PersonalLensStatus.HANDOFF_REQUIRED:
            # The handoff is answered before any release is read. A corrupt or
            # missing release must never be able to suppress a hand-over to a
            # clinician — that is the one answer this product owes unconditionally.
            released = evaluate_personal_decision_with_release(personal, None)
        else:
            try:
                release = await load_active_personal_decision_release(session)
            except PersonalDecisionReleaseInvariantError:
                # A corrupt active release is not the same operational state as no
                # active release, and answering as though it were would show
                # customers "no reviewed knowledge" while a broken bundle sits
                # activated. The detail stays in the log; the customer gets none of
                # the manifest, the hash or the rule identities.
                # Same reasoning. `PersonalDecisionReleaseInvariantError`
                # carries manifest and rule detail in its message, which is
                # exactly what must not reach a log line for a governed 503.
                logger.error("for_you_active_release_invalid")
                raise AppError(
                    "This result is not available right now.",
                    status_code=503,
                    code=ErrorCode.FEATURE_UNAVAILABLE,
                ) from None
            released = evaluate_personal_decision_with_release(personal, release)

        return build_for_you_response(
            barcode=body.barcode,
            pack=pack,
            snapshot=snapshot,
            product_category=category.value,
            released=released,
        )
