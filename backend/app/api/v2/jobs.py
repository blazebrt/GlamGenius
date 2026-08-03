"""GET /api/v2/jobs/{id} — the status of a long-running piece of work.

Deviation from the brief, documented: there is no separate job queue in Phase 1,
because there is nothing yet that runs asynchronously. Inventing an empty queue
to satisfy a route shape would be scaffolding with no load on it.

The one thing that *is* slow, costly and worth polling is an AI run. So a "job"
here is an ``ai_runs`` row, and this route reports its status. When real
background work arrives, this route keeps its shape and gains a second source.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ai_gateway.models import AI_STATUS_SUCCEEDED, AIRun
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import NotFoundError
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(AIRun)
        .where(AIRun.id == job_id)
        # Ownership check. Another account's run is "not found", never "denied".
        .where(AIRun.account_id == current.account_id)
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise NotFoundError("We could not find that job.")

    return {
        "id": str(run.id),
        "kind": "ai_run",
        "feature": run.feature,
        "status": run.status,
        "succeeded": run.status == AI_STATUS_SUCCEEDED,
        "failure_type": run.failure_type,
        "latency_ms": run.latency_ms,
        "allowance_consumed": run.allowance_consumed,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }
