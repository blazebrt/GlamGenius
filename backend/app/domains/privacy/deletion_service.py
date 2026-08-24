"""Account-deletion state machine — service + worker.

The **service** is what the API calls to (idempotently) request a deletion.
The **worker** is what actually walks the stages. The two are separated so
the API can return ``202 Accepted`` immediately and let the destructive
work happen without holding a request open.

Guarantees the worker enforces
------------------------------
1. Storage is emptied *before* the account row is deleted. The listing has
   to come back empty; a "delete may have succeeded" is not enough.
2. The Supabase Auth identity is deleted **last**. Removing it before
   storage would leave orphan personal bytes with no owning identity.
3. Every stage is idempotent. A crash between stages resumes at the same
   place; a duplicate worker cannot process the same job twice thanks to
   the lease.
4. Retry is bounded — after ``_MAX_ATTEMPTS`` the job moves to
   ``failed_terminal`` and is left for a human. No auto-escalation.
5. Nothing personal is written to the job. The tombstone that remains after
   completion contains only the account UUID and timestamps.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models import (
    ACCOUNT_STATUS_DELETED,
    ACCOUNT_STATUS_DELETION_REQUESTED,
    Account,
)
from app.domains.media import service as media_service
from app.domains.media.storage.base import (
    StorageError,
    StorageMisconfigured,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
)
from app.domains.privacy.models import (
    DESTRUCTIVE_STATES,
    STATE_AUTH_DELETING,
    STATE_COMPLETE,
    STATE_DATABASE_COMPLETE,
    STATE_DATABASE_DELETING,
    STATE_FAILED_RETRYABLE,
    STATE_FAILED_TERMINAL,
    STATE_INTEGRATIONS_COMPLETE,
    STATE_INTEGRATIONS_DELETING,
    STATE_REQUESTED,
    STATE_STORAGE_COMPLETE,
    STATE_STORAGE_DELETING,
    STATE_STORAGE_LISTING,
    AccountDeletionJob,
)
from app.shared.database.base import utcnow
from app.shared.supabase_client import get_supabase_admin

logger = logging.getLogger(__name__)


# The lease is short so a crashed worker cannot hold a job forever; the
# retry interval is long enough that a transient outage will recover, but
# short enough that a stuck queue does not sit idle.
_LEASE_SECONDS = 60
_RETRY_MIN_SECONDS = 30
_RETRY_MAX_SECONDS = 600
_MAX_ATTEMPTS = 8

_HOST_ID = f"{socket.gethostname()}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Service (API-facing)
# ---------------------------------------------------------------------------


async def request_deletion(session: AsyncSession, account_id: uuid.UUID) -> AccountDeletionJob:
    """Idempotently create the deletion job for ``account_id``.

    Marks the account ``deletion_requested`` and, if no active job exists,
    creates one in the ``requested`` state.
    """
    existing = (
        await session.execute(
            select(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    account = (await session.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is not None and account.status != ACCOUNT_STATUS_DELETED:
        account.status = ACCOUNT_STATUS_DELETION_REQUESTED
        account.deletion_requested_at = utcnow()

    job = AccountDeletionJob(
        account_id=account_id,
        state=STATE_REQUESTED,
        requested_at=utcnow(),
    )
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, account_id: uuid.UUID) -> AccountDeletionJob | None:
    return (
        await session.execute(
            select(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id)
        )
    ).scalar_one_or_none()


def status_payload(job: AccountDeletionJob) -> dict:
    """User-safe status view. No stack traces, no provider secrets."""
    return {
        "account_id": str(job.account_id),
        "state": job.state,
        "attempts": job.attempt_count,
        "retryable": job.state == STATE_FAILED_RETRYABLE,
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "message": _user_message(job),
    }


def _user_message(job: AccountDeletionJob) -> str:
    if job.state == STATE_COMPLETE:
        return "Your account has been permanently deleted."
    if job.state == STATE_FAILED_TERMINAL:
        return (
            "We could not finish deleting your account automatically. "
            "Please contact support so a human can complete the removal."
        )
    if job.state == STATE_FAILED_RETRYABLE:
        return "Your deletion hit a temporary problem and will retry shortly."
    if job.state == STATE_REQUESTED:
        return "Your deletion has been queued and will begin shortly."
    return "Your deletion is in progress."


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def claim_next(session: AsyncSession) -> AccountDeletionJob | None:
    """Take the next job that is ready to run, with a lease.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so two workers can safely poll
    the same table without stepping on each other's toes.
    """
    now = utcnow()
    stmt = (
        select(AccountDeletionJob)
        .where(AccountDeletionJob.state.notin_([STATE_COMPLETE, STATE_FAILED_TERMINAL]))
        .where(
            (AccountDeletionJob.lease_expires_at.is_(None))
            | (AccountDeletionJob.lease_expires_at < now)
        )
        .where(
            (AccountDeletionJob.next_retry_at.is_(None))
            | (AccountDeletionJob.next_retry_at <= now)
        )
        .order_by(AccountDeletionJob.requested_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.lease_owner = _HOST_ID
    job.lease_expires_at = now + timedelta(seconds=_LEASE_SECONDS)
    job.attempt_count += 1
    if job.started_at is None:
        job.started_at = now
    # If this is a retry, resume at the stage that failed rather than
    # starting over — restarts of an already-partly-run job would otherwise
    # skip the completed stages.
    if job.state == STATE_FAILED_RETRYABLE and job.last_error_stage:
        job.state = job.last_error_stage
    await session.flush()
    return job


async def run_job(session: AsyncSession, job: AccountDeletionJob) -> tuple[str, str | None]:
    """Advance the job one stage. Returns ``(new_state, error_code)``."""
    try:
        if job.state == STATE_REQUESTED or job.state == STATE_STORAGE_LISTING:
            job.state = STATE_STORAGE_DELETING
            await session.flush()

        if job.state == STATE_STORAGE_DELETING:
            _removed, remaining = await media_service.purge_account_storage(job.account_id)
            if remaining:
                # Object listing after delete still shows keys → the storage
                # stage is NOT complete. Do not advance; retry.
                logger.warning(
                    "account_deletion_storage_incomplete account=%s remaining=%d",
                    job.account_id, len(remaining),
                )
                _schedule_retry(job, code="storage_incomplete", stage="storage_deleting")
                return job.state, "storage_incomplete"
            job.state = STATE_STORAGE_COMPLETE
            await session.flush()

        if job.state == STATE_STORAGE_COMPLETE:
            job.state = STATE_INTEGRATIONS_DELETING
            await session.flush()

        if job.state == STATE_INTEGRATIONS_DELETING:
            await _remove_external_integrations(session, job.account_id)
            job.state = STATE_INTEGRATIONS_COMPLETE
            await session.flush()

        if job.state == STATE_INTEGRATIONS_COMPLETE:
            job.state = STATE_DATABASE_DELETING
            await session.flush()

        if job.state == STATE_DATABASE_DELETING:
            await _delete_account_row(session, job.account_id)
            job.state = STATE_DATABASE_COMPLETE
            await session.flush()

        if job.state == STATE_DATABASE_COMPLETE:
            job.state = STATE_AUTH_DELETING
            await session.flush()

        if job.state == STATE_AUTH_DELETING:
            await _delete_supabase_identity(job.account_id)
            job.state = STATE_COMPLETE
            job.completed_at = utcnow()
            job.next_retry_at = None
            await session.flush()

        return job.state, None

    except (StorageTimeout, StorageUnavailable) as exc:
        logger.warning(
            "account_deletion_storage_transient account=%s reason=%s",
            job.account_id, type(exc).__name__,
        )
        _schedule_retry(job, code="storage_unavailable", stage=job.state)
        return job.state, "storage_unavailable"
    except StorageUnauthorized as exc:
        logger.error(
            "account_deletion_storage_unauthorized account=%s reason=%s",
            job.account_id, type(exc).__name__,
        )
        _schedule_retry(job, code="storage_unauthorized", stage=job.state)
        return job.state, "storage_unauthorized"
    except StorageMisconfigured as exc:
        logger.error(
            "account_deletion_storage_misconfigured account=%s reason=%s",
            job.account_id, type(exc).__name__,
        )
        # Not the user's fault; move to terminal so a human can fix config.
        job.state = STATE_FAILED_TERMINAL
        job.last_error_code = "storage_misconfigured"
        job.last_error_stage = job.state
        await session.flush()
        return job.state, "storage_misconfigured"
    except StorageError as exc:
        logger.error(
            "account_deletion_storage_error account=%s reason=%s",
            job.account_id, type(exc).__name__,
        )
        _schedule_retry(job, code="storage_error", stage=job.state)
        return job.state, "storage_error"
    except Exception as exc:  # noqa: BLE001 — external boundary
        logger.exception(
            "account_deletion_unexpected account=%s stage=%s type=%s",
            job.account_id, job.state, type(exc).__name__,
        )
        _schedule_retry(job, code="unexpected", stage=job.state)
        return job.state, "unexpected"


def _schedule_retry(job: AccountDeletionJob, *, code: str, stage: str) -> None:
    job.last_error_code = code
    job.last_error_stage = stage
    # Release the lease so the next worker can pick this job up when the
    # retry timer expires.
    job.lease_owner = None
    job.lease_expires_at = None
    if job.attempt_count >= _MAX_ATTEMPTS:
        job.state = STATE_FAILED_TERMINAL
        job.next_retry_at = None
        return
    # Exponential backoff, clamped to a sensible ceiling.
    backoff = min(
        _RETRY_MIN_SECONDS * (2 ** max(0, job.attempt_count - 1)),
        _RETRY_MAX_SECONDS,
    )
    job.state = STATE_FAILED_RETRYABLE
    job.next_retry_at = utcnow() + timedelta(seconds=backoff)


async def _remove_external_integrations(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Remove external-integration references owned by the account.

    Any cross-system revocation (calendar OAuth tokens, push registrations)
    happens here. The base implementation deletes the local records because
    those are what our own systems index against; production integrations
    add their revocation calls here.
    """
    from app.domains.planning.calendar_sync import disconnect_google_calendar
    from app.domains.planning.models import ExternalIntegration, NotificationPreference

    google = (await session.execute(select(ExternalIntegration).where(
        ExternalIntegration.account_id == account_id,
        ExternalIntegration.kind == "calendar",
        ExternalIntegration.provider == "google",
        ExternalIntegration.status != "revoked",
    ))).scalar_one_or_none()
    if google is not None:
        result = await disconnect_google_calendar(session, account_id)
        if result.get("status") != "revoked":
            raise RuntimeError("google_calendar_revocation_pending")
    await session.execute(delete(ExternalIntegration).where(ExternalIntegration.account_id == account_id))
    await session.execute(
        delete(NotificationPreference).where(NotificationPreference.account_id == account_id)
    )
    await session.flush()


async def _delete_account_row(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Delete the accounts row. ON DELETE CASCADE handles the child tables."""
    account = (await session.execute(
        select(Account).where(Account.id == account_id)
    )).scalar_one_or_none()
    if account is None:
        return
    account.status = ACCOUNT_STATUS_DELETED
    await session.execute(delete(Account).where(Account.id == account_id))
    await session.flush()


async def _delete_supabase_identity(account_id: uuid.UUID) -> None:
    """Ask Supabase Auth to delete the identity. Idempotent."""
    admin = get_supabase_admin()
    try:
        admin.auth.admin.delete_user(str(account_id))
    except Exception as exc:  # noqa: BLE001 — external boundary
        message = str(exc).lower()
        if "user not found" in message or "not found" in message:
            # Already gone; the worker is retrying an interrupted deletion.
            return
        raise


async def process_once(session: AsyncSession) -> bool:
    """Run the next available job through one advance. Returns True if a job was processed."""
    job = await claim_next(session)
    if job is None:
        return False
    await run_job(session, job)
    return True


async def drain_all(session: AsyncSession, *, max_iterations: int = 100) -> int:
    """Drain the queue until it is empty. Bounded to guard against loops."""
    processed = 0
    for _ in range(max_iterations):
        if not await process_once(session):
            break
        processed += 1
    return processed


# Exported so the tests and privacy API can share the destructive-state set.
__all__ = [
    "AccountDeletionJob",
    "DESTRUCTIVE_STATES",
    "claim_next",
    "drain_all",
    "get_job",
    "process_once",
    "request_deletion",
    "run_job",
    "status_payload",
]
