"""OAuth, synchronization and revocation orchestration for Google Calendar."""
from __future__ import annotations

import hashlib
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_INITIAL_HORIZON_DAYS,
    GOOGLE_CALENDAR_REDIRECT_URI,
    GOOGLE_CALENDAR_SCOPE,
    GOOGLE_CALENDAR_STATE_TTL_SECONDS,
    GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT,
)
from app.domains.planning.credentials import credential_store
from app.domains.planning.models import CalendarEvent, ExternalIntegration, ExternalOAuthState
from app.domains.planning.providers.base import ProviderUnavailable
from app.domains.planning.providers.google_calendar import GoogleCalendarProvider, GoogleSyncTokenExpired
from app.domains.planning.service import INTEGRATION_CALENDAR
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import NotFoundError, ValidationFailedError

GOOGLE_PROVIDER = "google"


def _hash_state(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def async_authorization_url(session: AsyncSession, account_id: uuid.UUID) -> tuple[str, datetime]:
    from app.config import GOOGLE_CALENDAR_ENABLED
    if not GOOGLE_CALENDAR_ENABLED:
        raise ValidationFailedError("Google Calendar is not enabled yet.", field="provider")
    raw = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(seconds=GOOGLE_CALENDAR_STATE_TTL_SECONDS)
    session.add(ExternalOAuthState(account_id=account_id, provider=GOOGLE_PROVIDER, state_hash=_hash_state(raw), expires_at=expires))
    await session.flush()
    params = {
        "client_id": GOOGLE_CALENDAR_CLIENT_ID,
        "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_CALENDAR_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": raw,
    }
    return f"{GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT}?{urllib.parse.urlencode(params)}", expires


async def consume_state(session: AsyncSession, raw_state: str) -> ExternalOAuthState:
    if not raw_state or len(raw_state) > 256:
        raise ValidationFailedError("That calendar connection has expired. Please try again.", field="state")
    row = (await session.execute(select(ExternalOAuthState).where(ExternalOAuthState.state_hash == _hash_state(raw_state)).with_for_update())).scalar_one_or_none()
    now = utcnow()
    if row is None or row.provider != GOOGLE_PROVIDER or row.consumed_at is not None or row.expires_at <= now:
        raise ValidationFailedError("That calendar connection has expired. Please try again.", field="state")
    row.consumed_at = now
    await session.flush()
    return row


async def _integration(session: AsyncSession, account_id: uuid.UUID, *, lock: bool = False) -> ExternalIntegration | None:
    stmt = select(ExternalIntegration).where(
        ExternalIntegration.account_id == account_id,
        ExternalIntegration.kind == INTEGRATION_CALENDAR,
        ExternalIntegration.provider == GOOGLE_PROVIDER,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


def _bounds(timezone_name: str) -> tuple[datetime, datetime]:
    from app.domains.planning import clock
    local_today = clock.local_today(timezone_name)
    start, _ = clock.day_bounds(local_today, timezone_name)
    end, _ = clock.day_bounds(local_today + timedelta(days=GOOGLE_CALENDAR_INITIAL_HORIZON_DAYS), timezone_name)
    return start, end


async def _apply_reading(session: AsyncSession, integration: ExternalIntegration, reading: Any) -> CalendarEvent:
    row = (await session.execute(select(CalendarEvent).where(
        CalendarEvent.integration_id == integration.id,
        CalendarEvent.external_id == reading.external_id,
    ))).scalar_one_or_none()
    if row is None:
        row = CalendarEvent(
            account_id=integration.account_id, integration_id=integration.id,
            external_id=reading.external_id,
            dedup_key=f"google:{reading.external_id}:{reading.starts_at.isoformat()}",
            title=reading.title, starts_at=reading.starts_at, ends_at=reading.ends_at,
            all_day=reading.all_day, location=reading.location,
            provider="google", source="integration", status=reading.status,
        )
        session.add(row)
        return row
    if reading.status == "revoked" and reading.raw.get("tombstone"):
        row.status = "revoked"
        return row
    overrides = row.user_overrides or {}
    if "title" not in overrides:
        row.title = reading.title
    if "starts_at" not in overrides:
        row.starts_at = reading.starts_at
    if "ends_at" not in overrides:
        row.ends_at = reading.ends_at
    if "all_day" not in overrides:
        row.all_day = reading.all_day
    if "location" not in overrides:
        row.location = reading.location
    # Provider cancellation/deletion is authoritative and cannot be hidden by
    # a stale local dismissal. For a live event, an explicit user status wins.
    if reading.status == "revoked":
        row.status = "revoked"
    elif "status" not in overrides:
        row.status = reading.status
    row.dedup_key = f"google:{reading.external_id}:{reading.starts_at.isoformat()}"
    return row


async def sync_google_calendar(
    session: AsyncSession, account_id: uuid.UUID, timezone_name: str, *,
    provider: GoogleCalendarProvider | None = None, _allow_reset: bool = True,
) -> dict[str, Any]:
    # Serialize all reads and writes for one integration. The unique external
    # identity constraint remains a final invariant, not the lock mechanism.
    integration = await _integration(session, account_id, lock=True)
    if integration is None or integration.status not in {"connected", "temporary_failure"} or not integration.credential_ref:
        raise NotFoundError("Google Calendar is not connected.")
    provider = provider or GoogleCalendarProvider(credential_store(session))
    initial_sync = integration.sync_cursor is None
    sync_since: datetime | None = None
    sync_until: datetime | None = None
    try:
        if integration.sync_cursor:
            readings, cursor, _initial = await provider.fetch_changes(
                credential_ref=integration.credential_ref, timezone_name=timezone_name, sync_cursor=integration.sync_cursor,
            )
        else:
            since, until = _bounds(timezone_name)
            sync_since, sync_until = since, until
            readings, cursor, _initial = await provider.fetch_changes(
                credential_ref=integration.credential_ref, timezone_name=timezone_name, sync_cursor=None, since=since, until=until,
            )
    except GoogleSyncTokenExpired:
        if not _allow_reset:
            integration.last_error = "sync_reset_failed"
            await session.flush()
            return {"connected": True, "synced": False, "reason": "sync_reset_failed"}
        integration.sync_cursor = None
        await session.flush()
        return await sync_google_calendar(session, account_id, timezone_name, provider=provider, _allow_reset=False)
    except ProviderUnavailable as exc:
        integration.last_error = exc.reason
        if exc.reason == "reconnect_required":
            integration.status = "reconnect_required"
        else:
            integration.status = "temporary_failure"
        await session.flush()
        return {"connected": integration.status in {"connected", "temporary_failure"}, "synced": False, "reason": exc.reason}

    seen = set()
    created = updated = revoked = 0
    for reading in readings:
        seen.add(reading.external_id)
        existing = (await session.execute(select(CalendarEvent).where(
            CalendarEvent.integration_id == integration.id, CalendarEvent.external_id == reading.external_id,
        ))).scalar_one_or_none()
        if existing is None and reading.status == "revoked" and reading.raw.get("tombstone"):
            continue
        await _apply_reading(session, integration, reading)
        if existing is None:
            created += 1
        elif reading.status == "revoked":
            revoked += 1
        else:
            updated += 1
    if initial_sync and sync_since is not None and sync_until is not None:
        # A bounded full sync owns the future horizon. Anything absent from the
        # completed page set is no longer a usable imported event.
        for row in (await session.execute(select(CalendarEvent).where(
            CalendarEvent.integration_id == integration.id,
            CalendarEvent.status == "active",
            CalendarEvent.starts_at >= sync_since,
            CalendarEvent.starts_at < sync_until,
        ))).scalars().all():
            if row.external_id not in seen:
                row.status = "revoked"
                revoked += 1
    if cursor:
        integration.sync_cursor = cursor
    integration.status = "connected"
    integration.last_synced_at = utcnow()
    integration.last_error = None
    await session.flush()
    return {"connected": True, "synced": True, "created": created, "updated": updated, "revoked": revoked, "last_synced_at": integration.last_synced_at.isoformat()}


async def connect_from_callback(
    session: AsyncSession, account_id: uuid.UUID, code: str, *,
    provider: GoogleCalendarProvider | None = None,
) -> dict[str, Any]:
    if not code or len(code) > 2048:
        raise ValidationFailedError("Google Calendar could not be connected.", field="code")
    provider = provider or GoogleCalendarProvider(credential_store(session))
    existing = await _integration(session, account_id, lock=True)
    token_response = await provider.exchange_code(code, GOOGLE_CALENDAR_REDIRECT_URI)
    refresh_token = token_response.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        if existing is None or not existing.credential_ref:
            raise ValidationFailedError("Google did not provide a refresh credential. Please reconnect and approve access.", field="provider")
        credential_ref = existing.credential_ref
    else:
        store = provider.credential_store
        credential_ref = await store.replace(existing.credential_ref, refresh_token) if existing and existing.credential_ref else await store.store(refresh_token)
    if existing is None:
        existing = ExternalIntegration(
            account_id=account_id, kind=INTEGRATION_CALENDAR, provider=GOOGLE_PROVIDER,
            status="connected", scopes=[GOOGLE_CALENDAR_SCOPE], credential_ref=credential_ref,
            external_account_label="Google Calendar",
        )
        session.add(existing)
    else:
        existing.status = "connected"
        existing.scopes = [GOOGLE_CALENDAR_SCOPE]
        existing.credential_ref = credential_ref
        existing.revoked_at = None
        existing.last_error = None
    await session.flush()
    return {"integration": existing}


async def disconnect_google_calendar(session: AsyncSession, account_id: uuid.UUID, *, provider: GoogleCalendarProvider | None = None) -> dict[str, Any]:
    integration = await _integration(session, account_id, lock=True)
    if integration is None:
        return {"status": "revoked", "revoked": False, "message": "Google Calendar is already disconnected."}
    if integration.credential_ref:
        provider = provider or GoogleCalendarProvider(credential_store(session))
    if integration.credential_ref and not await provider.revoke(integration.credential_ref):
        integration.status = "revocation_pending"
        integration.last_error = "revocation_pending"
        for event in (await session.execute(select(CalendarEvent).where(CalendarEvent.integration_id == integration.id, CalendarEvent.status == "active"))).scalars().all():
            event.status = "revoked"
        await session.flush()
        return {"status": "revocation_pending", "revoked": False, "message": "Google Calendar has stopped feeding your plan; we will finish disconnecting shortly."}
    if integration.credential_ref:
        await provider.credential_store.delete(integration.credential_ref)
    for event in (await session.execute(select(CalendarEvent).where(CalendarEvent.integration_id == integration.id, CalendarEvent.status == "active"))).scalars().all():
        event.status = "revoked"
    integration.status = "revoked"
    integration.revoked_at = utcnow()
    integration.credential_ref = None
    integration.sync_cursor = None
    integration.last_error = None
    await session.flush()
    return {"status": "revoked", "revoked": True, "message": "Google Calendar is disconnected. Manual events remain available."}


__all__ = ["async_authorization_url", "connect_from_callback", "consume_state", "disconnect_google_calendar", "sync_google_calendar"]
