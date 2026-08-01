"""Declarative base and the column conventions every V2 table follows."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    V1 uses naive ``datetime.utcnow()``. V2 is aware from the start; mixing the
    two silently produces wrong comparisons, so V2 code must use this.
    """
    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    """Application-generated UUID primary keys.

    Matches how V1 already generates ids in Mongo, so identifiers are the same
    kind of thing on both sides and can be handed between them.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
