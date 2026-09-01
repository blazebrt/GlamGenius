"""Persistence, normalization, and aggregation for community observations."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.community_signals import (
    ACTIVE_POLICY_WINDOW_DAYS,
    ConditionContext,
    EvidenceWindowSummary,
    ObservationTimingCategory,
    PreparationUseConditionCategory,
    SignalEvidenceSummary,
    StorageConditionCategory,
    evaluate_signal,
    observation_definition,
)
from app.domains.product.models import CommunityObservationReport, ScanDevice
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ConflictError, ValidationFailedError

MAX_REPORTS_PER_DEVICE_PER_HOUR = 30


def normalize_batch_number(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    if len(normalized) > 80:
        raise ValidationFailedError("The batch identifier is too long.", field="batch_number")
    return normalized


def context_from_payload(payload: dict[str, str] | None) -> ConditionContext | None:
    if payload is None:
        return None
    try:
        return ConditionContext(
            storage_condition=StorageConditionCategory(payload["storage_condition"]),
            observation_timing=ObservationTimingCategory(payload["observation_timing"]),
            preparation_or_use_condition=PreparationUseConditionCategory(payload["preparation_or_use_condition"]),
        )
    except (KeyError, ValueError) as exc:
        raise ValidationFailedError("The observation context is not recognised.", field="condition_context") from exc


def context_counts_as_evidence(context: ConditionContext | None) -> bool:
    return context is not None and all(
        value.value != "unknown"
        for value in (context.storage_condition, context.observation_timing, context.preparation_or_use_condition)
    )


def _context_payload(context: ConditionContext | None) -> dict[str, str] | None:
    if context is None:
        return None
    return {
        "storage_condition": context.storage_condition.value,
        "observation_timing": context.observation_timing.value,
        "preparation_or_use_condition": context.preparation_or_use_condition.value,
    }


def normalize_observed_at(value: datetime | None) -> datetime | None:
    """Require an offset and compare event instants at microsecond UTC precision."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationFailedError("The observation time needs a timezone offset.", field="observed_at")
    return value.astimezone(UTC)


def _same_submission(
    row: CommunityObservationReport,
    *, barcode: str, observation_code: str, batch_number: str | None,
    context: ConditionContext | None, photo_asset_id: uuid.UUID | None, observed_at: datetime | None,
) -> bool:
    return (
        row.barcode == barcode.strip()
        and row.observation_code == observation_code
        and row.batch_number == normalize_batch_number(batch_number)
        and row.condition_context == _context_payload(context)
        and row.photo_asset_id == photo_asset_id
        and row.observed_at == observed_at
    )


def assert_same_submission_or_conflict(
    row: CommunityObservationReport,
    *, barcode: str, observation_code: str, batch_number: str | None,
    context: ConditionContext | None, photo_asset_id: uuid.UUID | None, observed_at: datetime | None,
) -> None:
    if not _same_submission(
        row, barcode=barcode, observation_code=observation_code,
        batch_number=batch_number, context=context, photo_asset_id=photo_asset_id,
        observed_at=observed_at,
    ):
        raise ConflictError("This report identifier belongs to a different observation.", current_version=0)


async def submit(
    session: AsyncSession,
    *,
    device: ScanDevice,
    client_report_id: str,
    barcode: str,
    observation_code: str,
    batch_number: str | None,
    condition_context: dict[str, str] | None,
    photo_asset_id: uuid.UUID | None,
    observed_at: datetime | None,
) -> tuple[CommunityObservationReport, bool]:
    """Store once per device/client id; retries return the canonical row."""

    try:
        definition = observation_definition(observation_code)
    except ValueError as exc:
        raise ValidationFailedError("That observation is not recognised.", field="observation_code") from exc
    context = context_from_payload(condition_context)
    if definition.requires_condition_context and context is None:
        raise ValidationFailedError("This observation needs structured context.", field="condition_context")
    observed_at = normalize_observed_at(observed_at)
    if observed_at is not None and observed_at > utcnow() + timedelta(minutes=5):
        raise ValidationFailedError("The observation time cannot be in the future.", field="observed_at")

    existing = (await session.execute(
        select(CommunityObservationReport).where(
            CommunityObservationReport.device_id == device.id,
            CommunityObservationReport.client_report_id == client_report_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        assert_same_submission_or_conflict(
            existing, barcode=barcode, observation_code=observation_code,
            batch_number=batch_number, context=context, photo_asset_id=photo_asset_id, observed_at=observed_at,
        )
        return existing, False

    recent_count = await session.scalar(
        select(func.count(CommunityObservationReport.id)).where(
            CommunityObservationReport.device_id == device.id,
            CommunityObservationReport.created_at >= utcnow() - timedelta(hours=1),
        )
    )
    if int(recent_count or 0) >= MAX_REPORTS_PER_DEVICE_PER_HOUR:
        raise ValidationFailedError("Too many observation reports were sent from this device.", field="client_report_id")

    row = CommunityObservationReport(
        device_id=device.id,
        account_id=device.claimed_by_account_id,
        client_report_id=client_report_id,
        barcode=barcode.strip(),
        observation_code=observation_code,
        batch_number=normalize_batch_number(batch_number),
        photo_asset_id=photo_asset_id,
        condition_context=_context_payload(context),
        observed_at=observed_at,
    )
    try:
        # Keep a competing offline retry local to a savepoint.  Rolling back
        # the request transaction here could discard an unrelated change made
        # by the caller before this service was reached.
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # The DB uniqueness boundary wins a concurrent offline retry.
        existing = (await session.execute(
            select(CommunityObservationReport).where(
                CommunityObservationReport.device_id == device.id,
                CommunityObservationReport.client_report_id == client_report_id,
            )
        )).scalar_one()
        assert_same_submission_or_conflict(
            existing, barcode=barcode, observation_code=observation_code,
            batch_number=batch_number, context=context, photo_asset_id=photo_asset_id, observed_at=observed_at,
        )
        return existing, False
    return row, True


def _report_time(row: CommunityObservationReport) -> datetime:
    """Use pack observation time when supplied; otherwise server creation time."""

    return row.observed_at or row.created_at


def _independent_key(row: CommunityObservationReport) -> tuple[str, str]:
    """V1: one claimed account, otherwise one device, per evidence window."""

    if row.account_id is not None:
        return "account", str(row.account_id)
    return "device", str(row.device_id)


def _window(rows: list[CommunityObservationReport]) -> EvidenceWindowSummary:
    """Choose one canonical row per reporter; rows are not reporter counts."""

    by_identity: dict[tuple[str, str], CommunityObservationReport] = {}
    for row in sorted(rows, key=lambda item: (item.created_at, str(item.id))):
        by_identity.setdefault(_independent_key(row), row)
    reporters = list(by_identity.values())
    batches: defaultdict[str, int] = defaultdict(int)
    photos = 0
    contexts = 0
    for row in reporters:
        if row.batch_number:
            batches[row.batch_number] += 1
        if row.photo_asset_id is not None:
            photos += 1
        if context_counts_as_evidence(context_from_payload(row.condition_context)):
            contexts += 1
    return EvidenceWindowSummary(
        independent_reporters=len(reporters),
        photo_reporters=photos,
        reporters_by_batch=dict(batches),
        condition_context_reporters=contexts,
    )


async def aggregate_evidence(
    session: AsyncSession, *, barcode: str, observation_code: str, now: datetime | None = None,
) -> SignalEvidenceSummary:
    """Build policy input with active/historical rows separated by event time."""

    observation_definition(observation_code)
    now = now or utcnow()
    cutoff = now - timedelta(days=ACTIVE_POLICY_WINDOW_DAYS)
    rows = list((await session.execute(
        select(CommunityObservationReport).where(
            CommunityObservationReport.barcode == barcode,
            CommunityObservationReport.observation_code == observation_code,
            CommunityObservationReport.account_id.is_not(None),
            CommunityObservationReport.status == "accepted",
            CommunityObservationReport.validity_state == "valid",
        )
    )).scalars().all())
    active_rows = [row for row in rows if _report_time(row) >= cutoff]
    historical_rows = [row for row in rows if _report_time(row) < cutoff]
    return SignalEvidenceSummary(active=_window(active_rows), historical=_window(historical_rows))


async def public_signals(session: AsyncSession, *, barcode: str) -> list:
    """Return only aggregate policy decisions; raw observations are private."""

    codes = list((await session.execute(
        select(CommunityObservationReport.observation_code)
        .where(
            CommunityObservationReport.barcode == barcode,
            CommunityObservationReport.status == "accepted",
            CommunityObservationReport.validity_state == "valid",
        )
        .distinct()
    )).scalars().all())
    decisions = []
    for code in codes:
        decision = evaluate_signal(code, await aggregate_evidence(session, barcode=barcode, observation_code=code))
        if decision.public:
            decisions.append(decision)
    return decisions
