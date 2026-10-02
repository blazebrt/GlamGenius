"""Step 17 — administering B2B API access. GlamGenius administrators only.

Controlled pilot issuance, nothing self-service: there is no public sign-up,
no developer portal, no organisation membership, and nothing here sets a
price. Every route uses the app's existing hidden-admin authority
(:func:`app.shared.security.supabase_auth.get_current_admin`): a signed-in
non-admin gets the same 404 any other admin surface gives them, and a caller
with no token gets 401.

The raw API key appears in exactly one place: the body of a successful
``POST /admin/b2b/clients/{client_id}/keys``. Every other response names a key
by id and public prefix only — never the secret, never its hash.

These routes manage access. None of them can reach a Product Truth answer:
suspending a client or changing a key changes who may ask, never what anybody
is told.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.b2b import access
from app.domains.b2b.models import (
    CLIENT_KEY_PATTERN,
    CLIENT_STATUS_ACTIVE,
    CLIENT_STATUS_SUSPENDED,
    DISPLAY_NAME_MAX,
    MAX_REQUESTS_PER_DAY,
    MAX_REQUESTS_PER_MINUTE,
    MIN_REQUESTS_PER_DAY,
    MIN_REQUESTS_PER_MINUTE,
)
from app.domains.identity.models import Account
from app.shared.database.sql import get_session
from app.shared.security.network import client_ip
from app.shared.security.supabase_auth import SupabaseUser, get_current_admin

router = APIRouter(prefix="/admin/b2b")

MAX_USAGE_DAYS = 90

_NOT_FOUND = {
    "client_not_found": "No such B2B client.",
    "key_not_found": "No such B2B API key.",
}


class ClientCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=3, max_length=48, pattern=CLIENT_KEY_PATTERN)
    display_name: str = Field(min_length=1, max_length=DISPLAY_NAME_MAX)
    requests_per_minute: int = Field(ge=MIN_REQUESTS_PER_MINUTE, le=MAX_REQUESTS_PER_MINUTE)
    requests_per_day: int = Field(ge=MIN_REQUESTS_PER_DAY, le=MAX_REQUESTS_PER_DAY)

    @field_validator("display_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display_name must not be blank")
        return value


class KeyIssueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must carry a timezone")
        return value


async def _actor(session: AsyncSession, admin: SupabaseUser) -> uuid.UUID | None:
    """The admin's account id for the audit trail, when the admin has an account row."""
    return admin.id if await session.get(Account, admin.id) is not None else None


def _refused(error: access.B2BLifecycleError) -> HTTPException:
    if error.code in _NOT_FOUND:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": error.code, "message": _NOT_FOUND[error.code]},
        )
    if error.code == "client_key_taken":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": "That client key is already in use."},
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": error.code, "message": "The request cannot be applied."},
    )


@router.post("/clients", status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreateBody,
    request: Request,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        client = await access.create_client(
            session,
            client_key=body.client_key,
            display_name=body.display_name,
            requests_per_minute=body.requests_per_minute,
            requests_per_day=body.requests_per_day,
            actor_account_id=await _actor(session, admin),
            client_ip=client_ip(request),
        )
    except access.B2BLifecycleError as error:
        raise _refused(error) from error
    await session.commit()
    return access.serialize_client(client, [], await access.database_now(session))


@router.get("/clients")
async def list_clients(
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    now = await access.database_now(session)
    return {
        "clients": [
            access.serialize_client(client, keys, now)
            for client, keys in await access.list_clients(session)
        ],
    }


@router.post("/clients/{client_id}/keys", status_code=status.HTTP_201_CREATED)
async def issue_key(
    client_id: uuid.UUID,
    request: Request,
    body: KeyIssueBody | None = None,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    """Issue a key. The response is the only time the raw key is ever shown."""
    try:
        key, issued = await access.issue_key(
            session,
            client_id=client_id,
            expires_at=body.expires_at if body is not None else None,
            actor_account_id=await _actor(session, admin),
            client_ip=client_ip(request),
        )
    except access.B2BLifecycleError as error:
        raise _refused(error) from error
    await session.commit()
    return {
        "key": access.serialize_key(key, await access.database_now(session)),
        "api_key": issued.raw,
        "notice": "Store this key now. GlamGenius keeps only a hash and cannot show it again.",
    }


@router.post("/keys/{key_id}/revoke")
async def revoke_key(
    key_id: uuid.UUID,
    request: Request,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        key = await access.revoke_key(
            session, key_id=key_id, actor_account_id=await _actor(session, admin), client_ip=client_ip(request),
        )
    except access.B2BLifecycleError as error:
        raise _refused(error) from error
    await session.commit()
    return access.serialize_key(key, await access.database_now(session))


async def _set_status(
    session: AsyncSession, admin: SupabaseUser, request: Request, client_id: uuid.UUID, new_status: str,
):
    try:
        client = await access.set_client_status(
            session, client_id=client_id, status=new_status,
            actor_account_id=await _actor(session, admin), client_ip=client_ip(request),
        )
    except access.B2BLifecycleError as error:
        raise _refused(error) from error
    await session.commit()
    now = await access.database_now(session)
    return access.serialize_client(client, await access.client_keys(session, client.id), now)


@router.post("/clients/{client_id}/suspend")
async def suspend_client(
    client_id: uuid.UUID,
    request: Request,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _set_status(session, admin, request, client_id, CLIENT_STATUS_SUSPENDED)


@router.post("/clients/{client_id}/activate")
async def activate_client(
    client_id: uuid.UUID,
    request: Request,
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _set_status(session, admin, request, client_id, CLIENT_STATUS_ACTIVE)


@router.get("/clients/{client_id}/usage")
async def client_usage(
    client_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=MAX_USAGE_DAYS),
    admin: SupabaseUser = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
):
    """Operational counts: requests, answers and refusals. Not sales, not money."""
    try:
        return await access.usage(session, client_id=client_id, days=days)
    except access.B2BLifecycleError as error:
        raise _refused(error) from error


__all__ = ["router"]
