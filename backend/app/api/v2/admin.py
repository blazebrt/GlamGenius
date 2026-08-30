from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.system.models import WorkerStatus
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(current: CurrentAccount = Depends(get_current_account)) -> CurrentAccount:
    if not current.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ADMIN_REQUIRED",
                "message": "Administrative privileges required.",
            },
        )
    return current


# Workers a scheduler must invoke, and how often, in seconds. Nothing in this
# repository can install a schedule; naming the expectation is what lets the
# endpoint say a run was missed.
SCHEDULED_WORKERS = {
    "notification_worker": 3600,
}

# How late a run may be before it counts as missed. One extra interval absorbs
# a slow run or a scheduler that fires a little late, without hiding a worker
# that has genuinely stopped.
_MISSED_GRACE = 2


def _freshness(worker, now) -> dict:
    """Age of the last run, so staleness is readable without date arithmetic."""
    last = worker.last_heartbeat_at
    return {
        "last_heartbeat_age_seconds": int((now - last).total_seconds()) if last else None,
    }


def _scheduled_state(name: str, interval_seconds: int, worker, now) -> dict:
    """Has this scheduled worker actually run recently enough?"""
    if worker is None or worker.last_heartbeat_at is None:
        return {
            "worker_name": name,
            "expected_interval_seconds": interval_seconds,
            "state": "never_run",
            "last_heartbeat_age_seconds": None,
            "detail": (
                f"{name} has never reported a run. Its schedule is probably not "
                "installed — see docs/OPERATIONS.md section 6."
            ),
        }
    age = int((now - worker.last_heartbeat_at).total_seconds())
    overdue = age > interval_seconds * _MISSED_GRACE
    failing = worker.last_error_code is not None and (
        worker.last_successful_job_at is None
        or (worker.last_error_at is not None and worker.last_error_at >= worker.last_successful_job_at)
    )
    if overdue:
        state, detail = "missed", f"Last run was {age}s ago; expected every {interval_seconds}s."
    elif failing:
        state, detail = "failing", f"Last run reported {worker.last_error_code}: {worker.last_error_summary}"
    else:
        state, detail = "healthy", f"Last run {age}s ago."
    return {
        "worker_name": name,
        "expected_interval_seconds": interval_seconds,
        "state": state,
        "last_heartbeat_age_seconds": age,
        "detail": detail,
    }


@router.get("/workers")
async def list_workers(
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """List system worker statuses. Admin only."""
    from sqlalchemy import func

    from app.domains.privacy.models import AccountDeletionJob
    from app.shared.database.base import utcnow
    
    result = await session.execute(
        select(WorkerStatus).order_by(WorkerStatus.last_heartbeat_at.desc())
    )
    workers = result.scalars().all()
    by_name = {w.worker_name: w for w in workers}

    now = utcnow()
    
    pending = await session.execute(
        select(func.count(AccountDeletionJob.account_id)).where(
            AccountDeletionJob.state.notin_(['complete', 'failed_terminal'])
        )
    )
    
    active_leases = await session.execute(
        select(func.count(AccountDeletionJob.account_id)).where(
            AccountDeletionJob.lease_expires_at > now
        )
    )
    
    retryable = await session.execute(
        select(func.count(AccountDeletionJob.account_id)).where(
            AccountDeletionJob.state == 'failed_retryable'
        )
    )
    
    terminal = await session.execute(
        select(func.count(AccountDeletionJob.account_id)).where(
            AccountDeletionJob.state == 'failed_terminal'
        )
    )
    
    oldest = await session.execute(
        select(AccountDeletionJob.requested_at)
        .where(AccountDeletionJob.state.notin_(['complete', 'failed_terminal']))
        .order_by(AccountDeletionJob.requested_at.asc())
        .limit(1)
    )
    oldest_dt = oldest.scalar()
    oldest_age = (now - oldest_dt).total_seconds() if oldest_dt else 0

    worker_rows = [
        {
            "worker_name": w.worker_name,
            "started_at": w.started_at.isoformat() if w.started_at else None,
            "service_version": w.service_version,
            "last_heartbeat_at": w.last_heartbeat_at.isoformat() if w.last_heartbeat_at else None,
            "last_attempted_job_at": w.last_attempted_job_at.isoformat() if w.last_attempted_job_at else None,
            "last_successful_job_at": w.last_successful_job_at.isoformat() if w.last_successful_job_at else None,
            "last_error_code": w.last_error_code,
            "last_error_summary": w.last_error_summary,
            "last_error_at": w.last_error_at.isoformat() if w.last_error_at else None,
            **_freshness(w, now),
        }
        for w in workers
    ]

    return {
        "workers": worker_rows,
        # A scheduled batch cannot report that it did not run. This names the
        # scheduled workers that should have a recent run and says, for each,
        # whether one actually happened — so a scheduler that was never
        # installed, or quietly died, is visible here rather than only in the
        # absence of notifications customers never knew to expect.
        "scheduled_workers": [
            _scheduled_state(name, interval, by_name.get(name), now)
            for name, interval in SCHEDULED_WORKERS.items()
        ],
        "job_metrics": {
            "pending_jobs": pending.scalar() or 0,
            "active_leases": active_leases.scalar() or 0,
            "retryable_failures": retryable.scalar() or 0,
            "terminal_failures": terminal.scalar() or 0,
            "oldest_pending_job_age_seconds": oldest_age,
        }
    }
