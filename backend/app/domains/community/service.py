"""Accepting, withdrawing, moderating and aggregating shopper observations.

The whole point of this module is the distance it keeps. A community report can
never write a label fact, promote a product's confidence, change a grade, or
create an official record. It produces one thing: a count of how many separate
people, each with their own photograph, reported the same visible thing about
the same pack — and only once enough of them have.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
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
# The reporter's own pack
# ---------------------------------------------------------------------------

async def own_confirmed_batch(
    session: AsyncSession, *, barcode: str, device_id: uuid.UUID | None,
) -> tuple[uuid.UUID | None, str | None]:
    """The lot number on the pack *this device* confirmed, and nothing else.

    Not the product's newest label version, which may have been captured by a
    stranger's phone in another city from another lot. Using that would show one
    shopper a warning about a pack they are not holding, which is precisely the
    false positive a batch scope exists to prevent.
    """
    if device_id is None:
        return None, None
    snapshot = (await session.execute(
        select(LabelSnapshot)
        .where(LabelSnapshot.barcode == barcode, LabelSnapshot.device_id == device_id)
        .order_by(LabelSnapshot.version_number.desc())
        .limit(1)
    )).scalar_one_or_none()
    if snapshot is None:
        return None, None
    return snapshot.id, normalise_batch((snapshot.facts or {}).get("batch_number"))


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

async def _assert_scanned(
    session: AsyncSession, *, barcode: str, account_id: uuid.UUID, device_id: uuid.UUID,
) -> None:
    """This account, on this phone, actually passed a scan over this barcode.

    Without it anyone could walk an enumerated barcode list and file reports
    against products they have never held.
    """
    scanned = await session.scalar(
        select(func.count())
        .select_from(ScanEvent)
        .where(
            ScanEvent.barcode == barcode,
            ScanEvent.account_id == account_id,
            ScanEvent.device_id == device_id,
        )
    )
    if not scanned:
        raise CommunityReportRejected(
            REASON_NO_SCAN, "Scan this pack first, then tell us what you saw.",
        )


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


async def _assert_within_rate_limits(
    session: AsyncSession, *, account_id: uuid.UUID, device_id: uuid.UUID, now: datetime,
) -> None:
    """Deterministic, database-backed, and no new dataset about the person.

    Counted from rows we already store, so an idempotent retry — which creates
    no row — costs a reporter nothing.
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

    existing = await _existing_report(session, account_id=account_id, client_report_id=client_report_id)
    if existing is not None:
        if not _same_submission(existing, barcode=barcode, code=observation_code, photo=photo_asset_id):
            raise CommunityReportConflict()
        return existing, False

    await _assert_scanned(session, barcode=barcode, account_id=account_id, device_id=device.id)
    await _assert_photo(session, account_id=account_id, photo_asset_id=photo_asset_id)

    snapshot_id, batch = await own_confirmed_batch(session, barcode=barcode, device_id=device.id)
    if is_batch_scoped(observation_code) and batch is None:
        # Refuse rather than store a pack-condition report that could never
        # become a signal: the person deserves to know their report would go
        # nowhere, and the app can send them to capture the label instead.
        raise CommunityReportRejected(
            REASON_BATCH_CAPTURE_REQUIRED,
            "Capture the pack label first so we can match the batch.",
        )

    await _assert_within_rate_limits(session, account_id=account_id, device_id=device.id, now=utcnow())

    report = CommunityObservationReport(
        account_id=account_id, device_id=device.id, client_report_id=client_report_id,
        barcode=barcode, observation_code=observation_code, photo_asset_id=photo_asset_id,
        label_snapshot_id=snapshot_id, batch_number=batch, status=REPORT_STATUS_ACCEPTED,
    )
    try:
        # A savepoint so a lost race does not poison the caller's transaction.
        async with session.begin_nested():
            session.add(report)
            await session.flush()
    except IntegrityError:
        winner = await _existing_report(
            session, account_id=account_id, client_report_id=client_report_id,
        )
        if winner is None:
            raise
        if not _same_submission(winner, barcode=barcode, code=observation_code, photo=photo_asset_id):
            raise CommunityReportConflict() from None
        return winner, False
    return report, True


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
    """Group into aggregate keys, keeping batches apart and counting people."""
    buckets: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for report, sha256 in rows:
        scope = observation_scope(report.observation_code)
        # A batch signal exists only for its own lot, and is only ever
        # assembled for a viewer holding that same lot.
        if scope == SCOPE_BATCH and (report.batch_number is None or report.batch_number != viewer_batch):
            continue
        key = (report.observation_code, scope, report.batch_number if scope == SCOPE_BATCH else None)
        bucket = buckets.setdefault(key, {"accounts": set(), "photos": set(), "first": None, "last": None})
        bucket["accounts"].add(str(report.account_id))
        bucket["photos"].add(sha256)
        seen_at = report.created_at
        bucket["first"] = seen_at if bucket["first"] is None else min(bucket["first"], seen_at)
        bucket["last"] = seen_at if bucket["last"] is None else max(bucket["last"], seen_at)
    return [
        AggregateEvidence(
            observation_code=code, scope=scope, batch_number=batch,
            reporter_account_ids=frozenset(bucket["accounts"]),
            supporting_photo_hashes=frozenset(bucket["photos"]),
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
    _, viewer_batch = await own_confirmed_batch(session, barcode=barcode, device_id=device_id)
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
