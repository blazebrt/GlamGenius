from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence import authoring
from app.domains.evidence.enums import EvidenceDomain, EvidenceTier, ReviewStatus
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


# ---------------------------------------------------------------------------
# Knowledge authoring — the admin tool
# ---------------------------------------------------------------------------
# Every route is admin-only and writes through app.domains.evidence.authoring,
# which owns the workflow rules. Nothing here decides whether an entry may be
# approved; it asks the service, so a script and this API cannot disagree.


class EntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=200)
    claim: str = Field(min_length=1, max_length=4000)
    value: str | None = Field(default=None, max_length=256)
    unit: str | None = Field(default=None, max_length=64)
    source_name: str = Field(min_length=1, max_length=512)
    source_url: str | None = Field(default=None, max_length=2000)
    evidence_tier: str = Field(min_length=1, max_length=32)
    notes: str | None = Field(default=None, max_length=8000)
    domain: str = Field(default=EvidenceDomain.NUTRITION.value, max_length=32)

    def to_input(self) -> authoring.EntryInput:
        return authoring.EntryInput(
            subject_type=self.subject_type, subject_key=self.subject, claim=self.claim,
            value=self.value, unit=self.unit, source_name=self.source_name,
            source_url=self.source_url or "", evidence_tier=self.evidence_tier,
            notes=self.notes, domain=self.domain,
        )


class PublicationVerificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_opened: bool
    founder_verified_fact: bool
    claude_review_completed: bool
    codex_review_completed: bool
    independent_reviews_agree: bool
    adversarial_review_passed: bool
    unresolved_doubt: bool = False

    def to_input(self) -> authoring.VerificationInput:
        return authoring.VerificationInput(**self.model_dump())


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


def _author(current: CurrentAccount) -> str:
    return current.account_id_str


@router.get("/knowledge/vocabulary")
async def knowledge_vocabulary(
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """What the form may offer. Served from the enums so the two cannot drift."""
    return {
        "evidence_tiers": [t.value for t in EvidenceTier],
        "statuses": [
            ReviewStatus.DRAFT.value, ReviewStatus.APPROVED.value,
            ReviewStatus.PUBLISHED.value, ReviewStatus.REJECTED.value,
            ReviewStatus.SUPERSEDED.value,
        ],
        "domains": [d.value for d in EvidenceDomain],
        "subject_types": await authoring.subject_types(session),
        "csv_columns": list(authoring.CSV_COLUMNS),
    }


@router.get("/knowledge/entries")
async def list_knowledge_entries(
    subject_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.list_entries(
        session, subject_type=subject_type, status=status, limit=limit, offset=offset,
    )


@router.post("/knowledge/entries", status_code=status.HTTP_201_CREATED)
async def create_knowledge_entry(
    body: EntryBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    entry = await authoring.create_draft(session, body.to_input(), author=_author(current))
    await session.commit()
    return entry


@router.get("/knowledge/entries/{entry_id}")
async def read_knowledge_entry(
    entry_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.get_entry(session, entry_id)


@router.get("/knowledge/entries/{entry_id}/versions")
async def read_knowledge_entry_versions(
    entry_id: uuid.UUID,
    _: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return await authoring.versions_of(session, entry_id)


@router.put("/knowledge/entries/{entry_id}")
async def edit_knowledge_entry(
    entry_id: uuid.UUID,
    body: EntryBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Edit. A published or approved entry gains a new version; nothing is overwritten."""
    entry = await authoring.edit(session, entry_id, body.to_input(), author=_author(current))
    await session.commit()
    return entry


@router.post("/knowledge/entries/{entry_id}/approve")
async def approve_knowledge_entry(
    entry_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    entry = await authoring.approve(session, entry_id, reviewer=_author(current))
    await session.commit()
    return entry


@router.post("/knowledge/entries/{entry_id}/publish")
async def publish_knowledge_entry(
    entry_id: uuid.UUID,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    entry = await authoring.publish(session, entry_id, publisher=_author(current))
    await session.commit()
    return entry


@router.post("/knowledge/entries/{entry_id}/publication-verification")
async def record_knowledge_publication_verification(
    entry_id: uuid.UUID,
    body: PublicationVerificationBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    entry = await authoring.record_publication_verification(
        session, entry_id, verification=body.to_input(), actor=_author(current),
    )
    await session.commit()
    return entry


@router.post("/knowledge/entries/{entry_id}/reject")
async def reject_knowledge_entry(
    entry_id: uuid.UUID,
    body: RejectBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    entry = await authoring.reject(
        session, entry_id, reviewer=_author(current), reason=body.reason,
    )
    await session.commit()
    return entry


class ImportTextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv: str = Field(min_length=1, max_length=1_000_000)


@router.post("/knowledge/import-text", status_code=status.HTTP_201_CREATED)
async def import_knowledge_csv_text(
    body: ImportTextBody,
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Same importer, for CSV pasted into the tool rather than uploaded.

    A phone has no comfortable file picker, and the rule is identical: every
    row lands as a draft.
    """
    outcome = await authoring.import_csv(session, body.csv, author=_author(current))
    await session.commit()
    return outcome.as_dict()


@router.post("/knowledge/import", status_code=status.HTTP_201_CREATED)
async def import_knowledge_csv(
    file: UploadFile = File(...),
    current: CurrentAccount = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Bulk import. Every row lands as a draft — this route cannot publish."""
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    outcome = await authoring.import_csv(session, raw, author=_author(current))
    await session.commit()
    return outcome.as_dict()
