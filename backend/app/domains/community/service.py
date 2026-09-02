"""Accepting, withdrawing, moderating and aggregating shopper observations.

The whole point of this module is the distance it keeps. A community report can
never write a label fact, promote a product's confidence, change a grade, or
create an official record. It produces one thing: a count of how many separate
people, each with their own photograph, reported the same visible thing about
the same pack — and only once enough of them have.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.domains.media.models import MEDIA_STATUS_ACTIVE, MediaAsset
from app.domains.product.models import LabelSnapshot, ScanDevice, ScanEvent
from app.shared.database.base import utcnow
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError, NotFoundError, ValidationFailedError

from .models import (
    REPORT_STATUS_ACCEPTED,
    REPORT_STATUS_INVALID,
    REPORT_STATUS_UNDER_REVIEW,
    REPORT_STATUS_WITHDRAWN,
    CommunityObservationReport,
)
from .observations import (
    OBSERVATION_CODES,
    PACK_CONDITION_OBSERVATIONS,
    SCOPE_BATCH,
    is_batch_scoped,
    normalise_batch,
    observation_scope,
)
from .policy import (
    ACTIVE_WINDOW_DAYS,
    COMMUNITY_POLICY_VERSION,
    AggregateEvidence,
    active_window_start,
    evaluate,
    public_display_state,
)

#: The purpose a photo must have been uploaded under. An inventory shelf photo
#: or a face-analysis image is not evidence a shopper offered about a pack, and
#: reusing one would put a picture the person never meant to submit behind a
#: public claim about a brand.
MEDIA_PURPOSE_COMMUNITY_OBSERVATION = "community_observation"

MAX_REPORTS_PER_ACCOUNT_PER_HOUR = 10
MAX_REPORTS_PER_ACCOUNT_PER_DAY = 20
MAX_REPORTS_PER_DEVICE_PER_HOUR = 10


class CommunityReportRejected(AppError):
    """A submission we will not accept, with a key the app can act on."""

    status_code = 422
    code = ErrorCode.VALIDATION_FAILED
    retryable = False

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message, extra={"reason": reason})
        self.reason = reason


class CommunityReportConflict(AppError):
    status_code = 409
    code = ErrorCode.CONFLICT
    retryable = False

    def __init__(self, reason: str = "idempotency_key_reused") -> None:
        super().__init__(
            "That report was already sent with different details.", extra={"reason": reason},
        )
        self.reason = reason


class CommunityRateLimited(AppError):
    status_code = 429
    code = ErrorCode.VALIDATION_FAILED
    retryable = True

    def __init__(self, reason: str) -> None:
        super().__init__("You have sent a lot of reports recently. Please try again later.",
                         extra={"reason": reason})
        self.reason = reason


def is_batch_scoped_codes() -> frozenset[str]:
    """Which codes the picker must warn will need the pack label captured."""
    return PACK_CONDITION_OBSERVATIONS


REASON_UNKNOWN_OBSERVATION = "unknown_observation_code"
REASON_DEVICE_NOT_CLAIMED = "device_not_claimed_by_account"
REASON_NO_SCAN = "no_scan_for_this_pack"
REASON_PHOTO_REQUIRED = "photo_required"
REASON_BATCH_CAPTURE_REQUIRED = "batch_capture_required"


# ---------------------------------------------------------------------------
# The pack in this person's hand, right now
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PackContext:
    """What this device is currently holding, as far as the server can tell."""

    scan_event: ScanEvent | None = None
    batch_number: str | None = None
    label_snapshot_id: uuid.UUID | None = None

    @property
    def has_scan(self) -> bool:
        return self.scan_event is not None


async def current_pack_event(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> ScanEvent | None:
    """The newest scan this device made of this barcode. Not the newest capture.

    Ordered by server time, then by id to break a tie deterministically — never
    by the client's ``scanned_at``, which an offline queue may backdate and a
    hostile client may choose.
    """
    if device_id is None:
        return None
    return (await session.execute(
        select(ScanEvent)
        .where(ScanEvent.barcode == barcode, ScanEvent.device_id == device_id)
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
        .limit(1)
    )).scalars().first()


async def current_pack_context(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> PackContext:
    """The lot number of the pack this device last scanned, if it captured one.

    Two rules, and the second is the one that matters:

    The batch comes from this device's own capture, never from the product's
    newest ``LabelSnapshot`` — that row may be a stranger's photograph of a
    stranger's packet, and Step 3 deduplicates identical label content into one
    snapshot owned by whoever captured it first, so ownership of it was never
    the question.

    And it comes from the *newest* scan only. A plain scan of the same barcode
    means a different physical packet is in this person's hand now, and its lot
    is unknown until they capture it. Reaching past that scan to an older
    capture would attach a report about today's packet to last month's lot, and
    would keep showing this shopper a signal about a pack they put back on the
    shelf.
    """
    event = await current_pack_event(session, barcode=barcode, device_id=device_id)
    if event is None:
        return PackContext()
    facts = event.label_facts
    if not isinstance(facts, dict) or not facts:
        # A plain scan. A new packet, and no lot until it is captured.
        return PackContext(scan_event=event)
    batch = normalise_batch(facts.get("batch_number"))
    # Provenance is the snapshot Step 3 allocated *for this exact event*, or
    # nothing. Matching on content fingerprint would be a lie: Step 3
    # deliberately excludes batch_number from the semantic fingerprint, so two
    # packets from lots B1 and B2 share one fingerprint by design, and pointing
    # at the older physical capture would claim provenance we do not have.
    snapshot_id = await session.scalar(
        select(LabelSnapshot.id).where(LabelSnapshot.scan_event_id == event.id)
    )
    return PackContext(scan_event=event, batch_number=batch, label_snapshot_id=snapshot_id)


async def pack_context_payload(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> dict[str, Any]:
    """What the app needs to know before offering a batch-scoped observation.

    Server-authoritative on purpose: the client must not guess whether this
    device has a current lot, and must never be given anybody else's.
    """
    context = await current_pack_context(session, barcode=barcode, device_id=device_id)
    return {
        "barcode": barcode,
        "has_current_scan_context": context.has_scan,
        "batch_context_available": context.batch_number is not None,
        "batch_number": context.batch_number,
        "batch_scoped_observation_codes": sorted(PACK_CONDITION_OBSERVATIONS),
    }


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

async def _assert_photo(
    session: AsyncSession, *, account_id: uuid.UUID, photo_asset_id: uuid.UUID,
) -> MediaAsset:
    """An active image this account uploaded for this purpose. Nothing else.

    Note what this does *not* do: it does not look at the picture. A photo makes
    a report answerable, it does not verify it, and no model is asked whether
    the image proves anything.
    """
    asset = (await session.execute(
        select(MediaAsset).where(
            MediaAsset.id == photo_asset_id,
            MediaAsset.account_id == account_id,
            MediaAsset.status == MEDIA_STATUS_ACTIVE,
        )
    )).scalar_one_or_none()
    if asset is None:
        raise NotFoundError("We could not find that photo.")
    if asset.purpose != MEDIA_PURPOSE_COMMUNITY_OBSERVATION:
        raise CommunityReportRejected(
            REASON_PHOTO_REQUIRED, "Add a photo of what you saw on the pack.",
        )
    if not asset.content_type.startswith("image/"):
        raise CommunityReportRejected(
            REASON_PHOTO_REQUIRED, "Add a photo of what you saw on the pack.",
        )
    return asset


async def _lock_reporter(session: AsyncSession, *, account_id: uuid.UUID, device_id: uuid.UUID) -> None:
    """Serialize this account's and this device's submissions in PostgreSQL.

    Counting rows and then inserting is not a limit: several requests can all
    read nine and all pass a limit of ten. The locks are transaction-scoped and
    always taken account-then-device, so two requests sharing both can never
    deadlock by taking them in opposite orders.
    """
    for name in (f"community_reporter:{account_id}", f"community_device:{device_id}"):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), {"name": name},
        )


async def _assert_within_rate_limits(
    session: AsyncSession, *, account_id: uuid.UUID, device_id: uuid.UUID, now: datetime,
) -> None:
    """Deterministic, database-backed, and no new dataset about the person.

    Counted from rows we already store. Callers hold the reporter locks, so the
    count cannot go stale between here and the insert.
    """
    async def _count(*conditions) -> int:
        return int(await session.scalar(
            select(func.count()).select_from(CommunityObservationReport).where(*conditions)
        ) or 0)

    hour, day = now - timedelta(hours=1), now - timedelta(days=1)
    if await _count(
        CommunityObservationReport.account_id == account_id,
        CommunityObservationReport.created_at >= hour,
    ) >= MAX_REPORTS_PER_ACCOUNT_PER_HOUR:
        raise CommunityRateLimited("account_hourly_limit")
    if await _count(
        CommunityObservationReport.account_id == account_id,
        CommunityObservationReport.created_at >= day,
    ) >= MAX_REPORTS_PER_ACCOUNT_PER_DAY:
        raise CommunityRateLimited("account_daily_limit")
    if await _count(
        CommunityObservationReport.device_id == device_id,
        CommunityObservationReport.created_at >= hour,
    ) >= MAX_REPORTS_PER_DEVICE_PER_HOUR:
        raise CommunityRateLimited("device_hourly_limit")


def _same_submission(report: CommunityObservationReport, *, barcode: str, code: str, photo: uuid.UUID) -> bool:
    return (
        report.barcode == barcode
        and report.observation_code == code
        and report.photo_asset_id == photo
    )


def _resolve_retry(
    existing: CommunityObservationReport, barcode: str, code: str, photo: uuid.UUID,
) -> CommunityObservationReport:
    """Same key, same content is the same report. Same key, different content is a bug."""
    if not _same_submission(existing, barcode=barcode, code=code, photo=photo):
        raise CommunityReportConflict()
    return existing


async def _existing_report(
    session: AsyncSession, *, account_id: uuid.UUID, client_report_id: str,
) -> CommunityObservationReport | None:
    return (await session.execute(
        select(CommunityObservationReport).where(
            CommunityObservationReport.account_id == account_id,
            CommunityObservationReport.client_report_id == client_report_id,
        )
    )).scalar_one_or_none()


async def submit_observation(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    device: ScanDevice,
    barcode: str,
    observation_code: str,
    photo_asset_id: uuid.UUID,
    client_report_id: str,
) -> tuple[CommunityObservationReport, bool]:
    """Record one observation. Returns the report and whether it was created now."""
    if observation_code not in OBSERVATION_CODES:
        raise ValidationFailedError("That is not an observation we accept.", field="observation_code")
    # A device nobody has claimed is not a person. Public influence over a
    # brand's reputation is not something an unclaimed handset gets to have.
    if device.claimed_by_account_id != account_id:
        raise CommunityReportRejected(
            REASON_DEVICE_NOT_CLAIMED, "Sign in on this phone before sending a report.",
        )

    # Cheap path first: an offline queue re-sending a report it already sent
    # should not wait on a lock, and should never be told it is rate limited.
    existing = await _existing_report(session, account_id=account_id, client_report_id=client_report_id)
    if existing is not None:
        return _resolve_retry(existing, barcode, observation_code, photo_asset_id), False

    await _assert_photo(session, account_id=account_id, photo_asset_id=photo_asset_id)
    context = await current_pack_context(session, barcode=barcode, device_id=device.id)
    event = context.scan_event
    # The report is anchored to the exact scan that established its context, and
    # that scan must be this person's, on this phone, of this pack.
    if event is None or event.account_id != account_id or event.barcode != barcode:
        raise CommunityReportRejected(
            REASON_NO_SCAN, "Scan this pack first, then tell us what you saw.",
        )
    if is_batch_scoped(observation_code) and context.batch_number is None:
        # Refuse rather than store a pack-condition report that could never
        # become a signal: the person deserves to know their report would go
        # nowhere, and the app can send them to capture the label instead.
        raise CommunityReportRejected(
            REASON_BATCH_CAPTURE_REQUIRED,
            "Capture the pack label first so we can match the batch.",
        )

    await _lock_reporter(session, account_id=account_id, device_id=device.id)
    # Re-read behind the lock: a concurrent copy of this same retry may have
    # won while we waited, and it must still resolve as a retry rather than be
    # refused for consuming the quota slot it is itself occupying.
    winner = await _existing_report(session, account_id=account_id, client_report_id=client_report_id)
    if winner is not None:
        return _resolve_retry(winner, barcode, observation_code, photo_asset_id), False
    await _assert_within_rate_limits(session, account_id=account_id, device_id=device.id, now=utcnow())

    report = CommunityObservationReport(
        account_id=account_id, device_id=device.id, client_report_id=client_report_id,
        barcode=barcode, observation_code=observation_code, photo_asset_id=photo_asset_id,
        scan_event_id=event.id, label_snapshot_id=context.label_snapshot_id,
        batch_number=context.batch_number, status=REPORT_STATUS_ACCEPTED,
    )
    try:
        # A savepoint so a lost race does not poison the caller's transaction.
        async with session.begin_nested():
            session.add(report)
            await session.flush()
    except IntegrityError:
        raced = await _existing_report(
            session, account_id=account_id, client_report_id=client_report_id,
        )
        if raced is None:
            raise
        return _resolve_retry(raced, barcode, observation_code, photo_asset_id), False
    return report, True


async def own_reports_for_barcode(
    session: AsyncSession, *, account_id: uuid.UUID, barcode: str,
) -> list[CommunityObservationReport]:
    """This account's own reports about one barcode, so they can withdraw one.

    Their rows only. Not a feed, not a history of anybody else, not a profile —
    the single purpose is letting a person manage the content they created.
    """
    return list((await session.execute(
        select(CommunityObservationReport)
        .where(
            CommunityObservationReport.account_id == account_id,
            CommunityObservationReport.barcode == barcode,
        )
        .order_by(CommunityObservationReport.created_at.desc())
    )).scalars().all())


async def withdraw_observation(
    session: AsyncSession, *, account_id: uuid.UUID, report_id: uuid.UUID,
) -> CommunityObservationReport:
    """A shopper retracts their own observation. Only ever their own."""
    report = (await session.execute(
        select(CommunityObservationReport).where(
            CommunityObservationReport.id == report_id,
            CommunityObservationReport.account_id == account_id,
        )
    )).scalar_one_or_none()
    if report is None:
        raise NotFoundError("We could not find that report.")
    if report.status != REPORT_STATUS_WITHDRAWN:
        report.status = REPORT_STATUS_WITHDRAWN
        report.withdrawn_at = utcnow()
        await session.flush()
    return report


async def moderate_observation(
    session: AsyncSession, *, report_id: uuid.UUID, status: str, moderation_reason: str,
) -> CommunityObservationReport:
    """An administrator stops a demonstrably bad report from contributing."""
    if status not in (REPORT_STATUS_UNDER_REVIEW, REPORT_STATUS_INVALID, REPORT_STATUS_ACCEPTED):
        raise ValidationFailedError("That is not a moderation status we accept.", field="status")
    report = await session.get(CommunityObservationReport, report_id)
    if report is None:
        raise NotFoundError("We could not find that report.")
    report.status = status
    report.moderation_reason = moderation_reason
    await session.flush()
    return report


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

async def _qualifying_rows(session: AsyncSession, *, barcode: str, now: datetime) -> list[Any]:
    """Reports that currently count: accepted, inside the window, photo intact.

    Every disqualification is applied by reading the world as it is now rather
    than from a cached count, so a withdrawal, a moderation, a deleted photo or
    a deleted account takes effect on the next request instead of leaving a
    stale public claim standing.
    """
    return list((await session.execute(
        select(CommunityObservationReport, MediaAsset.sha256)
        .join(MediaAsset, MediaAsset.id == CommunityObservationReport.photo_asset_id)
        .where(
            CommunityObservationReport.barcode == barcode,
            CommunityObservationReport.status == REPORT_STATUS_ACCEPTED,
            CommunityObservationReport.created_at >= active_window_start(now),
            MediaAsset.status == MEDIA_STATUS_ACTIVE,
        )
    )).all())


def _aggregate(rows: list[Any], *, viewer_batch: str | None) -> list[AggregateEvidence]:
    """Group into aggregate keys, keeping batches apart and keeping the pairing.

    Each reporter carries the photographs *they* supplied, so the policy can ask
    whether three people independently evidenced this rather than whether three
    names and three hashes happen to appear.
    """
    buckets: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for report, sha256 in rows:
        scope = observation_scope(report.observation_code)
        # A batch signal exists only for its own lot, and is only ever
        # assembled for a viewer holding that same lot.
        if scope == SCOPE_BATCH and (report.batch_number is None or report.batch_number != viewer_batch):
            continue
        key = (report.observation_code, scope, report.batch_number if scope == SCOPE_BATCH else None)
        bucket = buckets.setdefault(key, {"reporters": {}, "first": None, "last": None})
        bucket["reporters"].setdefault(str(report.account_id), set()).add(sha256)
        seen_at = report.created_at
        bucket["first"] = seen_at if bucket["first"] is None else min(bucket["first"], seen_at)
        bucket["last"] = seen_at if bucket["last"] is None else max(bucket["last"], seen_at)
    return [
        AggregateEvidence(
            observation_code=code, scope=scope, batch_number=batch,
            reporter_photo_hashes={
                account: frozenset(photos) for account, photos in bucket["reporters"].items()
            },
            first_reported_at=bucket["first"], last_reported_at=bucket["last"],
        )
        for (code, scope, batch), bucket in buckets.items()
    ]


def _public_signal(decision) -> dict[str, Any]:
    """The public shape. No report id, no account, no device, no photo, no hash."""
    return {
        "observation_code": decision.observation_code,
        "scope": decision.scope,
        "batch_number": decision.batch_number,
        "independent_reporters": decision.independent_reporters,
        "first_reported_at": decision.first_reported_at.isoformat() if decision.first_reported_at else None,
        "last_reported_at": decision.last_reported_at.isoformat() if decision.last_reported_at else None,
        "analysis_score_eligible": decision.analysis_score_eligible,
        "official_finding": decision.official_finding,
    }


def _ordered(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Product-data observations first, then the viewer's batch; newest first
    inside each group, then a stable code order.

    Deliberately not a severity ranking. This system does not decide which
    observation is more alarming than another — that judgement is exactly the
    step from observation to conclusion it exists to avoid. Stable sorts are
    applied in reverse order of precedence.
    """
    ordered = sorted(signals, key=lambda signal: signal["observation_code"])
    ordered.sort(key=lambda signal: signal["last_reported_at"] or "", reverse=True)
    ordered.sort(key=lambda signal: 0 if signal["scope"] != SCOPE_BATCH else 1)
    return ordered


async def community_observations_envelope(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> dict[str, Any]:
    """The additive Product Result block. Silent when there is nothing to say.

    An empty ``signals`` list is not a clean bill of health and no caller may
    render it as one: it equally means below threshold, outside the window,
    display switched off, or a batch signal belonging to a lot this shopper is
    not holding. Absence of a public signal is not evidence of absence.
    """
    enabled, _ = public_display_state(
        enabled=config.COMMUNITY_PUBLIC_SIGNALS_ENABLED,
        brand_reply_url=config.COMMUNITY_BRAND_REPLY_URL,
    )
    envelope: dict[str, Any] = {
        "policy_version": COMMUNITY_POLICY_VERSION,
        "public_enabled": enabled,
        "active_window_days": ACTIVE_WINDOW_DAYS,
        "brand_reply_url": config.COMMUNITY_BRAND_REPLY_URL if enabled else None,
        "signals": [],
    }
    if not enabled:
        return envelope
    now = utcnow()
    viewer_batch = (await current_pack_context(
        session, barcode=barcode, device_id=device_id,
    )).batch_number
    rows = await _qualifying_rows(session, barcode=barcode, now=now)
    decisions = [evaluate(evidence) for evidence in _aggregate(rows, viewer_batch=viewer_batch)]
    envelope["signals"] = _ordered([_public_signal(d) for d in decisions if d.public])
    return envelope


def to_public_report(report: CommunityObservationReport) -> dict[str, Any]:
    """What the reporter themselves gets back. Their own row, no aggregate."""
    return {
        "id": str(report.id),
        "barcode": report.barcode,
        "observation_code": report.observation_code,
        "scope": observation_scope(report.observation_code),
        "batch_number": report.batch_number,
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "withdrawn_at": report.withdrawn_at.isoformat() if report.withdrawn_at else None,
    }
