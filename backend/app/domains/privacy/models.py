"""Durable account-deletion state machine.

The old privacy delete was one synchronous transaction that ran storage,
database and Supabase Auth deletion in a single request. If storage failed
halfway through it either left personal bytes on disk with no matching row,
or it "succeeded" from the caller's point of view while the objects were
still there.

The state machine is the fix: a persistent job row that walks a defined
sequence of stages, is safe to resume after a crash, and never deletes the
Supabase Auth identity until a listing of the account's storage prefix
proves it is empty.

Nothing in this table stores personal payloads. It is deliberately safe to
keep after the account itself has been deleted — a tombstone that says
"account X was purged at time Y" and nothing more.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey

# Ordered state machine. Each stage is idempotent; a worker crash between
# two stages is safe because the next tick picks up at ``state``.
STATE_REQUESTED = "requested"
STATE_STORAGE_LISTING = "storage_listing"
STATE_STORAGE_DELETING = "storage_deleting"
STATE_STORAGE_COMPLETE = "storage_complete"
STATE_INTEGRATIONS_DELETING = "integrations_deleting"
STATE_INTEGRATIONS_COMPLETE = "integrations_complete"
STATE_DATABASE_DELETING = "database_deleting"
STATE_DATABASE_COMPLETE = "database_complete"
STATE_AUTH_DELETING = "auth_deleting"
STATE_COMPLETE = "complete"
STATE_FAILED_RETRYABLE = "failed_retryable"
STATE_FAILED_TERMINAL = "failed_terminal"

TERMINAL_STATES = {STATE_COMPLETE, STATE_FAILED_TERMINAL}
DESTRUCTIVE_STATES = {
    STATE_STORAGE_DELETING,
    STATE_INTEGRATIONS_DELETING,
    STATE_DATABASE_DELETING,
    STATE_AUTH_DELETING,
    STATE_STORAGE_COMPLETE,
    STATE_INTEGRATIONS_COMPLETE,
    STATE_DATABASE_COMPLETE,
    STATE_COMPLETE,
}


class AccountDeletionJob(UUIDPrimaryKey, TimestampMixin, Base):
    """Persistent account-deletion job.

    Deliberately *not* an account-owned table — a job for an account that has
    already been cascade-deleted must still exist so the worker can complete
    the tail-end stages (Supabase Auth deletion, final tombstone).
    """

    __tablename__ = "account_deletion_jobs"

    # The account this job is deleting. No FK: the accounts row is removed by
    # the database-deleting stage and the job must survive that.
    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True, unique=True
    )

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=STATE_REQUESTED, server_default=STATE_REQUESTED
    )

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Only the shape of the last failure — never a stack trace, provider
    # response or personal payload.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)

    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lease so two workers cannot process the same job. A short lease that
    # expires means a crashed worker cannot lock a job forever.
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def can_cancel(self) -> bool:
        """True while the job has not yet begun destructive work."""
        return self.state == STATE_REQUESTED

    __table_args__ = (
        Index("ix_account_deletion_jobs_state", "state", "next_retry_at"),
    )
