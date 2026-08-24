"""Read-only Google Calendar v3 adapter.

The adapter is deliberately narrow: it requests the primary calendar, expands
recurrences, retains only the fields needed by ``CalendarEvent``, and keeps
short-lived access tokens in memory. Refresh credentials are resolved through
the injected opaque credential store.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import (
    GOOGLE_CALENDAR_CLIENT_ID,
    GOOGLE_CALENDAR_CLIENT_SECRET,
    GOOGLE_CALENDAR_EVENTS_ENDPOINT,
    GOOGLE_CALENDAR_SCOPE,
    GOOGLE_CALENDAR_TIMEOUT_SECONDS,
    GOOGLE_OAUTH_REVOCATION_ENDPOINT,
    GOOGLE_OAUTH_TOKEN_ENDPOINT,
)
from app.domains.planning.credentials import CalendarCredentialStore
from app.domains.planning.providers.base import CalendarEventReading, CalendarProvider, ProviderUnavailable


class GoogleSyncTokenExpired(ProviderUnavailable):
    def __init__(self) -> None:
        super().__init__("Google Calendar synchronization must be reset.", provider="google", reason="sync_token_expired")


class MalformedGoogleEvent(ProviderUnavailable):
    """A provider change could not be normalized safely; retry the same cursor."""

    def __init__(self) -> None:
        super().__init__("Google Calendar returned an unusable event.", provider="google", reason="malformed_event")


class IncompleteGoogleSync(ProviderUnavailable):
    """Google did not provide the cursor proving the final page completed."""

    def __init__(self) -> None:
        super().__init__("Google Calendar synchronization was incomplete.", provider="google", reason="missing_sync_token")


def _aware(value: str, timezone_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(timezone_name))


def _all_day_bounds(start_value: str, end_value: str | None, timezone_name: str) -> tuple[datetime, datetime | None]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(date.fromisoformat(start_value), time.min, tzinfo=zone)
    end = datetime.combine(date.fromisoformat(end_value), time.min, tzinfo=zone) if end_value else None
    return start, end


def normalize_google_event(item: dict[str, Any], timezone_name: str) -> CalendarEventReading:
    if not isinstance(item.get("id"), str) or not item["id"].strip():
        raise ValueError("Google event has no usable id")
    start = item.get("start") or {}
    end = item.get("end") or {}
    if item.get("status") == "cancelled" and not start.get("date") and not start.get("dateTime"):
        # Google may send a deletion tombstone with only id/status. Keep it in
        # the change stream so an existing canonical row can be revoked, but
        # never create a new visible event for a tombstone.
        return CalendarEventReading(
            external_id=item["id"].strip(), title="Calendar event",
            starts_at=datetime(1970, 1, 1, tzinfo=UTC), provider="google",
            source="integration", status="revoked", raw={"tombstone": True},
        )
    all_day = "date" in start
    if all_day:
        starts_at, ends_at = _all_day_bounds(start["date"], end.get("date"), timezone_name)
    else:
        if not start.get("dateTime"):
            raise ValueError("Google event has no usable start")
        starts_at = _aware(start["dateTime"], timezone_name)
        ends_at = _aware(end["dateTime"], timezone_name) if end.get("dateTime") else None
    return CalendarEventReading(
        external_id=item["id"].strip(),
        title=(str(item.get("summary") or "").strip() or "Calendar event")[:240],
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        location=(str(item.get("location")).strip()[:200] if item.get("location") else None),
        provider="google",
        source="integration",
        status="revoked" if item.get("status") == "cancelled" else "active",
    )


class GoogleCalendarProvider(CalendarProvider):
    name = "google"
    scope = GOOGLE_CALENDAR_SCOPE

    def __init__(self, credential_store: CalendarCredentialStore, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.credential_store = credential_store
        self.transport = transport
        self._access_token: str | None = None

    def is_configured(self) -> bool:
        from app.config import GOOGLE_CALENDAR_ENABLED
        return bool(GOOGLE_CALENDAR_ENABLED and GOOGLE_CALENDAR_CLIENT_ID and GOOGLE_CALENDAR_CLIENT_SECRET)

    async def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        return await self._token_request({
            "code": code, "client_id": GOOGLE_CALENDAR_CLIENT_ID, "client_secret": GOOGLE_CALENDAR_CLIENT_SECRET,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        })

    async def refresh(self, credential_ref: str) -> str:
        refresh_token = await self.credential_store.read(credential_ref)
        if not refresh_token:
            raise ProviderUnavailable("Google Calendar needs to be connected again.", provider="google", reason="missing_refresh_credential")
        payload = await self._token_request({
            "refresh_token": refresh_token, "client_id": GOOGLE_CALENDAR_CLIENT_ID,
            "client_secret": GOOGLE_CALENDAR_CLIENT_SECRET, "grant_type": "refresh_token",
        })
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderUnavailable("Google Calendar needs to be connected again.", provider="google", reason="invalid_token_response")
        self._access_token = token
        return token

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=GOOGLE_CALENDAR_TIMEOUT_SECONDS) as client:
                    response = await client.post(GOOGLE_OAUTH_TOKEN_ENDPOINT, data=data)
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(0)
                    continue
                if response.status_code >= 400:
                    error_code = None
                    try:
                        body = response.json()
                        error_code = body.get("error") if isinstance(body, dict) else None
                    except ValueError:
                        pass
                    if error_code == "invalid_grant" and "refresh_token" in data:
                        raise ProviderUnavailable("Google Calendar needs to be connected again.", provider="google", reason="reconnect_required")
                    raise ProviderUnavailable("Google Calendar authorization failed.", provider="google", reason="authorization_failed")
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("token response is not an object")
                return body
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0)
                    continue
                raise ProviderUnavailable("Google Calendar is temporarily unavailable.", provider="google", reason="provider_unavailable") from exc
        raise AssertionError("unreachable")

    async def _request(self, credential_ref: str, params: dict[str, str]) -> dict[str, Any]:
        if self._access_token is None:
            await self.refresh(credential_ref)
        assert self._access_token is not None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=GOOGLE_CALENDAR_TIMEOUT_SECONDS) as client:
                    response = await client.get(GOOGLE_CALENDAR_EVENTS_ENDPOINT, params=params, headers={"Authorization": f"Bearer {self._access_token}"})
                if response.status_code == 410:
                    raise GoogleSyncTokenExpired()
                if response.status_code == 401 and attempt == 0:
                    await self.refresh(credential_ref)
                    continue
                if response.status_code in {408, 429, 500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(0)
                    continue
                if response.status_code >= 400:
                    raise ProviderUnavailable("Google Calendar could not be read.", provider="google", reason="provider_error")
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("calendar response is not an object")
                return body
            except GoogleSyncTokenExpired:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0)
                    continue
                raise ProviderUnavailable("Google Calendar is temporarily unavailable.", provider="google", reason="provider_unavailable") from exc
        raise AssertionError("unreachable")

    async def fetch_changes(
        self, *, credential_ref: str, timezone_name: str, sync_cursor: str | None,
        since: datetime | None = None, until: datetime | None = None,
    ) -> tuple[list[CalendarEventReading], str | None, bool]:
        params: dict[str, str] = {"singleEvents": "true", "showDeleted": "true", "maxResults": "2500"}
        if sync_cursor:
            params["syncToken"] = sync_cursor
        else:
            if since is None or until is None:
                raise ValueError("initial Google sync requires bounds")
            params["timeMin"] = since.isoformat()
            params["timeMax"] = until.isoformat()
            params["orderBy"] = "startTime"
        readings: list[CalendarEventReading] = []
        next_page: str | None = None
        next_cursor: str | None = None
        while True:
            query = dict(params)
            if next_page:
                query["pageToken"] = next_page
            body = await self._request(credential_ref, query)
            for item in body.get("items", []) or []:
                if not isinstance(item, dict):
                    raise MalformedGoogleEvent()
                try:
                    readings.append(normalize_google_event(item, timezone_name))
                except (KeyError, ValueError):
                    raise MalformedGoogleEvent() from None
            next_page = body.get("nextPageToken")
            if not next_page:
                next_cursor = body.get("nextSyncToken")
                if not isinstance(next_cursor, str) or not next_cursor:
                    raise IncompleteGoogleSync()
                break
        return readings, next_cursor, sync_cursor is None

    async def events(self, *, since: datetime, until: datetime, credential_ref: str | None) -> list[CalendarEventReading]:
        if not credential_ref:
            raise ProviderUnavailable("Google Calendar needs to be connected.", provider="google", reason="missing_refresh_credential")
        rows, _cursor, _initial = await self.fetch_changes(credential_ref=credential_ref, timezone_name=str(since.tzinfo), sync_cursor=None, since=since, until=until)
        return rows

    async def revoke(self, credential_ref: str) -> bool:
        try:
            refresh_token = await self.credential_store.read(credential_ref)
        except Exception:  # noqa: BLE001 — callers retain the reference for retry
            return False
        if not refresh_token:
            return True
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=GOOGLE_CALENDAR_TIMEOUT_SECONDS) as client:
                response = await client.post(GOOGLE_OAUTH_REVOCATION_ENDPOINT, data={"token": refresh_token})
            if response.status_code == 200:
                return True
            if response.status_code == 400:
                # Google documents invalid_token as the already-revoked case;
                # other 400 responses are unresolved and must be retried.
                try:
                    body = response.json()
                    if isinstance(body, dict) and body.get("error") == "invalid_token":
                        return True
                except ValueError:
                    pass
                return False
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                return False
            return False
        except httpx.HTTPError:
            return False


__all__ = ["GoogleCalendarProvider", "GoogleSyncTokenExpired", "MalformedGoogleEvent", "IncompleteGoogleSync", "normalize_google_event"]
