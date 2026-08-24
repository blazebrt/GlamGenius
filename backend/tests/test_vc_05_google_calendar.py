import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from app.api.v2 import integrations as integrations_api
from app.domains.planning import calendar_sync
from app.domains.planning.credentials import InMemoryCredentialStore, SupabaseVaultCredentialStore
from app.domains.planning.providers.google_calendar import (
    GoogleCalendarProvider,
    GoogleSyncTokenExpired,
    IncompleteGoogleSync,
    MalformedGoogleEvent,
    normalize_google_event,
)
from app.shared.errors.exceptions import ValidationFailedError
from sqlalchemy import text
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
    def __init__(self, *, read_error=False, delete_error=False):
        self.read_error = read_error
        self.delete_error = delete_error
        self.deleted = []

    async def read(self, _ref):
        if self.read_error:
            raise RuntimeError("vault unavailable")
        return "refresh-token"

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
async def test_revocation_accepts_only_success_or_documented_invalid_token():
    responses = [httpx.Response(400, json={"error": "other_error"}), httpx.Response(400, json={"error": "invalid_token"}), httpx.Response(200)]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    assert await provider.revoke("memory:1") is False
    assert await provider.revoke("memory:1") is True
    assert await provider.revoke("memory:1") is True


@pytest.mark.asyncio
async def test_malformed_changed_item_raises_before_cursor_is_returned():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"id": "event-1", "start": {}}], "nextSyncToken": "must-not-advance"})

    provider = GoogleCalendarProvider(InMemoryCredentialStore({"memory:1": "refresh"}), transport=httpx.MockTransport(handler))
    provider._access_token = "access-token"
    with pytest.raises(MalformedGoogleEvent) as error:
        await provider.fetch_changes(credential_ref="memory:1", timezone_name="UTC", sync_cursor="cursor-1")
    assert error.value.reason == "malformed_event"


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
