import asyncio
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from app.api.v2 import integrations as integrations_api
from app.domains.planning import calendar_sync, event_ready
from app.domains.planning import context as planning_context
from app.domains.planning import service as planning_service
from app.domains.planning.credentials import InMemoryCredentialStore, SupabaseVaultCredentialStore
from app.domains.planning.models import CalendarEvent, ExternalIntegration, ExternalOAuthState
from app.domains.planning.providers.base import CalendarEventReading, ProviderUnavailable
from app.domains.planning.providers.google_calendar import (
    GoogleCalendarProvider,
    GoogleSyncTokenExpired,
    IncompleteGoogleSync,
    MalformedGoogleEvent,
    normalize_google_event,
)
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ValidationFailedError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError


class _Session:
    def __init__(self, integration=None) -> None:
        self.added = []
        self.integration = integration

    def add(self, value) -> None:
        self.added.append(value)

    async def execute(self, _statement):
        return _VaultResult(self.integration)

    async def flush(self) -> None:
        return None


class _VaultResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _VaultSession:
    def __init__(self):
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return _VaultResult("vault-id")


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _DisconnectSession:
    def __init__(self, integration, events):
        self._integration = integration
        self._events = events
        self.results = [_Rows([integration]), _Rows(events)]
        self.flushes = []

    async def execute(self, _statement):
        return self.results.pop(0)

    async def flush(self):
        self.flushes.append((self.integration.status, [event.status for event in self.events]))

    @property
    def integration(self):
        return self._integration

    @property
    def events(self):
        return self._events


class _RecordingCredentialStore:
    def __init__(self, *, read_error=False, delete_error=False, read_value="refresh-token"):
        self.read_error = read_error
        self.delete_error = delete_error
        self.read_value = read_value
        self.deleted = []

    async def read(self, _ref):
        if self.read_error:
            raise RuntimeError("vault unavailable")
        return self.read_value

    async def delete(self, ref):
        if self.delete_error:
            raise RuntimeError("vault unavailable")
        self.deleted.append(ref)


class _RecordingProvider:
    def __init__(self, store, *, revoke_result=True):
        self.credential_store = store
        self.revoke_result = revoke_result
        self.revoke_calls = []

    async def revoke(self, ref):
        self.revoke_calls.append(ref)
        if self.credential_store.read_error:
            return False
        return self.revoke_result


class _EmptySession:
    async def execute(self, _statement):
        return _Rows([])


class _CommitSession:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class _FakeSyncProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def fetch_changes(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _CoordinatedSyncProvider:
    def __init__(self, response, *, entered=None, release=None):
        self.response = response
        self.entered = entered
        self.release = release
        self.calls = []

    async def fetch_changes(self, **kwargs):
        self.calls.append(kwargs)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return self.response


def _reading(external_id, *, title="Launch", starts_at=None, location="Mumbai", status="active", raw=None):
    return CalendarEventReading(
        external_id=external_id, title=title,
        starts_at=starts_at or datetime(2026, 8, 10, 9, tzinfo=UTC),
        location=location, provider="google", source="integration", status=status,
        raw=raw or {},
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize("read_value", [None, RuntimeError("vault unavailable")])
async def test_google_refresh_credential_failures_are_safe(read_value):
    class Store:
        async def read(self, _ref):
            if isinstance(read_value, BaseException):
                raise read_value
            return read_value

    provider = GoogleCalendarProvider(Store())
    with pytest.raises(ProviderUnavailable) as error:
        await provider.refresh("opaque-ref")
    assert error.value.reason == (
        "provider_unavailable" if isinstance(read_value, BaseException) else "reconnect_required"
    )
    assert "vault unavailable" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "credential_ref", "expects_prompt"),
    [("connected", "vault:1", False), ("reconnect_required", "vault:1", True), ("connected", None, True)],
)
async def test_authorization_requests_consent_only_for_credential_recovery(
    monkeypatch, status, credential_ref, expects_prompt,
):
    monkeypatch.setattr("app.config.GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_REDIRECT_URI", "https://example.test/callback")
    integration = SimpleNamespace(status=status, credential_ref=credential_ref)
    session = _Session(integration)

    url, _ = await calendar_sync.async_authorization_url(session, uuid4())
    query = parse_qs(urlparse(url).query)
    assert ("prompt" in query) is expects_prompt
    if expects_prompt:
        assert query["prompt"] == ["consent"]


@pytest.mark.asyncio
async def test_generic_calendar_disconnect_is_manual_only(monkeypatch):
    calls = []
    session = _CommitSession()
    account_id = uuid4()

    async def manual_disconnect(_session, value):
        calls.append(value)
        return []

    async def calendar_status(_session, value):
        assert value == account_id
        return {"integrations": [{"provider": "google", "status": "revocation_pending"}]}

    monkeypatch.setattr(integrations_api.service, "disconnect_manual_calendar", manual_disconnect)
    monkeypatch.setattr(integrations_api.service, "calendar_status", calendar_status)
    body = await integrations_api.disconnect_calendar(
        current=SimpleNamespace(account_id=account_id), session=session,
    )

    assert calls == [account_id]
    assert session.committed is True
    assert body["revoked"] == 0
    assert "Manual calendar access is disconnected" in body["message"]
    assert "Google Calendar uses its own secure disconnect control" in body["message"]


@pytest.mark.asyncio
async def test_reconnect_without_new_refresh_token_does_not_claim_connected():
    existing = SimpleNamespace(status="reconnect_required", credential_ref="vault:1")
    session = _Session(existing)

    class Provider:
        credential_store = _RecordingCredentialStore()

        async def exchange_code(self, _code, _redirect_uri):
            return {"access_token": "short-lived-only"}

    with pytest.raises(ValidationFailedError, match="refresh credential"):
        await calendar_sync.connect_from_callback(session, uuid4(), "oauth-code", provider=Provider())


@pytest.mark.asyncio
async def test_oauth_state_is_single_use_and_denied_callback_consumes_it(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    now = datetime.now(UTC)
    valid_raw, denied_raw, expired_raw = "valid-state", "denied-state", "expired-state"
    async with factory() as session:
        session.add_all([
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state(valid_raw), expires_at=now + timedelta(minutes=5)),
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state(denied_raw), expires_at=now + timedelta(minutes=5)),
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state(expired_raw), expires_at=now - timedelta(minutes=1)),
        ])
        await session.commit()

    async with factory() as session:
        await calendar_sync.consume_state(session, valid_raw)
        await session.commit()
        with pytest.raises(ValidationFailedError):
            await calendar_sync.consume_state(session, valid_raw)
        with pytest.raises(ValidationFailedError):
            await calendar_sync.consume_state(session, expired_raw)
        with pytest.raises(ValidationFailedError):
            await calendar_sync.consume_state(session, "unknown-state")

    async with factory() as session:
        response = await integrations_api.google_callback(
            code=None, state=denied_raw, error="access_denied", session=session,
        )
        assert response.headers["location"].endswith("result=denied")
        await session.commit()
        with pytest.raises(ValidationFailedError):
            await calendar_sync.consume_state(session, denied_raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "revoke", "delete"])
async def test_google_disconnect_disables_events_before_cleanup_and_stays_pending(failure):
    integration = SimpleNamespace(
        id=uuid4(), status="connected", credential_ref="vault:1", revoked_at=None,
        sync_cursor="cursor", last_error=None,
    )
    event = SimpleNamespace(status="dismissed")
    session = _DisconnectSession(integration, [event])
    store = _RecordingCredentialStore(read_error=failure == "read", delete_error=failure == "delete")
    provider = _RecordingProvider(store, revoke_result=failure != "revoke")

    result = await calendar_sync.disconnect_google_calendar(session, uuid4(), provider=provider)

    assert result["status"] == "revocation_pending"
    assert integration.status == "revocation_pending"
    assert integration.credential_ref == "vault:1"
    assert event.status == "revoked"
    assert session.flushes[0] == ("revocation_pending", ["revoked"])


@pytest.mark.asyncio
async def test_google_disconnect_retry_finishes_after_pending_cleanup_and_is_idempotent():
    integration = SimpleNamespace(
        id=uuid4(), status="revocation_pending", credential_ref="vault:1", revoked_at=None,
        sync_cursor="cursor", last_error="revocation_pending",
    )
    event = SimpleNamespace(status="revoked")
    session = _DisconnectSession(integration, [event])
    store = _RecordingCredentialStore()
    provider = _RecordingProvider(store)

    result = await calendar_sync.disconnect_google_calendar(session, uuid4(), provider=provider)

    assert result["status"] == "revoked"
    assert integration.status == "revoked"
    assert integration.credential_ref is None
    assert integration.sync_cursor is None
    assert store.deleted == ["vault:1"]

    repeated = await calendar_sync.disconnect_google_calendar(_EmptySession(), uuid4())
    assert repeated["status"] == "revoked" and repeated["revoked"] is False


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
async def test_google_provider_rejects_incomplete_final_page_without_cursor():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    provider._access_token = "access-token"
    with pytest.raises(IncompleteGoogleSync) as error:
        await provider.fetch_changes(
            credential_ref="memory:1", timezone_name="UTC", sync_cursor="cursor-1",
        )
    assert error.value.reason == "missing_sync_token"


@pytest.mark.asyncio
async def test_in_memory_credential_replace_keeps_opaque_reference_and_new_secret():
    store = InMemoryCredentialStore()
    old_ref = await store.store("old-refresh-token")
    same_ref = await store.replace(old_ref, "new-refresh-token")

    assert same_ref == old_ref
    assert await store.read(old_ref) == "new-refresh-token"


@pytest.mark.asyncio
async def test_revocation_is_confirmed_only_by_http_200_and_retains_unresolved_secret():
    marker = "VC05_REVOCATION_REFRESH_MARKER"
    store = InMemoryCredentialStore({"memory:1": marker})
    responses = [
        httpx.Response(400, json={"error": "invalid_token"}),
        httpx.Response(400, json={"error": "other_error"}),
        httpx.Response(429),
        httpx.Response(503),
        httpx.ConnectError("provider unavailable"),
        httpx.Response(200),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    provider = GoogleCalendarProvider(store, transport=httpx.MockTransport(handler))
    for _ in range(5):
        assert await provider.revoke("memory:1") is False
        assert await store.read("memory:1") == marker
    assert await provider.revoke("memory:1") is True
    assert await store.read("memory:1") == marker


@pytest.mark.asyncio
async def test_revocation_with_missing_vault_secret_is_unresolved():
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    provider = GoogleCalendarProvider(
        InMemoryCredentialStore({"memory:missing": None}),
        transport=httpx.MockTransport(handler),
    )

    assert await provider.revoke("memory:missing") is False
    assert called is False


@pytest.mark.asyncio
async def test_disconnect_with_missing_vault_secret_stays_pending_and_disables_events():
    integration = SimpleNamespace(
        id=uuid4(), status="connected", credential_ref="memory:missing", revoked_at=None,
        sync_cursor="cursor", last_error=None,
    )
    event = SimpleNamespace(status="active")
    session = _DisconnectSession(integration, [event])
    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:missing": None}))

    result = await calendar_sync.disconnect_google_calendar(session, uuid4(), provider=provider)

    assert result["status"] == "revocation_pending"
    assert integration.status == "revocation_pending"
    assert integration.credential_ref == "memory:missing"
    assert event.status == "revoked"


@pytest.mark.asyncio
async def test_malformed_changed_item_raises_before_cursor_is_returned():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"id": "event-1", "start": {}}], "nextSyncToken": "must-not-advance"})

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    provider._access_token = "access-token"
    with pytest.raises(MalformedGoogleEvent) as error:
        await provider.fetch_changes(credential_ref="memory:1", timezone_name="UTC", sync_cursor="cursor-1")
    assert error.value.reason == "malformed_event"


@pytest.mark.asyncio
async def test_sync_google_calendar_initial_repeat_and_user_authority(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1",
        ))
        await session.commit()

    provider = _FakeSyncProvider([
        ([_reading("google-1")], "cursor-1", True),
        ([_reading("google-1", title="Moved", starts_at=datetime(2026, 8, 11, 10, tzinfo=UTC), location="Delhi")], "cursor-2", False),
        ([_reading("google-1", title="Provider title", starts_at=datetime(2026, 8, 12, 11, tzinfo=UTC), location="Pune")], "cursor-3", False),
        ([_reading("google-1", status="revoked", raw={"tombstone": True})], "cursor-4", False),
    ])
    async with factory() as session:
        first = await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.external_id == "google-1"))).scalar_one()
        first_id = row.id
        assert first["created"] == 1 and row.provider == "google" and row.source == "integration"
        assert row.starts_at == datetime(2026, 8, 10, 9, tzinfo=UTC)

        second = await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.external_id == "google-1"))).scalar_one()
        assert second["updated"] == 1 and row.id == first_id and row.title == "Moved" and row.location == "Delhi"

        row.occasion_key = "wedding"
        row.dress_code_hint = "formal"
        row.status = "dismissed"
        row.user_overrides = {"occasion_key": True, "dress_code_hint": True, "status": True, "title": True}
        await session.flush()
        await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.external_id == "google-1"))).scalar_one()
        assert row.id == first_id and row.occasion_key == "wedding" and row.dress_code_hint == "formal"
        assert row.status == "dismissed" and row.starts_at == datetime(2026, 8, 12, 11, tzinfo=UTC)
        assert row.title == "Moved" and row.location == "Pune"

        await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.external_id == "google-1"))).scalar_one()
        assert row.id == first_id and row.status == "revoked"
    assert [call["sync_cursor"] for call in provider.calls] == [None, "cursor-1", "cursor-2", "cursor-3"]


@pytest.mark.asyncio
async def test_sync_same_integration_serializes_two_postgres_sessions(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1",
        ))
        await session.commit()

    a_entered = asyncio.Event()
    release_a = asyncio.Event()
    provider_a = _CoordinatedSyncProvider(
        ([_reading("concurrent-event", title="A", location="A")], "cursor-a", True),
        entered=a_entered, release=release_a,
    )
    provider_b = _CoordinatedSyncProvider(
        ([_reading("concurrent-event", title="B", location="B")], "cursor-b", False),
    )
    application_name = f"vc05-sync-b-{uuid4().hex}"

    async def wait_for_lock_wait() -> bool:
        async with factory() as probe:
            for _ in range(100):
                row = (await probe.execute(text("""
                    SELECT wait_event_type, pg_blocking_pids(pid) AS blockers
                    FROM pg_stat_activity
                    WHERE application_name = :application_name
                      AND wait_event_type = 'Lock'
                """), {"application_name": application_name})).first()
                if row is not None:
                    return bool(row.blockers)
                await asyncio.sleep(0.05)
        return False

    async with factory() as session_a, factory() as session_b:
        await session_b.execute(
            text("SELECT set_config('application_name', :application_name, false)"),
            {"application_name": application_name},
        )
        task_a = asyncio.create_task(
            calendar_sync.sync_google_calendar(session_a, account_id, "UTC", provider=provider_a),
        )
        await asyncio.wait_for(a_entered.wait(), timeout=5)
        task_b = asyncio.create_task(
            calendar_sync.sync_google_calendar(session_b, account_id, "UTC", provider=provider_b),
        )
        try:
            assert await asyncio.wait_for(wait_for_lock_wait(), timeout=5)
            release_a.set()
            result_a = await asyncio.wait_for(task_a, timeout=5)
            await session_a.commit()
            result_b = await asyncio.wait_for(task_b, timeout=5)
            await session_b.commit()
        finally:
            release_a.set()
            if not task_a.done():
                task_a.cancel()
            if not task_b.done():
                task_b.cancel()
            await asyncio.gather(task_a, task_b, return_exceptions=True)

    async with factory() as session:
        events = (await session.execute(select(CalendarEvent).where(
            CalendarEvent.integration_id == integration_id,
            CalendarEvent.external_id == "concurrent-event",
        ))).scalars().all()
        integration = await session.get(ExternalIntegration, integration_id)
    assert result_a["created"] == 1
    assert result_b["updated"] == 1
    assert len(events) == 1
    assert events[0].title == "B" and events[0].location == "B"
    assert integration.sync_cursor == "cursor-b"


@pytest.mark.asyncio
async def test_sync_different_integrations_progress_without_global_lock(
    db_clean, registered_supabase_user,
):
    _, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    factory = get_sessionmaker()
    integration_a_id, integration_b_id = uuid4(), uuid4()
    async with factory() as session:
        session.add_all([
            ExternalIntegration(
                id=integration_a_id, account_id=account_a, kind="calendar", provider="google",
                status="connected", scopes=["readonly"], credential_ref="memory:a",
            ),
            ExternalIntegration(
                id=integration_b_id, account_id=account_b, kind="calendar", provider="google",
                status="connected", scopes=["readonly"], credential_ref="memory:b",
            ),
        ])
        await session.commit()

    a_entered = asyncio.Event()
    release_a = asyncio.Event()
    b_entered = asyncio.Event()
    provider_a = _CoordinatedSyncProvider(
        ([_reading("event-a", title="A")], "cursor-a", True),
        entered=a_entered, release=release_a,
    )
    provider_b = _CoordinatedSyncProvider(
        ([_reading("event-b", title="B")], "cursor-b", True),
        entered=b_entered,
    )

    async with factory() as session_a, factory() as session_b:
        task_a = asyncio.create_task(
            calendar_sync.sync_google_calendar(session_a, account_a, "UTC", provider=provider_a),
        )
        await asyncio.wait_for(a_entered.wait(), timeout=5)
        task_b = asyncio.create_task(
            calendar_sync.sync_google_calendar(session_b, account_b, "UTC", provider=provider_b),
        )
        try:
            result_b = await asyncio.wait_for(task_b, timeout=5)
            await session_b.commit()
            assert b_entered.is_set()
            assert not release_a.is_set()
            release_a.set()
            result_a = await asyncio.wait_for(task_a, timeout=5)
            await session_a.commit()
        finally:
            release_a.set()
            if not task_a.done():
                task_a.cancel()
            if not task_b.done():
                task_b.cancel()
            await asyncio.gather(task_a, task_b, return_exceptions=True)

    async with factory() as session:
        events = (await session.execute(select(CalendarEvent).where(
            CalendarEvent.integration_id.in_([integration_a_id, integration_b_id]),
        ))).scalars().all()
    assert result_a["created"] == 1 and result_b["created"] == 1
    assert {event.external_id for event in events} == {"event-a", "event-b"}


@pytest.mark.asyncio
async def test_sync_google_calendar_410_reset_reconciles_owned_horizon(monkeypatch, db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 11, 1, tzinfo=UTC)
    monkeypatch.setattr(calendar_sync, "_bounds", lambda _timezone: (start, end))
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1", sync_cursor="old",
        ))
        await session.flush()
        for external_id, when, status, overrides in (
            ("present", datetime(2026, 8, 10, 9, tzinfo=UTC), "dismissed", {"status": True}),
            ("dismissed-absent", datetime(2026, 8, 11, 9, tzinfo=UTC), "dismissed", {"status": True}),
            ("active-absent", datetime(2026, 8, 12, 9, tzinfo=UTC), "active", {}),
            ("already-revoked", datetime(2026, 8, 13, 9, tzinfo=UTC), "revoked", {}),
            ("outside-horizon", datetime(2026, 11, 2, 9, tzinfo=UTC), "active", {}),
        ):
            session.add(CalendarEvent(
                account_id=account_id, integration_id=integration_id, external_id=external_id,
                dedup_key=f"google:{external_id}", title=external_id, starts_at=when,
                provider="google", source="integration", status=status, user_overrides=overrides,
            ))
        await session.commit()

    provider = _FakeSyncProvider([
        GoogleSyncTokenExpired(),
        ([_reading("present", title="Present", starts_at=datetime(2026, 8, 10, 10, tzinfo=UTC))], "new", True),
    ])
    async with factory() as session:
        result = await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        rows = {row.external_id: row for row in (await session.execute(select(CalendarEvent).where(CalendarEvent.integration_id == integration_id))).scalars()}
    assert result["synced"] is True and result["revoked"] == 2
    assert len(provider.calls) == 2 and provider.calls[0]["sync_cursor"] == "old" and provider.calls[1]["sync_cursor"] is None
    assert rows["present"].status == "dismissed"
    assert rows["dismissed-absent"].status == "revoked"
    assert rows["active-absent"].status == "revoked"
    assert rows["already-revoked"].status == "revoked"
    assert rows["outside-horizon"].status == "active"


@pytest.mark.asyncio
async def test_synced_google_event_is_consumed_by_upcoming_today_and_event_ready(
    monkeypatch, db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    starts_at = datetime.now(UTC) + timedelta(days=2)
    starts_at = starts_at.replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(
        calendar_sync, "_bounds",
        lambda _timezone: (starts_at - timedelta(days=1), starts_at + timedelta(days=90)),
    )
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1",
        ))
        await session.commit()

    provider = _FakeSyncProvider([
        ([_reading("canonical-google", title="Client dinner", starts_at=starts_at, location="Delhi")], "canonical-cursor", True),
    ])
    async with factory() as session:
        result = await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(
            CalendarEvent.integration_id == integration_id,
            CalendarEvent.external_id == "canonical-google",
        ))).scalar_one()
        event_id = row.id
        upcoming = await planning_service.upcoming_events(session, account_id, "UTC", days=90)
        todays_events = await planning_context.day_events(session, account_id, starts_at.date(), "UTC")
        captured: dict[str, object] = {}
        original_gather = planning_context.gather

        async def capture_gather(*args, **kwargs):
            captured.update(kwargs)
            return await original_gather(*args, **kwargs)

        monkeypatch.setattr(event_ready.context_stage, "gather", capture_gather)
        ready = await event_ready.generate(session, account_id, event_id)

    assert result["synced"] is True
    assert [item.id for item in upcoming] == [event_id]
    assert [item.id for item in todays_events] == [event_id]
    assert todays_events[0].location == "Delhi"
    assert ready["event"]["id"] == str(event_id)
    assert ready["event"]["location"] == "Delhi"
    assert captured["environment_location"] == "Delhi"
    assert captured["explicit_environment_location"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    MalformedGoogleEvent(),
    IncompleteGoogleSync(),
    ProviderUnavailable("offline", provider="google", reason="provider_unavailable"),
    ProviderUnavailable("reconnect", provider="google", reason="reconnect_required"),
])
async def test_sync_google_calendar_failure_keeps_cursor_and_last_sync(failure, db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    previous = datetime(2026, 8, 9, 12, tzinfo=UTC)
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1", sync_cursor="authoritative",
            last_synced_at=previous,
        ))
        await session.commit()
    async with factory() as session:
        result = await calendar_sync.sync_google_calendar(
            session, account_id, "UTC", provider=_FakeSyncProvider([failure]),
        )
        await session.commit()
        row = (await session.execute(select(ExternalIntegration).where(ExternalIntegration.id == integration_id))).scalar_one()
    assert result["synced"] is False and result["reason"] == failure.reason
    assert result["connected"] is (failure.reason != "reconnect_required")
    assert row.status == ("reconnect_required" if failure.reason == "reconnect_required" else "temporary_failure")
    assert row.sync_cursor == "authoritative" and row.last_synced_at == previous


@pytest.mark.asyncio
async def test_google_privacy_export_omits_credentials_cursor_and_oauth_state(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    raw_state = "privacy-export-state"
    async with factory() as session:
        session.add(ExternalIntegration(
            account_id=account_id, kind="calendar", provider="google", status="connected",
            scopes=["readonly"], credential_ref="supabase-vault:secret-ref",
            sync_cursor="cursor-secret", external_account_label="Google Calendar",
        ))
        session.add(ExternalOAuthState(
            account_id=account_id, provider="google", state_hash=calendar_sync._hash_state(raw_state),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ))
        session.add(CalendarEvent(
            account_id=account_id, external_id="google-export", dedup_key="google:google-export",
            title="Planning", starts_at=datetime(2026, 8, 10, 9, tzinfo=UTC), location="Mumbai",
            provider="google", source="integration", status="active",
        ))
        await session.commit()
        from app.domains.privacy import export as export_service
        exported = await export_service.build_export(session, account_id)
    exported_text = str(exported)
    assert "supabase-vault:secret-ref" not in exported_text
    assert "cursor-secret" not in exported_text
    assert calendar_sync._hash_state(raw_state) not in exported_text
    assert raw_state not in exported_text
    assert "Planning" in exported_text and "Mumbai" in exported_text


def _run_alembic(revision: str, *, downgrade: bool = False) -> None:
    command = "downgrade" if downgrade else "upgrade"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, revision],
        cwd=Path(__file__).parents[1], env=os.environ.copy(),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_vc05_identity_migration_preserves_legacy_duplicates_and_guards_google(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()
    integration_id = uuid4()
    google_integration_id = uuid4()
    first_id, second_id = uuid4(), uuid4()
    async with factory() as session:
        await session.execute(text("""
            INSERT INTO external_integrations (id, account_id, kind, provider, status, scopes)
            VALUES (:id, :account_id, 'calendar', 'manual', 'connected', '[]'::jsonb),
                   (:google_id, :account_id, 'calendar', 'google', 'connected', '[]'::jsonb)
        """), {"id": integration_id, "google_id": google_integration_id, "account_id": account_id})
        for row_id, suffix in ((first_id, "one"), (second_id, "two")):
            await session.execute(text("""
                INSERT INTO calendar_events
                    (id, account_id, integration_id, external_id, dedup_key, title, starts_at, provider, source, status)
                VALUES (:id, :account_id, :integration_id, 'legacy-id', :dedup_key,
                        'Legacy event', '2026-08-10T09:00:00+00:00', 'manual', 'integration', 'active')
            """), {"id": row_id, "account_id": account_id, "integration_id": integration_id, "dedup_key": f"legacy:{suffix}"})
        await session.commit()

    _run_alembic("f6a7b8c9d0e1", downgrade=True)
    try:
        _run_alembic("head")
        async with factory() as session:
            assert await session.scalar(text("SELECT count(*) FROM calendar_events WHERE integration_id = :id"), {"id": integration_id}) == 2
            await session.execute(text("""
                INSERT INTO calendar_events
                    (id, account_id, integration_id, external_id, dedup_key, title, starts_at, provider, source, status, user_overrides)
                VALUES (:id, :account_id, :integration_id, 'google-id', 'google:one',
                        'Google event', '2026-08-10T09:00:00+00:00', 'google', 'integration', 'active', '{}'::jsonb)
            """), {"id": uuid4(), "account_id": account_id, "integration_id": google_integration_id})
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(text("""
                    INSERT INTO calendar_events
                        (id, account_id, integration_id, external_id, dedup_key, title, starts_at, provider, source, status, user_overrides)
                    VALUES (:id, :account_id, :integration_id, 'google-id', 'google:two',
                            'Google event moved', '2026-08-11T09:00:00+00:00', 'google', 'integration', 'active', '{}'::jsonb)
                """), {"id": uuid4(), "account_id": account_id, "integration_id": google_integration_id})
                await session.rollback()
    finally:
        _run_alembic("head")


@pytest.mark.asyncio
async def test_calendar_status_downgrade_maps_lifecycle_states_before_narrowing(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    rows = [
        ExternalIntegration(account_id=account_id, kind="calendar", provider="manual", status="revocation_pending"),
        ExternalIntegration(account_id=account_id, kind="calendar", provider="google", status="reconnect_required"),
        ExternalIntegration(account_id=account_id, kind="weather", provider="manual", status="temporary_failure"),
    ]
    async with factory() as session:
        session.add_all(rows)
        await session.commit()
    _run_alembic("g7b8c9d0e1f2", downgrade=True)
    try:
        async with factory() as session:
            values = (await session.execute(
                select(ExternalIntegration.kind, ExternalIntegration.provider, ExternalIntegration.status).where(
                    ExternalIntegration.account_id == account_id,
                )
            )).all()
        assert {(kind, provider): status for kind, provider, status in values} == {
            ("calendar", "google"): "connected",
            ("calendar", "manual"): "connected",
            ("weather", "manual"): "connected",
        }
    finally:
        _run_alembic("head")


@pytest.mark.asyncio
async def test_vault_credentials_are_unnamed_and_replace_in_place():
    session = _VaultSession()
    store = SupabaseVaultCredentialStore(session)
    first = await store.store("refresh-one")
    same = await store.replace(first, "refresh-two")

    assert first == "supabase-vault:vault-id"
    assert same == first
    assert "vault.create_secret(:secret)" in session.calls[0][0]
    assert "glamgenius-google-calendar" not in session.calls[0][0]
    assert "vault.update_secret" in session.calls[1][0]
    assert session.calls[1][1]["id"] == "vault-id"
    assert session.calls[1][1]["secret"] == "refresh-two"


def test_oauth_redaction_covers_parameterized_and_free_text_boundaries():
    from app.shared.observability.logging import OAuthRedactionFilter
    from app.shared.observability.sentry_privacy import scrub_event

    marker = "VC05_FAKE_REFRESH_MARKER"
    record = logging.LogRecord("oauth", logging.ERROR, __file__, 1, "payload=%s", ({"refresh_token": marker},), None)
    OAuthRedactionFilter().filter(record)
    assert marker not in record.getMessage()
    event = scrub_event({"exception": {"values": [{"value": f"refresh_token={marker}"}]}, "request": {"url": f"https://x/callback?code={marker}"}})
    assert marker not in str(event)


@pytest.mark.asyncio
async def test_explicit_null_correction_does_not_claim_a_field_from_the_provider(
    db_clean, registered_supabase_user, monkeypatch,
):
    """A null field in a PATCH is not a correction and must not freeze the field.

    Recording an override for a field the user never actually set would stop
    every later Google sync from updating it, silently and permanently.
    """
    _, account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    integration_id = uuid4()
    async with factory() as session:
        session.add(ExternalIntegration(
            id=integration_id, account_id=account_id, kind="calendar", provider="google",
            status="connected", scopes=["readonly"], credential_ref="memory:1",
        ))
        await session.commit()

    provider = _FakeSyncProvider([
        ([_reading("google-null", title="Original")], "cursor-1", True),
        ([_reading("google-null", title="Provider renamed")], "cursor-2", False),
    ])
    async with factory() as session:
        await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.external_id == "google-null"))).scalar_one()
        event_id = row.id

    async def resolve_timezone_for(_session, _account_id):
        return "UTC"

    monkeypatch.setattr(integrations_api.context_stage, "resolve_timezone_for", resolve_timezone_for)
    async with factory() as session:
        body = integrations_api.CalendarEventPatch(title=None, dress_code_hint="formal")
        await integrations_api.patch_event(
            event_id=event_id, body=body,
            current=SimpleNamespace(account_id=account_id), session=session,
        )
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))).scalar_one()
        # The field the user really set is claimed; the null one is not.
        assert row.user_overrides == {"dress_code_hint": True}
        assert row.dress_code_hint == "formal"
        assert row.title == "Original"

    async with factory() as session:
        await calendar_sync.sync_google_calendar(session, account_id, "UTC", provider=provider)
        await session.commit()
        row = (await session.execute(select(CalendarEvent).where(CalendarEvent.id == event_id))).scalar_one()
        assert row.title == "Provider renamed"
        assert row.dress_code_hint == "formal"


@pytest.mark.asyncio
async def test_authorization_clears_only_this_accounts_spent_and_expired_state(
    db_clean, registered_supabase_user, monkeypatch,
):
    """Dead nonces are pruned; a live one and another account's rows survive.

    A spent or expired nonce can never authorize anything again, so keeping it
    only grows the table. Pruning must stay account-scoped and must never touch
    a nonce that is still usable.
    """
    _, account_id = await registered_supabase_user()
    _, other_account_id = await registered_supabase_user()
    factory = get_sessionmaker()
    now = datetime.now(UTC)

    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(calendar_sync, "GOOGLE_CALENDAR_REDIRECT_URI", "https://api.example/callback")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENABLED", "true")
    import app.config as app_config
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)

    async with factory() as session:
        session.add_all([
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state("spent"), expires_at=now + timedelta(minutes=5), consumed_at=now),
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state("expired"), expires_at=now - timedelta(minutes=1)),
            ExternalOAuthState(account_id=account_id, provider="google", state_hash=calendar_sync._hash_state("live"), expires_at=now + timedelta(minutes=5)),
            ExternalOAuthState(account_id=other_account_id, provider="google", state_hash=calendar_sync._hash_state("other-spent"), expires_at=now + timedelta(minutes=5), consumed_at=now),
        ])
        await session.commit()

    async with factory() as session:
        url, _expires = await calendar_sync.async_authorization_url(session, account_id)
        await session.commit()
        assert "client-id" in url

    async with factory() as session:
        mine = (await session.execute(select(ExternalOAuthState).where(
            ExternalOAuthState.account_id == account_id,
        ))).scalars().all()
        theirs = (await session.execute(select(ExternalOAuthState).where(
            ExternalOAuthState.account_id == other_account_id,
        ))).scalars().all()

    # The live nonce plus the freshly issued one; the spent and expired ones are gone.
    assert len(mine) == 2
    hashes = {row.state_hash for row in mine}
    assert calendar_sync._hash_state("live") in hashes
    assert calendar_sync._hash_state("spent") not in hashes
    assert calendar_sync._hash_state("expired") not in hashes
    # Another account's spent nonce is not this account's to delete.
    assert [row.state_hash for row in theirs] == [calendar_sync._hash_state("other-spent")]


@pytest.mark.asyncio
async def test_provider_events_tolerates_a_fixed_offset_timezone():
    """``events`` must not explode on a tzinfo that is not an IANA zone."""
    captured = {}

    class Provider(GoogleCalendarProvider):
        async def fetch_changes(self, *, credential_ref, timezone_name, sync_cursor, since=None, until=None):
            captured["timezone_name"] = timezone_name
            return [], "cursor", True

    provider = Provider(InMemoryCredentialStore({"memory:1": "refresh"}))
    fixed_offset = timezone(timedelta(hours=5, minutes=30))
    await provider.events(
        since=datetime(2026, 8, 10, tzinfo=fixed_offset),
        until=datetime(2026, 8, 20, tzinfo=fixed_offset),
        credential_ref="memory:1",
    )
    assert captured["timezone_name"] == "UTC"
    ZoneInfo(captured["timezone_name"])

    await provider.events(
        since=datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Kolkata")),
        until=datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Kolkata")),
        credential_ref="memory:1",
    )
    assert captured["timezone_name"] == "Asia/Kolkata"
