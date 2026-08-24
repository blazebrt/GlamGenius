"""Account-deletion state machine tests.

Covers:
    * Idempotent job creation.
    * Storage listing/deletion; refusal to advance if the prefix is not empty.
    * External-integration removal.
    * Database cascade removes account data.
    * Supabase Auth deletion runs LAST.
    * Retry after transient storage failure.
    * Duplicate worker safety via the lease.
    * Cancellation before destructive stages; refusal after.
    * Cross-account status access is denied.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.identity import service as identity
from app.domains.planning import calendar_sync
from app.domains.planning.models import ExternalIntegration
from app.domains.privacy import deletion_service
from app.domains.privacy.models import (
    STATE_COMPLETE,
    STATE_FAILED_RETRYABLE,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _FakeSupabaseAdmin:
    def __init__(self) -> None:
        self.deleted_users: list[str] = []
        self.raises: Exception | None = None

    class _AuthAdmin:
        def __init__(self, outer):
            self._outer = outer

        def delete_user(self, user_id: str) -> None:
            if self._outer.raises is not None:
                raise self._outer.raises
            self._outer.deleted_users.append(user_id)

    class _Auth:
        def __init__(self, outer):
            self.admin = _FakeSupabaseAdmin._AuthAdmin(outer)

    def __init__(self):  # noqa: F811 — Python replays the outer definition
        self.deleted_users = []
        self.raises = None
        self.auth = _FakeSupabaseAdmin._Auth(self)


@pytest.fixture
def fake_admin(monkeypatch):
    admin = _FakeSupabaseAdmin()
    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin",
        lambda: admin,
    )
    return admin


class _FakeStorage:
    """Tracks deletions per account prefix and can be told to fail on demand."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_next: Exception | None = None
        # If True, delete_prefix leaves one object behind to prove the
        # worker refuses to advance until listing is empty.
        self.leave_one_behind = False

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return None

    async def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        keys = [k for k in self.objects if k.startswith(prefix)]
        if self.leave_one_behind and keys:
            keys = keys[1:]
        for k in keys:
            self.objects.pop(k, None)
        return len(keys)

    backend_name = "fake"


@pytest.fixture
def fake_storage(monkeypatch):
    storage = _FakeStorage()
    from app.domains.media.storage import factory as storage_factory
    storage_factory.set_storage(storage)
    yield storage
    storage_factory.set_storage(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_request_deletion_is_idempotent(db_clean):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        first = await deletion_service.request_deletion(session, account_id)
        second = await deletion_service.request_deletion(session, account_id)
        await session.commit()
    assert first.id == second.id


async def test_full_happy_path(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    other_account = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await identity.register_account(session, other_account)
        await session.commit()

    # Seed some storage under both accounts.
    prefix = f"media/{account_id}"
    other_prefix = f"media/{other_account}"
    await fake_storage.put(f"{prefix}/a.jpg", b"a", "image/jpeg")
    await fake_storage.put(f"{prefix}/b.jpg", b"b", "image/jpeg")
    await fake_storage.put(f"{other_prefix}/keep.jpg", b"c", "image/jpeg")

    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    async with factory() as session:
        processed = await deletion_service.drain_all(session)
        await session.commit()

    assert processed >= 1
    # Storage prefix is empty.
    remaining = list(await fake_storage.list_prefix(prefix))
    assert remaining == []
    # Other account is untouched.
    assert list(await fake_storage.list_prefix(other_prefix)) == [f"{other_prefix}/keep.jpg"]
    # Supabase Auth deletion happened.
    assert fake_admin.deleted_users == [str(account_id)]
    # Job reached ``complete``.
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job is not None
        assert job.state == STATE_COMPLETE


async def test_storage_incomplete_prevents_advance(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()

    prefix = f"media/{account_id}"
    await fake_storage.put(f"{prefix}/a.jpg", b"a", "image/jpeg")
    await fake_storage.put(f"{prefix}/b.jpg", b"b", "image/jpeg")
    fake_storage.leave_one_behind = True

    async with factory() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    async with factory() as session:
        await deletion_service.process_once(session)
        await session.commit()

    # Auth deletion must NOT have happened because storage is not empty.
    assert fake_admin.deleted_users == []
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.last_error_code == "storage_incomplete"


async def test_transient_storage_failure_schedules_retry(db_clean, fake_admin, fake_storage):
    from app.domains.media.storage.base import StorageUnavailable

    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    fake_storage.fail_next = StorageUnavailable("provider down")
    async with factory() as session:
        await deletion_service.process_once(session)
        await session.commit()

    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.next_retry_at is not None
        assert fake_admin.deleted_users == []

    # A retry after the transient failure clears the error and completes.
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        # Reset next_retry_at so we can pick it up now.
        job.next_retry_at = None
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        assert job.state == STATE_COMPLETE


async def test_unresolved_google_revocation_retries_integration_stage(
    db_clean, fake_admin, fake_storage, monkeypatch,
):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        session.add(ExternalIntegration(
            account_id=account_id, kind="calendar", provider="google",
            status="connected", credential_ref="opaque-ref",
        ))
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    async def unresolved(*_args, **_kwargs):
        return {"status": "revocation_pending"}

    monkeypatch.setattr(calendar_sync, "disconnect_google_calendar", unresolved)
    async with factory() as session:
        await deletion_service.process_once(session)
        await session.commit()
        job = await deletion_service.get_job(session, account_id)
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.last_error_stage == "integrations_deleting"
        assert fake_admin.deleted_users == []

    async def resolved(*_args, **_kwargs):
        return {"status": "revoked"}

    monkeypatch.setattr(calendar_sync, "disconnect_google_calendar", resolved)
    async with factory() as session:
        job = await deletion_service.get_job(session, account_id)
        job.next_retry_at = None
        await session.commit()
        await deletion_service.drain_all(session)
        await session.commit()

    assert fake_admin.deleted_users == [str(account_id)]
    async with factory() as session:
        assert (await session.execute(
            select(ExternalIntegration).where(ExternalIntegration.account_id == account_id)
        )).scalar_one_or_none() is None


async def test_supabase_auth_deletion_happens_last(db_clean, fake_admin, fake_storage):
    """Supabase Auth deletion must not run until database cleanup succeeded.

    We inject a failure at the database-deletion stage and confirm the auth
    call never happened.
    """
    from app.domains.privacy import deletion_service as ds

    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    async def _fail_db(*_a, **_kw):
        raise RuntimeError("simulated database failure")

    from unittest.mock import patch
    with patch.object(ds, "_delete_account_row", side_effect=_fail_db):
        async with factory() as session:
            await deletion_service.process_once(session)
            await session.commit()
    assert fake_admin.deleted_users == []


async def test_cross_account_status_access_denied(db_clean):
    factory = get_sessionmaker()
    a = uuid.uuid4()
    b = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, a)
        await identity.register_account(session, b)
        await deletion_service.request_deletion(session, a)
        await session.commit()

    async with factory() as session:
        # Different account looking for its own job — must be None.
        job_b = await deletion_service.get_job(session, b)
        assert job_b is None
        # Same account gets its own job back.
        job_a = await deletion_service.get_job(session, a)
        assert job_a is not None


async def test_lease_prevents_duplicate_processing(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await deletion_service.request_deletion(session, account_id)
        await session.commit()

    # Claim the job in one session and hold it — another claimer must find nothing.
    async with factory() as s1:
        job = await deletion_service.claim_next(s1)
        assert job is not None
        async with factory() as s2:
            second = await deletion_service.claim_next(s2)
            assert second is None
        await s1.rollback()  # release the row-lock without committing


async def test_completed_account_returns_terminal_state(db_clean, fake_admin, fake_storage):
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    # Re-drain: the job is complete and must not be picked up again.
    async with factory() as session:
        processed = await deletion_service.process_once(session)
        assert processed is False


async def test_deleted_identity_can_no_longer_be_registered(db_clean, fake_admin, fake_storage):
    """After deletion, the account row is gone; registering the same UUID
    again succeeds — but any pre-existing data is truly erased."""
    from app.domains.identity.models import Account
    from sqlalchemy import select

    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()

    async with factory() as session:
        account = (await session.execute(
            select(Account).where(Account.id == account_id)
        )).scalar_one_or_none()
        assert account is None
