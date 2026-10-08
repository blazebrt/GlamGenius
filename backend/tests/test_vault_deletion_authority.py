"""F14: exact Vault deletion SQL and retained disconnect authority.

The PostgreSQL fixture is a disposable local SQL-contract table, NOT a Vault
extension or encryption smoke test. No hosted database is accessed here.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from app.domains.planning import calendar_sync
from app.domains.planning.credentials import SupabaseVaultCredentialStore
from app.domains.planning.models import CalendarEvent, ExternalIntegration
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError


class _Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _RecordingSession:
    def __init__(self):
        self.calls = []
        self.nested_events = []

    @asynccontextmanager
    async def begin_nested(self):
        self.nested_events.append("savepoint")
        try:
            yield
        except Exception:
            self.nested_events.append("rollback")
            raise
        else:
            self.nested_events.append("release")

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Rows()


@pytest.mark.asyncio
async def test_vault_delete_uses_only_a_bound_exact_uuid_predicate():
    session = _RecordingSession()
    identifier = str(uuid4())
    store = SupabaseVaultCredentialStore(session)

    assert await store.delete(f"supabase-vault:{identifier}") is None
    assert session.calls == [(
        "DELETE FROM vault.secrets WHERE id = CAST(:id AS uuid)",
        {"id": identifier},
    )]
    assert session.nested_events == ["savepoint", "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", ["", "memory:1", "vault:1", str(uuid4())])
async def test_vault_delete_rejects_invalid_opaque_prefix_before_sql(reference):
    session = _RecordingSession()
    with pytest.raises(ValueError, match="unsupported credential reference"):
        await SupabaseVaultCredentialStore(session).delete(reference)
    assert session.calls == []
    assert session.nested_events == []


@pytest_asyncio.fixture
async def vault_sql_contract():
    """Create synthetic rows in a local test DB, then remove only our fixture.

    Refuse to shadow a real installation. These tests must not touch existing
    Vault data, even if somebody mistakenly points the test DB at such a DB.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        url = session.bind.url
        assert url.host in {"localhost", "127.0.0.1", "::1"}
        assert url.database.endswith("_test"), "requires a disposable test database"
        existing = (await session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'vault')"
        ))).scalar_one()
        assert not existing, "requires a disposable test database without Vault"
        await session.execute(text("CREATE SCHEMA vault"))
        await session.execute(text("CREATE TABLE vault.secrets (id uuid PRIMARY KEY)"))
        target, unrelated = uuid4(), uuid4()
        await session.execute(
            text("INSERT INTO vault.secrets (id) VALUES (:target), (:unrelated)"),
            {"target": target, "unrelated": unrelated},
        )
        await session.commit()
        try:
            yield session, target, unrelated
        finally:
            await session.rollback()
            await session.execute(text("DROP TABLE IF EXISTS vault.synthetic_reference"))
            await session.execute(text("DROP TABLE vault.secrets"))
            await session.execute(text("DROP SCHEMA vault"))
            await session.commit()


@asynccontextmanager
async def _record_sql(session):
    """Observe real statements/errors without replacing database execution."""
    await session.connection()
    engine = session.bind.sync_engine
    trace = SimpleNamespace(statements=[], failures=[])
    original_execute = session.execute

    def before_cursor_execute(_connection, _cursor, statement, _parameters, _context, _many):
        trace.statements.append(statement)

    async def execute(statement, *args, **kwargs):
        try:
            return await original_execute(statement, *args, **kwargs)
        except DBAPIError as exc:
            trace.failures.append((str(statement), exc.orig.sqlstate))
            raise

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    session.execute = execute
    try:
        yield trace
    finally:
        session.execute = original_execute
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


async def _seed_integration(account_id, reference):
    async with get_sessionmaker()() as seed:
        integration = ExternalIntegration(
            account_id=account_id, kind="calendar", provider="google",
            status="connected", credential_ref=reference, sync_cursor="synthetic-cursor",
        )
        seed.add(integration)
        await seed.flush()
        seed.add(CalendarEvent(
            account_id=account_id, integration_id=integration.id,
            external_id="synthetic-import", dedup_key="google:synthetic-import",
            title="Synthetic local event", starts_at=datetime.now(UTC),
            provider="google", source="google_calendar", status="active",
        ))
        await seed.commit()


async def _block_delete(session, target):
    await session.execute(text(
        "CREATE TABLE vault.synthetic_reference (secret_id uuid REFERENCES vault.secrets(id))"
    ))
    await session.execute(text("INSERT INTO vault.synthetic_reference VALUES (:id)"), {"id": target})
    await session.commit()


def _provider(session, trace, reference):
    async def revoked(actual_reference):
        assert actual_reference == reference
        # Local pending/event UPDATEs were sent before external revocation;
        # no SAVEPOINT has been entered around them or the remote operation.
        assert any("UPDATE external_integrations" in sql for sql in trace.statements)
        assert any("UPDATE calendar_events" in sql for sql in trace.statements)
        assert not any(sql.startswith("SAVEPOINT") for sql in trace.statements)
        return True

    store = SupabaseVaultCredentialStore(session)
    assert store.session is session
    return SimpleNamespace(credential_store=store, revoke=revoked)


@pytest.mark.asyncio
async def test_postgres_exact_delete_preserves_other_rows_and_replays_harmlessly(vault_sql_contract):
    session, target, unrelated = vault_sql_contract
    store = SupabaseVaultCredentialStore(session)

    await store.delete(f"supabase-vault:{target}")
    assert (await session.execute(text("SELECT id FROM vault.secrets"))).scalars().all() == [unrelated]
    await store.delete(f"supabase-vault:{target}")
    await store.delete(f"supabase-vault:{uuid4()}")
    assert (await session.execute(text("SELECT id FROM vault.secrets"))).scalars().all() == [unrelated]


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["", "not-a-uuid", "' OR TRUE --", "00000000-0000-0000-0000-000000000000; DELETE FROM vault.secrets"])
async def test_postgres_malformed_uuid_cannot_become_an_unscoped_delete(vault_sql_contract, identifier):
    session, target, unrelated = vault_sql_contract
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await SupabaseVaultCredentialStore(session).delete(f"supabase-vault:{identifier}")
    remaining = set((await session.execute(text("SELECT id FROM vault.secrets"))).scalars().all())
    assert remaining == {target, unrelated}


@pytest.mark.asyncio
async def test_postgres_delete_error_preserves_committable_pending_disconnect(
    db_clean, registered_supabase_user, vault_sql_contract,
):
    """A real SQL error must not roll back the earlier local revocation.

    A LOCAL synthetic FK blocks the target delete; no actual Vault extension,
    secret or provider is involved. Query the caller's transaction after the
    failure, rather than mistaking an in-memory pending flag for durable SQL.
    """
    _, account_id = await registered_supabase_user()
    session, target, _ = vault_sql_contract
    reference = f"supabase-vault:{target}"
    factory = get_sessionmaker()
    await _seed_integration(account_id, reference)
    await _block_delete(session, target)

    async with _record_sql(session) as trace:
        outer_transaction = session.sync_session.get_transaction()
        provider = _provider(session, trace, reference)
        result = await calendar_sync.disconnect_google_calendar(session, account_id, provider=provider)
        assert result["status"] == "revocation_pending"
        assert result["revoked"] is False
        assert trace.failures == [("DELETE FROM vault.secrets WHERE id = CAST(:id AS uuid)", "23503")]
        # This was the retained failing assertion without the store savepoint.
        status, stored_reference, revoked_at = (await session.execute(select(
            ExternalIntegration.status, ExternalIntegration.credential_ref, ExternalIntegration.revoked_at,
        ).where(ExternalIntegration.account_id == account_id))).one()
        assert status == "revocation_pending"
        assert stored_reference == reference
        assert revoked_at is None
        assert any(sql.startswith("ROLLBACK TO SAVEPOINT") for sql in trace.statements)
        assert session.sync_session.get_transaction() is outer_transaction
        await session.commit()

    async with factory() as observer:
        integration = (await observer.execute(select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
        ))).scalar_one()
        assert integration.status == integration.last_error == "revocation_pending"
        assert integration.credential_ref == reference
        assert integration.revoked_at is None
        assert (await observer.execute(select(CalendarEvent.status).where(
            CalendarEvent.account_id == account_id,
        ))).scalar_one() == "revoked"
        assert (await observer.execute(text("SELECT id FROM vault.secrets WHERE id = :id"),
                                       {"id": target})).scalar_one() == target


@pytest.mark.asyncio
@pytest.mark.parametrize("already_absent", [False, True], ids=["present", "already-absent"])
async def test_postgres_successful_cleanup_commits_final_revocation(
    db_clean, registered_supabase_user, vault_sql_contract, already_absent,
):
    _, account_id = await registered_supabase_user()
    session, target, unrelated = vault_sql_contract
    reference = f"supabase-vault:{target}"
    await _seed_integration(account_id, reference)
    if already_absent:
        await session.execute(text("DELETE FROM vault.secrets WHERE id = :id"), {"id": target})
        await session.commit()
    async with _record_sql(session) as trace:
        outer_transaction = session.sync_session.get_transaction()
        result = await calendar_sync.disconnect_google_calendar(
            session, account_id, provider=_provider(session, trace, reference),
        )
        assert result["status"] == "revoked"
        assert result["revoked"] is True
        assert trace.failures == []
        assert any(sql.startswith("SAVEPOINT") for sql in trace.statements)
        assert any(sql.startswith("RELEASE SAVEPOINT") for sql in trace.statements)
        assert not any(sql.startswith("ROLLBACK") for sql in trace.statements)
        assert session.sync_session.get_transaction() is outer_transaction
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        await session.commit()
    async with get_sessionmaker()() as observer:
        integration = (await observer.execute(select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
        ))).scalar_one()
        assert integration.status == "revoked"
        assert integration.credential_ref is None
        assert integration.revoked_at is not None
        assert integration.last_error is integration.sync_cursor is None
        assert (await observer.execute(select(CalendarEvent.status).where(
            CalendarEvent.account_id == account_id,
        ))).scalar_one() == "revoked"
        assert (await observer.execute(text("SELECT id FROM vault.secrets"))).scalars().all() == [unrelated]


@pytest.mark.asyncio
async def test_postgres_vault_error_leaves_privacy_deletion_retryable(
    db_clean, registered_supabase_user, vault_sql_contract, monkeypatch,
):
    from app.domains.identity.models import Account
    from app.domains.privacy import deletion_service
    from app.domains.privacy.models import STATE_COMPLETE, STATE_FAILED_RETRYABLE

    _, account_id = await registered_supabase_user()
    session, target, unrelated = vault_sql_contract
    reference = f"supabase-vault:{target}"
    await _seed_integration(account_id, reference)
    await _block_delete(session, target)
    await deletion_service.request_deletion(session, account_id)
    await session.commit()
    auth_deletions = []
    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: SimpleNamespace(
        auth=SimpleNamespace(admin=SimpleNamespace(delete_user=auth_deletions.append)),
    ))

    async with _record_sql(session) as trace:
        monkeypatch.setattr(calendar_sync, "credential_store", lambda actual_session: (
            SupabaseVaultCredentialStore(actual_session)
        ))

        def provider(store):
            assert store.session is session
            return _provider(session, trace, reference)

        monkeypatch.setattr(calendar_sync, "GoogleCalendarProvider", provider)
        # Use the real claim/run/retry worker, including its existing claim commit.
        assert await deletion_service.process_once(session) is True
        assert trace.failures == [("DELETE FROM vault.secrets WHERE id = CAST(:id AS uuid)", "23503")]
        assert any(sql.startswith("ROLLBACK TO SAVEPOINT") for sql in trace.statements)
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        await session.commit()

    async with get_sessionmaker()() as observer:
        job = await deletion_service.get_job(observer, account_id)
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.last_error_stage == "integrations_deleting"
        assert job.last_error_code == "unexpected"
        assert job.next_retry_at is not None
        assert job.completed_at is None
        assert auth_deletions == []
        assert (await observer.execute(select(Account.id).where(Account.id == account_id))).scalar_one() == account_id
        integration = (await observer.execute(select(ExternalIntegration).where(
            ExternalIntegration.account_id == account_id,
        ))).scalar_one()
        assert integration.status == "revocation_pending"
        assert integration.credential_ref == reference
        assert integration.revoked_at is None
        assert (await observer.execute(select(CalendarEvent.status).where(
            CalendarEvent.account_id == account_id,
        ))).scalar_one() == "revoked"
        assert set((await observer.execute(text("SELECT id FROM vault.secrets"))).scalars().all()) == {target, unrelated}

        # Removing only the synthetic FK obstruction lets the normal retry finish.
        await observer.execute(text("DELETE FROM vault.synthetic_reference WHERE secret_id = :id"), {"id": target})
        job.next_retry_at = None
        await observer.commit()
    async with get_sessionmaker()() as retry:
        async def revoked(_reference):
            return True

        monkeypatch.setattr(calendar_sync, "GoogleCalendarProvider", lambda store: SimpleNamespace(
            credential_store=store, revoke=revoked,
        ))
        await deletion_service.drain_all(retry)
        await retry.commit()
    async with get_sessionmaker()() as observer:
        job = await deletion_service.get_job(observer, account_id)
        assert job.state == STATE_COMPLETE
        assert job.completed_at is not None
        assert auth_deletions == [str(account_id)]
        assert (await observer.execute(select(Account.id).where(Account.id == account_id))).scalar_one_or_none() is None
        assert (await observer.execute(text("SELECT id FROM vault.secrets"))).scalars().all() == [unrelated]


class _DisconnectSession(_RecordingSession):
    def __init__(self, *, fail_delete=False):
        super().__init__()
        self.integration = SimpleNamespace(
            id=uuid4(), status="connected", credential_ref=f"supabase-vault:{uuid4()}",
            revoked_at=None, sync_cursor="synthetic-cursor", last_error=None,
        )
        self.event = SimpleNamespace(status="active")
        self.fail_delete = fail_delete
        self.delete_entered = asyncio.Event()
        self.delete_release = asyncio.Event()
        self.flushes = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if sql.startswith("DELETE"):
            assert sql == "DELETE FROM vault.secrets WHERE id = CAST(:id AS uuid)"
            assert self.integration.status == "revocation_pending"
            assert self.event.status == "revoked"
            assert self.integration.credential_ref == f"supabase-vault:{params['id']}"
            self.delete_entered.set()
            if self.fail_delete:
                raise RuntimeError("synthetic Vault adapter failure")
            await self.delete_release.wait()
            return _Rows()
        if "external_integrations" in sql:
            assert "FOR UPDATE" in sql
            return _Rows([self.integration])
        assert "calendar_events" in sql
        return _Rows([self.event])

    async def flush(self):
        self.flushes.append((self.integration.status, self.event.status, self.integration.credential_ref))


class _Provider:
    def __init__(self, session):
        self.session = session
        self.credential_store = SupabaseVaultCredentialStore(session)
        self.revocations = []

    async def revoke(self, reference):
        assert self.session.flushes[0] == ("revocation_pending", "revoked", reference)
        self.revocations.append(reference)
        return True


@pytest.mark.asyncio
async def test_vault_delete_failure_keeps_disconnect_pending_and_reference_retryable():
    session = _DisconnectSession(fail_delete=True)
    reference = session.integration.credential_ref
    provider = _Provider(session)

    result = await calendar_sync.disconnect_google_calendar(session, uuid4(), provider=provider)

    assert provider.revocations == [reference]
    assert session.delete_entered.is_set()
    assert result["status"] == session.integration.status == "revocation_pending"
    assert result["revoked"] is False
    assert session.integration.credential_ref == reference
    assert session.integration.revoked_at is None
    assert session.event.status == "revoked"


@pytest.mark.asyncio
async def test_disconnect_clears_reference_only_after_vault_deletion_returns():
    session = _DisconnectSession()
    reference = session.integration.credential_ref
    provider = _Provider(session)
    task = asyncio.create_task(calendar_sync.disconnect_google_calendar(session, uuid4(), provider=provider))
    try:
        await asyncio.wait_for(session.delete_entered.wait(), timeout=5)
        assert not task.done()
        assert provider.revocations == [reference]
        assert session.integration.status == "revocation_pending"
        assert session.integration.credential_ref == reference
        assert session.event.status == "revoked"
        session.delete_release.set()
        result = await asyncio.wait_for(task, timeout=5)
        assert result["status"] == session.integration.status == "revoked"
        assert result["revoked"] is True
        assert session.integration.credential_ref is None
        assert session.integration.sync_cursor is None
        assert session.integration.revoked_at is not None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
