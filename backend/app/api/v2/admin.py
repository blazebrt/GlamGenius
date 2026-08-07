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

    return {
        "workers": [
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
            }
            for w in workers
        ],
        "job_metrics": {
            "pending_jobs": pending.scalar() or 0,
            "active_leases": active_leases.scalar() or 0,
            "retryable_failures": retryable.scalar() or 0,
            "terminal_failures": terminal.scalar() or 0,
            "oldest_pending_job_age_seconds": oldest_age,
        }
    }
