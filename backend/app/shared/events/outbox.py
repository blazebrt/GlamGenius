"""Writing domain events."""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.events.models import OutboxEvent


async def publish(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> OutboxEvent:
    """Queue an event on the caller's transaction.

    Deliberately does not commit — the caller's commit is what makes the event
    real. That is the whole point of an outbox.
    """
    event = OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        event_type=event_type,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event
