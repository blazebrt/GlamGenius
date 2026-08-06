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
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("idx_worker_status_name", "worker_name"),
    )
