"""Writing audit events."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit.models import AuditEvent
from app.shared.observability.request_id import get_request_id


def hash_ip(ip: Optional[str]) -> Optional[str]:
    """One-way hash of a source address.

    Enough to link several actions to one actor during an investigation, but not
    reversible into a location. Salted with a fixed label so these hashes cannot
    be compared against a rainbow table of bare IPs.
    """
    if not ip:
        return None
    return hashlib.sha256(f"glamgenius-audit:{ip}".encode()).hexdigest()


async def record(
    session: AsyncSession,
    *,
    action: str,
    account_id: Optional[uuid.UUID] = None,
    actor_type: str = "user",
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
    client_ip: Optional[str] = None,
) -> AuditEvent:
    """Add an audit event to the caller's transaction.

    Does not commit — an audit record must land with the change it describes,
    or not at all.
    """
    event = AuditEvent(
        account_id=account_id,
        actor_type=actor_type,
        action=action,
        subject_type=subject_type,
        subject_id=str(subject_id) if subject_id is not None else None,
        context=context or {},
        ip_hash=hash_ip(client_ip),
        request_id=get_request_id(),
    )
    session.add(event)
    await session.flush()
    return event
