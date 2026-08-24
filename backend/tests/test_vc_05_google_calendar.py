from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from app.domains.planning import calendar_sync
from app.domains.planning.credentials import InMemoryCredentialStore
from app.domains.planning.providers.google_calendar import (
    GoogleCalendarProvider,
    GoogleSyncTokenExpired,
    normalize_google_event,
)


class _Session:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_authorization_state_is_opaque_and_scope_is_read_only(monkeypatch):
    monkeypatch.setattr("app.config.GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_REDIRECT_URI", "https://example.test/callback")
    session = _Session()

    url, expires_at = await calendar_sync.async_authorization_url(session, uuid4())
    query = parse_qs(urlparse(url).query)
    state = query["state"][0]

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["scope"] == ["https://www.googleapis.com/auth/calendar.events.readonly"]
    assert query["access_type"] == ["offline"]
    assert query["response_type"] == ["code"]
    assert len(state) >= 32
    assert session.added[0].state_hash != state
    assert expires_at > datetime.now(UTC)


def test_google_event_normalization_preserves_all_day_and_cancellation():
    row = normalize_google_event(
        {"id": "event-1", "summary": "", "start": {"date": "2026-08-10"}, "end": {"date": "2026-08-12"}},
        "Asia/Kolkata",
    )
    cancelled = normalize_google_event(
        {"id": "event-2", "status": "cancelled", "start": {"dateTime": "2026-08-10T09:00:00+05:30"}},
        "Asia/Kolkata",
    )
    tombstone = normalize_google_event({"id": "event-3", "status": "cancelled"}, "Asia/Kolkata")

    assert row.title == "Calendar event"
    assert row.all_day is True
    assert row.starts_at.isoformat() == "2026-08-10T00:00:00+05:30"
    assert row.ends_at.isoformat() == "2026-08-12T00:00:00+05:30"
    assert cancelled.status == "revoked"
    assert tombstone.status == "revoked" and tombstone.raw == {"tombstone": True}


@pytest.mark.asyncio
async def test_google_provider_paginates_initial_and_omits_forbidden_incremental_params():
    requests = []
    responses = [
        {"items": [{"id": "event-1", "summary": "Launch", "start": {"dateTime": "2026-08-10T09:00:00Z"}}], "nextPageToken": "page-2"},
        {"items": [], "nextSyncToken": "cursor-1"},
        {"items": [{"id": "event-1", "summary": "Updated", "start": {"dateTime": "2026-08-10T09:00:00Z"}}], "nextSyncToken": "cursor-2"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=responses.pop(0))

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    provider._access_token = "access-token"
    rows, cursor, initial = await provider.fetch_changes(
        credential_ref="memory:1", timezone_name="Asia/Kolkata", sync_cursor=None,
        since=datetime(2026, 8, 10, tzinfo=UTC), until=datetime(2026, 8, 20, tzinfo=UTC),
    )
    incremental, next_cursor, is_initial = await provider.fetch_changes(
        credential_ref="memory:1", timezone_name="Asia/Kolkata", sync_cursor=cursor,
    )

    assert [row.external_id for row in rows] == ["event-1"]
    assert rows[0].title == "Launch"
    assert cursor == "cursor-1" and initial is True
    assert incremental[0].title == "Updated" and next_cursor == "cursor-2" and is_initial is False
    assert "orderBy" in dict(requests[0].url.params)
    assert "timeMin" not in dict(requests[2].url.params)
    assert "timeMax" not in dict(requests[2].url.params)
    assert "orderBy" not in dict(requests[2].url.params)
    assert dict(requests[2].url.params)["syncToken"] == "cursor-1"


@pytest.mark.asyncio
async def test_google_provider_exposes_expired_sync_token_for_one_time_reset():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"error": {"code": 410}})

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    provider._access_token = "access-token"

    with pytest.raises(GoogleSyncTokenExpired) as error:
        await provider.fetch_changes(credential_ref="memory:1", timezone_name="UTC", sync_cursor="stale")

    assert error.value.reason == "sync_token_expired"


@pytest.mark.asyncio
async def test_in_memory_credential_replace_keeps_opaque_reference_and_new_secret():
    store = InMemoryCredentialStore()
    old_ref = await store.store("old-refresh-token")
    same_ref = await store.replace(old_ref, "new-refresh-token")

    assert same_ref == old_ref
    assert await store.read(old_ref) == "new-refresh-token"
