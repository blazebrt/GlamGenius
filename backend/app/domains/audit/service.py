"""The audit trail.

An audit row says what happened, to what, by whom, and from roughly where.
"Roughly where" is the delicate part: an address is personal data, so the trail
keeps a hash of it rather than the address itself.

The hash has to be genuinely one-way, and for addresses that is harder than it
sounds. There are only 2^32 IPv4 addresses. Hashing one with SHA-256 and a
*constant* prefix — which is what this file used to do, calling the prefix a
salt — is not one-way at all: anyone holding the constant can compute the
entire table. Measured on one core of unoptimised Python, the whole space takes
under two core-hours; with a GPU it is seconds. A constant that lives in the
source is known to everyone who can read the source.

So the hash is keyed with a secret instead — HMAC-SHA256 under
``AUDIT_IP_HASH_KEY``. The same address still produces the same hash, so an
investigation can still link one actor's actions together, but reversing the
hash now needs the key rather than the repository.

Outside production the key is optional, and a clearly-labelled development
value is used so tests and local runs work. Production and staging refuse to
start without a real one — see ``validate_production_configuration()``.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AUDIT_IP_HASH_KEY
from app.domains.audit.models import AuditEvent
from app.shared.observability.request_id import get_request_id

#: Used only when no key is configured, which production forbids. Named so that
#: a hash computed with it is recognisable for what it is.
DEVELOPMENT_ONLY_KEY = "glamgenius-audit-development-only-not-a-secret"


def _key() -> bytes:
    return (AUDIT_IP_HASH_KEY or DEVELOPMENT_ONLY_KEY).encode("utf-8")


def hash_ip(ip: str | None) -> str | None:
    """Keyed one-way hash of a source address.

    Enough to link several actions to one actor during an investigation, and
    not reversible into a location by anybody who does not hold the key.
    """
    if not ip:
        return None
    return hmac.new(_key(), ip.encode("utf-8"), hashlib.sha256).hexdigest()


async def record(
    session: AsyncSession,
    *,
    action: str,
    account_id: uuid.UUID | None = None,
    actor_type: str = "user",
    subject_type: str | None = None,
    subject_id: str | None = None,
    context: dict[str, Any] | None = None,
    client_ip: str | None = None,
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
