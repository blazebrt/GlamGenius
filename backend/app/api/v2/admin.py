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
    result = await session.execute(
        select(WorkerStatus).order_by(WorkerStatus.last_heartbeat_at.desc())
    )
    workers = result.scalars().all()
    return [
        {
            "worker_name": w.worker_name,
            "last_heartbeat_at": w.last_heartbeat_at.isoformat(),
            "last_error": w.last_error,
        }
        for w in workers
    ]
