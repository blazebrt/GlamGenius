"""Who may call the B2B API: per-request authentication and the admin lifecycle.

Authentication
--------------
:func:`authenticate` takes the presented bearer value and answers with a
:class:`B2BCaller` or ``None``. ``None`` covers every way of being refused —
absent, malformed, unknown prefix, wrong secret, expired, revoked, suspended
client — and the route turns all of them into one identical 401. A caller
learns nothing about *why*, so a well-formed guess cannot be used to discover
which prefixes exist or which clients are suspended.

A presented value that is not exactly the credential shape (a consumer
Supabase JWT, for instance) is refused before any database read: B2B never
verifies a consumer token, and the consumer routes never accept a B2B key.

Expiry is judged by the database clock in the same statement that reads the
key, not by this process's clock.

Lifecycle (GlamGenius administrators only)
------------------------------------------
Create a client, issue a key (the raw credential is returned once, here, and
never again), revoke a key, suspend or reactivate a client, read usage. Keys
are revoked rather than deleted and clients are suspended rather than deleted,
so the operational history survives. Every change is written to the audit
trail with the client key and the key prefix — never the credential, never its
hash.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit import service as audit
from app.domains.b2b import credentials
from app.domains.b2b.models import (
    CLIENT_STATUS_ACTIVE,
    CLIENT_STATUS_SUSPENDED,
    B2BApiClient,
    B2BApiKey,
    B2BApiUsageDaily,
)
from app.domains.b2b.quota import utc_today

logger = logging.getLogger(__name__)

AUDIT_CLIENT_CREATED = "b2b.client.created"
AUDIT_CLIENT_SUSPENDED = "b2b.client.suspended"
AUDIT_CLIENT_ACTIVATED = "b2b.client.activated"
AUDIT_KEY_ISSUED = "b2b.key.issued"
AUDIT_KEY_REVOKED = "b2b.key.revoked"

KEY_STATE_ACTIVE = "active"
KEY_STATE_EXPIRED = "expired"
KEY_STATE_REVOKED = "revoked"

#: How many times issuance redraws a prefix that collided with an existing one.
#: 48 random bits make a single collision astronomically unlikely; this bound
#: only exists so the loop is provably finite.
_ISSUE_ATTEMPTS = 3


@dataclass(frozen=True)
class B2BCaller:
    """An authenticated client, for access control and accounting only.

    It is never handed to the Product Truth projection: nothing about who is
    calling, how much they may call, or how much they have called can reach
    the answer.
    """

    client_id: uuid.UUID
    client_key: str
    key_prefix: str
    requests_per_minute: int
    requests_per_day: int


class B2BLifecycleError(Exception):
    """A refused administrative change. Carries a stable code only."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
async def authenticate(session: AsyncSession, presented: str | None) -> B2BCaller | None:
    """The caller this credential authenticates, or ``None`` for every refusal."""
    prefix = credentials.prefix_of(presented)
    if prefix is None:
        return None
    expired = and_(B2BApiKey.expires_at.is_not(None), B2BApiKey.expires_at <= func.now())
    row = (await session.execute(
        select(B2BApiKey, B2BApiClient, expired.label("expired"))
        .join(B2BApiClient, B2BApiClient.id == B2BApiKey.client_id)
        .where(B2BApiKey.key_prefix == prefix)
    )).one_or_none()
    stored_hash = row[0].key_hash if row is not None else None
    # One constant-time comparison whatever was found, before any state check.
    if not credentials.matches(str(presented), stored_hash) or row is None:
        return None
    key, client, is_expired = row
    if key.revoked_at is not None or is_expired or client.status != CLIENT_STATUS_ACTIVE:
        return None
    return B2BCaller(
        client_id=client.id,
        client_key=client.client_key,
        key_prefix=key.key_prefix,
        requests_per_minute=client.requests_per_minute,
        requests_per_day=client.requests_per_day,
    )


# ---------------------------------------------------------------------------
# Serialisation (operator views; never a secret, never a hash)
# ---------------------------------------------------------------------------
def key_state(key: B2BApiKey, now: datetime) -> str:
    if key.revoked_at is not None:
        return KEY_STATE_REVOKED
    if key.expires_at is not None and key.expires_at <= now:
        return KEY_STATE_EXPIRED
    return KEY_STATE_ACTIVE


def serialize_key(key: B2BApiKey, now: datetime) -> dict[str, Any]:
    return {
        "id": str(key.id),
        "client_id": str(key.client_id),
        "key_prefix": key.key_prefix,
        "created_at": key.created_at.isoformat(),
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
        "state": key_state(key, now),
    }


def serialize_client(
    client: B2BApiClient, keys: list[B2BApiKey] | None, now: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(client.id),
        "client_key": client.client_key,
        "display_name": client.display_name,
        "status": client.status,
        "requests_per_minute": client.requests_per_minute,
        "requests_per_day": client.requests_per_day,
        "created_at": client.created_at.isoformat(),
        "updated_at": client.updated_at.isoformat(),
    }
    if keys is not None:
        payload["keys"] = [serialize_key(key, now) for key in keys]
    return payload


async def database_now(session: AsyncSession) -> datetime:
    return (await session.execute(select(func.now()))).scalar_one()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def _audit(
    session: AsyncSession,
    *,
    action: str,
    actor_account_id: uuid.UUID | None,
    subject_type: str,
    subject_id: uuid.UUID,
    context: dict[str, Any],
    client_ip: str | None,
) -> None:
    await audit.record(
        session,
        action=action,
        account_id=actor_account_id,
        actor_type="admin",
        subject_type=subject_type,
        subject_id=str(subject_id),
        context=context,
        client_ip=client_ip,
    )


async def create_client(
    session: AsyncSession,
    *,
    client_key: str,
    display_name: str,
    requests_per_minute: int,
    requests_per_day: int,
    actor_account_id: uuid.UUID | None,
    client_ip: str | None,
) -> B2BApiClient:
    client = B2BApiClient(
        client_key=client_key,
        display_name=display_name.strip(),
        status=CLIENT_STATUS_ACTIVE,
        requests_per_minute=requests_per_minute,
        requests_per_day=requests_per_day,
    )
    try:
        async with session.begin_nested():
            session.add(client)
            await session.flush()
    except IntegrityError as exc:
        raise B2BLifecycleError("client_key_taken") from exc
    await session.refresh(client)
    await _audit(
        session, action=AUDIT_CLIENT_CREATED, actor_account_id=actor_account_id,
        subject_type="b2b_api_client", subject_id=client.id,
        context={
            "client_key": client.client_key,
            "requests_per_minute": client.requests_per_minute,
            "requests_per_day": client.requests_per_day,
        },
        client_ip=client_ip,
    )
    logger.info("b2b_client_created client_key=%s", client.client_key)
    return client


async def list_clients(session: AsyncSession) -> list[tuple[B2BApiClient, list[B2BApiKey]]]:
    clients = (await session.execute(
        select(B2BApiClient).order_by(B2BApiClient.created_at, B2BApiClient.client_key)
    )).scalars().all()
    keys = (await session.execute(
        select(B2BApiKey).order_by(B2BApiKey.created_at, B2BApiKey.key_prefix)
    )).scalars().all()
    by_client: dict[uuid.UUID, list[B2BApiKey]] = {}
    for key in keys:
        by_client.setdefault(key.client_id, []).append(key)
    return [(client, by_client.get(client.id, [])) for client in clients]


async def get_client(session: AsyncSession, client_id: uuid.UUID) -> B2BApiClient:
    client = await session.get(B2BApiClient, client_id)
    if client is None:
        raise B2BLifecycleError("client_not_found")
    return client


async def issue_key(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    expires_at: datetime | None,
    actor_account_id: uuid.UUID | None,
    client_ip: str | None,
) -> tuple[B2BApiKey, credentials.IssuedCredential]:
    """A new credential for ``client_id``. The raw value exists only in the return."""
    client = await get_client(session, client_id)
    if expires_at is not None and expires_at <= await database_now(session):
        raise B2BLifecycleError("expiry_not_in_future")
    for _attempt in range(_ISSUE_ATTEMPTS):
        issued = credentials.generate()
        key = B2BApiKey(
            client_id=client.id, key_prefix=issued.prefix, key_hash=issued.key_hash, expires_at=expires_at,
        )
        try:
            async with session.begin_nested():
                session.add(key)
                await session.flush()
        except IntegrityError:
            continue
        await session.refresh(key)
        await _audit(
            session, action=AUDIT_KEY_ISSUED, actor_account_id=actor_account_id,
            subject_type="b2b_api_key", subject_id=key.id,
            context={
                "client_key": client.client_key,
                "key_prefix": key.key_prefix,
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            },
            client_ip=client_ip,
        )
        logger.info("b2b_key_issued client_key=%s key_prefix=%s", client.client_key, key.key_prefix)
        return key, issued
    raise B2BLifecycleError("key_generation_failed")


async def revoke_key(
    session: AsyncSession,
    *,
    key_id: uuid.UUID,
    actor_account_id: uuid.UUID | None,
    client_ip: str | None,
) -> B2BApiKey:
    """Revoke at once. Idempotent: a revoked key keeps its first revocation time."""
    key = (await session.execute(
        select(B2BApiKey).where(B2BApiKey.id == key_id).with_for_update()
    )).scalar_one_or_none()
    if key is None:
        raise B2BLifecycleError("key_not_found")
    if key.revoked_at is None:
        key.revoked_at = await database_now(session)
        await session.flush()
        client = await session.get(B2BApiClient, key.client_id)
        await _audit(
            session, action=AUDIT_KEY_REVOKED, actor_account_id=actor_account_id,
            subject_type="b2b_api_key", subject_id=key.id,
            context={"client_key": client.client_key if client else None, "key_prefix": key.key_prefix},
            client_ip=client_ip,
        )
        logger.info("b2b_key_revoked key_prefix=%s", key.key_prefix)
    await session.refresh(key)
    return key


async def set_client_status(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    status: str,
    actor_account_id: uuid.UUID | None,
    client_ip: str | None,
) -> B2BApiClient:
    """Suspend or reactivate a client. Changes access only; never any answer."""
    if status not in (CLIENT_STATUS_ACTIVE, CLIENT_STATUS_SUSPENDED):
        raise B2BLifecycleError("status_unknown")
    client = (await session.execute(
        select(B2BApiClient).where(B2BApiClient.id == client_id).with_for_update()
    )).scalar_one_or_none()
    if client is None:
        raise B2BLifecycleError("client_not_found")
    if client.status != status:
        client.status = status
        await session.flush()
        await _audit(
            session,
            action=AUDIT_CLIENT_SUSPENDED if status == CLIENT_STATUS_SUSPENDED else AUDIT_CLIENT_ACTIVATED,
            actor_account_id=actor_account_id,
            subject_type="b2b_api_client", subject_id=client.id,
            context={"client_key": client.client_key, "status": status},
            client_ip=client_ip,
        )
        logger.info("b2b_client_status client_key=%s status=%s", client.client_key, status)
    await session.refresh(client)
    return client


async def client_keys(session: AsyncSession, client_id: uuid.UUID) -> list[B2BApiKey]:
    return list((await session.execute(
        select(B2BApiKey).where(B2BApiKey.client_id == client_id)
        .order_by(B2BApiKey.created_at, B2BApiKey.key_prefix)
    )).scalars().all())


def _usage_row(row: B2BApiUsageDaily | None, usage_date: date) -> dict[str, Any]:
    return {
        "usage_date": usage_date.isoformat(),
        "requests": row.request_count if row else 0,
        "successful": row.successful_count if row else 0,
        "not_enough_information": row.not_enough_information_count if row else 0,
        "rate_limited": row.rate_limited_count if row else 0,
    }


async def usage(session: AsyncSession, *, client_id: uuid.UUID, days: int) -> dict[str, Any]:
    """Operational counts for one client: today, and a window of whole UTC days.

    Requests, answers and refusals. Nothing here is a sale, a conversion or an
    amount of money, and nothing names a barcode or a product.
    """
    client = await get_client(session, client_id)
    today = (await session.execute(select(utc_today()))).scalar_one()
    start = today - timedelta(days=days - 1)
    rows = (await session.execute(
        select(B2BApiUsageDaily)
        .where(B2BApiUsageDaily.client_id == client_id, B2BApiUsageDaily.usage_date >= start)
        .order_by(B2BApiUsageDaily.usage_date)
    )).scalars().all()
    by_date = {row.usage_date: row for row in rows}
    daily = [_usage_row(by_date.get(start + timedelta(days=offset)), start + timedelta(days=offset))
             for offset in range(days)]
    today_row = _usage_row(by_date.get(today), today)
    window = {
        "days": days,
        "from": start.isoformat(),
        "to": today.isoformat(),
        **{name: sum(row[name] for row in daily)
           for name in ("requests", "successful", "not_enough_information", "rate_limited")},
    }
    return {
        "client_id": str(client.id),
        "client_key": client.client_key,
        "status": client.status,
        "quota": {
            "requests_per_minute": client.requests_per_minute,
            "requests_per_day": client.requests_per_day,
        },
        "today": {**today_row, "remaining": max(0, client.requests_per_day - today_row["requests"])},
        "window": window,
        "daily": daily,
    }


__all__ = [
    "AUDIT_CLIENT_ACTIVATED",
    "AUDIT_CLIENT_CREATED",
    "AUDIT_CLIENT_SUSPENDED",
    "AUDIT_KEY_ISSUED",
    "AUDIT_KEY_REVOKED",
    "B2BCaller",
    "B2BLifecycleError",
    "authenticate",
    "client_keys",
    "create_client",
    "database_now",
    "get_client",
    "issue_key",
    "key_state",
    "list_clients",
    "revoke_key",
    "serialize_client",
    "serialize_key",
    "set_client_status",
    "usage",
]
