from __future__ import annotations

import datetime
from typing import Optional

from app.shared.database.base import Base, TimestampMixin, UUIDPrimaryKey
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column


class WorkerStatus(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "system_worker_status"

    worker_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    last_heartbeat_at: Mapped[datetime.datetime] = mapped_column(nullable=False)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    last_successful_job_at: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    last_attempted_job_at: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error_summary: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_error_at: Mapped[Optional[datetime.datetime]] = mapped_column(nullable=True)
    service_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_worker_status_name", "worker_name"),
    )
