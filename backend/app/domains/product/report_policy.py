"""Beta label-report resource ceilings; database quotas are authoritative.

Lifetime retained-row quotas do not reset on restart/window rollover. Resolved
reports still count. Anonymous reports belong to the device, never its claimant.
Account quotas apply in addition to device quotas across authenticated devices.
These are safety limits, not entitlement/payment restrictions.
"""
from __future__ import annotations

import uuid

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import LabelErrorReport
from app.shared.security.rate_limit import FixedWindowLimiter

MAX_REPORT_PHOTO_BYTES = 6 * 1024 * 1024
REPORT_READ_CHUNK_BYTES = 64 * 1024
# Invite/beta retained-resource budgets: allow substantial correction work,
# but only ten/twenty full-sized photos per device/account across restarts.
DEVICE_REPORT_COUNT_LIMIT = 100
DEVICE_REPORT_BYTE_LIMIT = 60 * 1024 * 1024
ACCOUNT_REPORT_COUNT_LIMIT = 200
ACCOUNT_REPORT_BYTE_LIMIT = 120 * 1024 * 1024
# Cheap process-local flood protection supplements, never replaces, DB quotas.
REPORT_RATE_WINDOW_SECONDS = 3600
REPORT_IP_RATE_LIMIT = 60
REPORT_DEVICE_RATE_LIMIT = 20
REPORT_ACCOUNT_RATE_LIMIT = 30
REPORT_RATE_MAX_KEYS = 4096

report_limiter = FixedWindowLimiter(
    window_seconds=REPORT_RATE_WINDOW_SECONDS,
    max_per_window=REPORT_DEVICE_RATE_LIMIT,
    max_keys=REPORT_RATE_MAX_KEYS,
)


class ReportQuotaExceeded(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def lock_report_quotas(session: AsyncSession, *, device_id: uuid.UUID, account_id: uuid.UUID | None) -> None:
    """After report identity and account lifecycle; before any row/device flush.

    All quota keys are sorted lexically (account before device). Deletion takes
    no quota/identity lock, and writers never acquire an account hold after a
    quota/device lock. Locks end with the filing transaction, not the savepoint.
    """
    keys = [f"label-report-quota:device:{device_id}"]
    if account_id is not None:
        keys.append(f"label-report-quota:account:{account_id}")
    for key in sorted(keys):
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


async def admit_report(session: AsyncSession, *, device_id: uuid.UUID, account_id: uuid.UUID | None, photo_bytes: int) -> None:
    """Call under quota locks. No reservation, row, audit or storage side effect.

    At READ COMMITTED the aggregate after the lock sees the prior holder's
    committed row. Old photos of unknown size cost the full existing 6 MiB cap.
    An absent photo costs zero, regardless of stale legacy size metadata.
    """
    photo_cost = case(
        (LabelErrorReport.photo_key.is_(None), 0),
        else_=func.coalesce(LabelErrorReport.photo_byte_size, MAX_REPORT_PHOTO_BYTES),
    )
    scopes = [("device", LabelErrorReport.device_id == device_id, DEVICE_REPORT_COUNT_LIMIT, DEVICE_REPORT_BYTE_LIMIT)]
    if account_id is not None:
        scopes.append(("account", LabelErrorReport.account_id == account_id, ACCOUNT_REPORT_COUNT_LIMIT, ACCOUNT_REPORT_BYTE_LIMIT))
    for name, predicate, count_limit, byte_limit in scopes:
        count, size = (await session.execute(
            select(func.count(LabelErrorReport.id), func.coalesce(func.sum(photo_cost), 0)).where(predicate)
        )).one()
        if count >= count_limit:
            raise ReportQuotaExceeded(f"{name}_report_count_limit")
        if size + photo_bytes > byte_limit:
            raise ReportQuotaExceeded(f"{name}_report_photo_byte_limit")
