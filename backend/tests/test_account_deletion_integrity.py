"""Account-deletion integrity: once processing starts, deletion is irreversible.

Against PostgreSQL 16, the real deletion service, the scheduled worker cycle
and the real routes. Where row-lock ordering is the invariant, two real
sessions race on the job row; nothing here mocks ``SELECT … FOR UPDATE``.

* L — cancellation is decided under the job's row lock, from durable facts
* A — a deletion-requested account is registered but not active
* N — the notification worker gives a non-active account no new work
* F — storage is proved empty again before the database and Auth go
* S — a media write and the deletion request serialise on the account row
* P — the lock modes that serialisation relies on, checked against PostgreSQL
* D — no deadlock between request, upload, cancellation and the worker
* G — the notification worker's final lifecycle gate before the provider

Every pause below is an event the test sets. Every wait is on a condition the
database reports: ``pg_blocking_pids`` and the waiter's tuple lock. None of
them is a sleep chosen to be long enough.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from datetime import timedelta

import pytest
from app.domains.identity import service as identity_service
from app.domains.identity.models import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DELETION_REQUESTED,
    Account,
)
from app.domains.media import service as media_service
from app.domains.media.models import MediaAsset
from app.domains.media.storage.base import (
    StorageError,
    StorageMisconfigured,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
    account_prefix,
)
from app.domains.planning.models import NotificationDelivery
from app.domains.privacy import deletion_service
from app.domains.privacy.models import (
    STATE_AUTH_DELETING,
    STATE_COMPLETE,
    STATE_DATABASE_COMPLETE,
    STATE_DATABASE_DELETING,
    STATE_FAILED_RETRYABLE,
    STATE_FAILED_TERMINAL,
    STATE_INTEGRATIONS_COMPLETE,
    STATE_INTEGRATIONS_DELETING,
    STATE_REQUESTED,
    STATE_STORAGE_COMPLETE,
    STATE_STORAGE_DELETING,
    STATE_STORAGE_LISTING,
    AccountDeletionJob,
)
from app.shared.database.base import utcnow
from app.shared.database.sql import get_engine, get_sessionmaker
from app.shared.security import deps
from app.workers import account_deletion
from app.workers import notifications as notification_worker
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import DBAPIError

from tests.conftest import auth, png_bytes
from tests.test_notification_worker_operations import _at_local_hour, _CountingPush, _opt_in

CANCEL = "/api/v2/privacy/account-deletion/cancel"
STATUS = "/api/v2/privacy/account-deletion"
DELETE = "/api/v2/privacy/account"
INACTIVE = "ACCOUNT_INACTIVE"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class _Pause:
    """A point where one coroutine stops until the test lets it go."""

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        #: the backend of the session that paused, when the pause knows it
        self.pid: int | None = None
        self.value = None

    async def __call__(self) -> None:
        self.reached.set()
        await self.release.wait()

    async def wait_reached(self) -> None:
        # A ceiling on a broken build, not a pace: it returns the moment the
        # paused coroutine arrives.
        await asyncio.wait_for(self.reached.wait(), timeout=30)


class _Storage:
    """Object storage with a call log, scripted failures and stuck keys."""

    backend_name = "fake"

    def __init__(self, events: list[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.events = events if events is not None else []
        self.purges = 0
        self.lists = 0
        #: {("delete_prefix" | "list_prefix", nth call): exception}
        self.fail_on: dict[tuple[str, int], Exception] = {}
        #: keys a purge cannot remove (a listing still shows them)
        self.stuck: set[str] = set()
        self.puts: list[str] = []
        #: when set, put() stops mid-write until the test releases it
        self.put_pause: _Pause | None = None

    async def put(self, key, data, content_type):
        self.puts.append(key)
        if self.put_pause is not None:
            await self.put_pause()
        self.objects[key] = data

    async def get(self, key):
        return self.objects[key]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return None

    async def delete_prefix(self, prefix):
        self.purges += 1
        self.events.append("storage_purge")
        failure = self.fail_on.pop(("delete_prefix", self.purges), None)
        if failure is not None:
            raise failure
        keys = [k for k in self.objects if k.startswith(prefix) and k not in self.stuck]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)

    async def list_prefix(self, prefix):
        self.lists += 1
        failure = self.fail_on.pop(("list_prefix", self.lists), None)
        if failure is not None:
            raise failure
        return [k for k in self.objects if k.startswith(prefix)]


class _Admin:
    """Supabase admin client double; records the Auth deletion."""

    def __init__(self, events: list[str]) -> None:
        self.deleted: list[str] = []
        outer = self

        class _AuthAdmin:
            def delete_user(self, user_id: str) -> None:
                events.append("auth")
                outer.deleted.append(user_id)

        class _Auth:
            admin = _AuthAdmin()

        self.auth = _Auth()


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def storage(events):
    from app.domains.media.storage import factory as storage_factory

    double = _Storage(events)
    storage_factory.set_storage(double)
    yield double
    storage_factory.set_storage(None)


@pytest.fixture
def admin(monkeypatch, events):
    double = _Admin(events)
    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: double)
    return double


def _factory():
    return get_sessionmaker()


async def _requested(registered_supabase_user) -> tuple[str, uuid.UUID]:
    token, account_id = await registered_supabase_user()
    async with _factory()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    return token, account_id


async def _job(account_id) -> AccountDeletionJob | None:
    async with _factory()() as session:
        return await deletion_service.get_job(session, account_id)


async def _account(account_id) -> Account | None:
    async with _factory()() as session:
        return await session.get(Account, account_id)


async def _set_job(account_id, **values) -> None:
    async with _factory()() as session:
        await session.execute(
            update(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id).values(**values)
        )
        await session.commit()


async def _make_due(account_id) -> None:
    await _set_job(account_id, next_retry_at=None, lease_owner=None, lease_expires_at=None)


def _inactive(response) -> bool:
    return response.status_code == 403 and response.json()["detail"]["code"] == INACTIVE


# ---------------------------------------------------------------------------
# L — cancellation under the row lock, from durable facts
# ---------------------------------------------------------------------------
async def test_l1_cancel_that_wins_the_lock_cancels_and_the_worker_finds_nothing(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    token, account_id = await _requested(registered_supabase_user)
    factory = _factory()
    cancelling = factory()
    try:
        await deletion_service.cancel_deletion(cancelling, account_id)  # holds the row lock
        worker = factory()
        try:
            # SKIP LOCKED: the worker steps past the row it cannot lock, at once.
            claimed = await asyncio.wait_for(deletion_service.claim_next(worker), timeout=5)
            assert claimed is None
        finally:
            await worker.close()
        await cancelling.commit()
    finally:
        await cancelling.close()
    async with factory() as worker:
        assert await deletion_service.claim_next(worker) is None
    assert await _job(account_id) is None
    account = await _account(account_id)
    assert account.status == ACCOUNT_STATUS_ACTIVE and account.deletion_requested_at is None
    assert storage.purges == 0 and admin.deleted == []
    # The account is an ordinary active account again.
    assert (await app_client.get("/api/v2/me", headers=auth(token))).status_code == 200


async def test_l2_cancel_queues_behind_an_uncommitted_worker_claim_and_then_refuses(
    db_clean, registered_supabase_user,
):
    """The race itself: the worker holds the row mid-claim; cancellation must
    wait for it rather than read the pristine row it can still see."""
    _, account_id = await _requested(registered_supabase_user)
    factory = _factory()
    worker = factory()
    try:
        job = await deletion_service._claim_locked(worker)  # claim written, not committed
        assert job is not None and job.started_at is not None
        cancelling = factory()
        try:
            attempt = asyncio.create_task(deletion_service.cancel_deletion(cancelling, account_id))
            await asyncio.sleep(0.3)
            assert not attempt.done(), "cancellation must wait on the job row, not read around it"
            await worker.commit()
            with pytest.raises(deletion_service.DeletionNotCancellable):
                await asyncio.wait_for(attempt, timeout=10)
            await cancelling.rollback()
        finally:
            await cancelling.close()
    finally:
        await worker.close()
    job = await _job(account_id)
    assert job is not None and job.started_at is not None
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


async def test_l2_api_cancel_behind_a_committed_claim_returns_409(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    """Through the real route, while the worker holds the claimed row for its run."""
    token, account_id = await _requested(registered_supabase_user)
    factory = _factory()
    worker = factory()
    try:
        job = await deletion_service.claim_next(worker)  # claim committed, row re-locked
        assert job is not None
        attempt = asyncio.create_task(app_client.post(CANCEL, headers=auth(token)))
        await asyncio.sleep(0.3)
        assert not attempt.done(), "the route must serialise on the job row"
        await worker.commit()
        response = await asyncio.wait_for(attempt, timeout=10)
    finally:
        await worker.close()
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "deletion_in_progress"
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert (await _job(account_id)) is not None


async def test_l3_a_retryable_failure_after_start_is_never_cancellable(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    token, account_id = await _requested(registered_supabase_user)
    storage.fail_on[("delete_prefix", 1)] = StorageUnavailable("provider down")
    summary = await account_deletion.run_cycle()
    assert summary.processed and summary.error_code == "storage_unavailable"
    job = await _job(account_id)
    assert job.state == STATE_FAILED_RETRYABLE
    assert job.started_at is not None and job.lease_owner is None and job.lease_expires_at is None

    response = await app_client.post(CANCEL, headers=auth(token))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "deletion_in_progress"
    # Nor after the retry timer has run out and nobody holds a lease.
    await _set_job(account_id, next_retry_at=utcnow() - timedelta(hours=1))
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409
    account = await _account(account_id)
    assert account.status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert (await _job(account_id)).started_at == job.started_at, "started_at is never cleared"
    assert admin.deleted == []


async def test_l3_a_terminal_failure_after_start_is_never_cancellable(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    token, account_id = await _requested(registered_supabase_user)
    storage.fail_on[("delete_prefix", 1)] = StorageMisconfigured("bucket gone")
    await account_deletion.run_cycle()
    assert (await _job(account_id)).state == STATE_FAILED_TERMINAL
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


@pytest.mark.parametrize("durable", [
    {"started_at": True},
    {"lease": True},
    {"started_at": True, "lease": True},
    {"started_at": True, "lease": True, "expired": True},
])
async def test_l3_requested_but_touched_is_not_cancellable(
    app_client, db_clean, registered_supabase_user, durable,
):
    """``requested`` alone is not enough: a start or any lease, even an
    expired one, means a worker has been here."""
    token, account_id = await _requested(registered_supabase_user)
    now = utcnow()
    values: dict = {"state": STATE_REQUESTED}
    if durable.get("started_at"):
        values["started_at"] = now - timedelta(minutes=5)
    if durable.get("lease"):
        values["lease_owner"] = "some-worker"
        values["lease_expires_at"] = now - timedelta(minutes=4) if durable.get("expired") else now + timedelta(minutes=1)
    await _set_job(account_id, **values)
    response = await app_client.post(CANCEL, headers=auth(token))
    assert response.status_code == 409, response.text
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert await _job(account_id) is not None


async def test_l3_a_crash_after_the_first_destructive_stage_leaves_it_irreversible(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    """The worker dies after purging storage and before it records anything
    else. The claim was already committed, so the job is not pristine."""
    token, account_id = await _requested(registered_supabase_user)
    storage.objects[f"{account_prefix(account_id)}photo.jpg"] = b"x"
    factory = _factory()
    worker = factory()
    try:
        job = await deletion_service.claim_next(worker)
        assert job is not None
        await media_service.purge_account_storage(account_id)
        # The process dies here: the open transaction is lost.
        await worker.rollback()
    finally:
        await worker.close()
    assert not storage.objects, "the irreversible part has happened"
    job = await _job(account_id)
    assert job.state == STATE_REQUESTED and job.started_at is not None and job.lease_owner is not None
    response = await app_client.post(CANCEL, headers=auth(token))
    assert response.status_code == 409, response.text
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


async def test_l3_a_poisoned_database_stage_cannot_roll_the_claim_back(
    app_client, db_clean, registered_supabase_user, storage, admin, monkeypatch,
):
    """A database error that aborts the run's transaction after storage and
    integrations are gone: the cycle's commit fails and everything the run
    did is rolled back — except the claim, which was committed first."""
    token, account_id = await _requested(registered_supabase_user)

    async def _poison(session, account_id):
        await session.execute(text("SELECT no_such_column FROM accounts"))

    monkeypatch.setattr(deletion_service, "_delete_account_row", _poison)
    summary = await account_deletion.run_cycle()
    assert summary.ok is False
    job = await _job(account_id)
    assert job.started_at is not None, "the claim survived the failed run"
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409
    assert await _account(account_id) is not None
    assert admin.deleted == []


async def test_l_no_api_path_revives_a_started_deletion(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    token, account_id = await _requested(registered_supabase_user)
    storage.fail_on[("delete_prefix", 1)] = StorageTimeout("slow")
    await account_deletion.run_cycle()
    before = await _job(account_id)
    assert before.state == STATE_FAILED_RETRYABLE
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409
    again = await app_client.delete(DELETE, headers=auth(token))
    assert again.status_code == 202 and again.json()["state"] == STATE_FAILED_RETRYABLE
    registered = await app_client.post("/api/v2/access/register", headers=auth(token), json={})
    assert registered.status_code == 200 and registered.json()["invite_redeemed"] is False
    assert registered.json()["account"]["status"] == ACCOUNT_STATUS_DELETION_REQUESTED
    assert _inactive(await app_client.get("/api/v2/me", headers=auth(token)))
    after = await _job(account_id)
    assert (after.id, after.started_at) == (before.id, before.started_at)
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


def test_l_cancellability_is_exactly_the_pristine_row():
    now = utcnow()
    states = [
        STATE_REQUESTED, STATE_STORAGE_LISTING, STATE_STORAGE_DELETING, STATE_STORAGE_COMPLETE,
        STATE_INTEGRATIONS_DELETING, STATE_INTEGRATIONS_COMPLETE, STATE_DATABASE_DELETING,
        STATE_DATABASE_COMPLETE, STATE_AUTH_DELETING, STATE_COMPLETE, STATE_FAILED_RETRYABLE,
        STATE_FAILED_TERMINAL,
    ]
    for state in states:
        for started in (None, now):
            for owner in (None, "w"):
                for expires in (None, now):
                    job = AccountDeletionJob(
                        account_id=uuid.uuid4(), state=state, requested_at=now,
                        started_at=started, lease_owner=owner, lease_expires_at=expires,
                    )
                    pristine = state == STATE_REQUESTED and (started, owner, expires) == (None, None, None)
                    assert job.can_cancel() is pristine, (state, started, owner, expires)


def test_l_the_route_does_not_decide_cancellation_itself():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "api" / "v2" / "privacy.py").read_text()
    body = source[source.index("async def cancel_deletion"):]
    assert "deletion_service.cancel_deletion(" in body
    for forbidden in ("DESTRUCTIVE_STATES", "get_job(", "job.state", "delete(AccountDeletionJob"):
        assert forbidden not in body, forbidden


# ---------------------------------------------------------------------------
# A — registered is not active
# ---------------------------------------------------------------------------
async def test_a4_a_deletion_requested_account_cannot_upload_media(
    app_client, db_clean, registered_supabase_user, storage,
):
    token, account_id = await registered_supabase_user()
    ok = await app_client.post(
        "/api/v2/media/upload", headers=auth(token), files={"file": ("a.png", png_bytes(), "image/png")},
    )
    assert ok.status_code == 200, ok.text
    written = list(storage.puts)
    assert (await app_client.delete(DELETE, headers=auth(token))).status_code == 202

    refused = await app_client.post(
        "/api/v2/media/upload", headers=auth(token), files={"file": ("b.png", png_bytes(), "image/png")},
    )
    assert _inactive(refused), refused.text
    assert refused.json()["detail"]["retryable"] is False
    assert storage.puts == written, "the storage adapter must not be written"
    async with _factory()() as session:
        assert await session.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.account_id == account_id)
        ) == 1


async def test_a5_an_unrelated_mutation_is_refused_by_the_same_dependency(
    app_client, db_clean, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    body = {
        "category": "hair", "display_name": "Gentle Shampoo", "subcategory": "shampoo",
        "details": {"product_type": "shampoo", "routine_position": "cleanse"},
    }
    created = await app_client.post("/api/v2/inventory/items", headers=auth(token), json=body)
    assert created.status_code in (200, 201), created.text
    before = (await app_client.get("/api/v2/inventory/items", headers=auth(token))).json()

    assert (await app_client.delete(DELETE, headers=auth(token))).status_code == 202
    refused = await app_client.post("/api/v2/inventory/items", headers=auth(token), json=body)
    assert _inactive(refused), refused.text
    # Reads are refused too, and export is not a loophole.
    for path in ("/api/v2/inventory/items", "/api/v2/me", "/api/v2/privacy/export"):
        assert _inactive(await app_client.get(path, headers=auth(token))), path

    await app_client.post(CANCEL, headers=auth(token))
    after = (await app_client.get("/api/v2/inventory/items", headers=auth(token))).json()
    assert after == before, "no item was created while the account was inactive"


async def test_a6_the_owner_can_still_read_their_deletion_status(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    other, _ = await registered_supabase_user()
    await app_client.delete(DELETE, headers=auth(token))
    response = await app_client.get(STATUS, headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == str(account_id)
    assert response.json()["state"] == STATE_REQUESTED
    # Nobody else can read it: the route scopes by the caller.
    assert (await app_client.get(STATUS, headers=auth(other))).status_code == 404


async def test_a7_a_pristine_deletion_can_still_be_cancelled(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    await app_client.delete(DELETE, headers=auth(token))
    assert _inactive(await app_client.get("/api/v2/me", headers=auth(token)))
    response = await app_client.post(CANCEL, headers=auth(token))
    assert response.status_code == 200 and response.json() == {"status": "cancelled"}
    assert (await _account(account_id)).status == ACCOUNT_STATUS_ACTIVE
    assert await _job(account_id) is None
    assert (await app_client.get("/api/v2/me", headers=auth(token))).status_code == 200
    # Nothing left to cancel; a new request starts a new job.
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 404
    assert (await app_client.delete(DELETE, headers=auth(token))).status_code == 202
    assert (await _job(account_id)).state == STATE_REQUESTED


async def test_a8_repeating_the_deletion_request_is_idempotent(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    first = await app_client.delete(DELETE, headers=auth(token))
    second = await app_client.delete(DELETE, headers=auth(token))
    assert first.status_code == second.status_code == 202, second.text
    assert first.json() == second.json()
    async with _factory()() as session:
        assert await session.scalar(
            select(func.count()).select_from(AccountDeletionJob).where(AccountDeletionJob.account_id == account_id)
        ) == 1


async def test_a_an_unregistered_identity_is_still_registration_required(
    app_client, db_clean, fake_supabase_user,
):
    token, _ = fake_supabase_user(email="nobody@example.com")
    for method, path in (("get", "/api/v2/me"), ("get", STATUS), ("post", CANCEL), ("delete", DELETE)):
        response = await getattr(app_client, method)(path, headers=auth(token))
        assert response.status_code == 403, (path, response.text)
        assert response.json()["detail"]["code"] == "REGISTRATION_REQUIRED", path


async def test_a_optional_auth_refuses_rather_than_downgrading_to_anonymous(
    db_clean, registered_supabase_user, fake_supabase_user,
):
    active_token, active_id = await registered_supabase_user()
    inactive_token, _ = await _requested(registered_supabase_user)
    stranger, _ = fake_supabase_user(email="stranger@example.com")

    def bearer(token):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    async with _factory()() as session:
        assert await deps.get_optional_account(None, session) is None
        current = await deps.get_optional_account(bearer(active_token), session)
        assert current is not None and current.account_id == active_id
        with pytest.raises(HTTPException) as refused:
            await deps.get_optional_account(bearer(inactive_token), session)
        assert refused.value.status_code == 403 and refused.value.detail["code"] == INACTIVE
        with pytest.raises(HTTPException) as unregistered:
            await deps.get_optional_account(bearer(stranger), session)
        assert unregistered.value.detail["code"] == "REGISTRATION_REQUIRED"
        with pytest.raises(HTTPException) as invalid:
            await deps.get_optional_account(bearer("not-a-token"), session)
        assert invalid.value.status_code == 401


def test_a_registered_and_active_are_two_dependencies():
    assert deps.get_registered_account is not deps.get_current_account
    import inspect

    parameter = inspect.signature(deps.get_current_account).parameters["registered"]
    assert parameter.default.dependency is deps.get_registered_account


def _api_routes(router):
    """Every APIRoute under ``router``, through FastAPI's lazily included routers."""
    for route in router.routes:
        if isinstance(route, APIRoute):
            yield route
        elif hasattr(route, "original_router"):
            yield from _api_routes(route.original_router)


def test_a_only_the_deletion_lifecycle_accepts_a_non_active_account():
    from app.api.v2 import router as v2_router

    routes = list(_api_routes(v2_router))
    active_only = {
        (method, route.path)
        for route in routes
        for dependency in route.dependant.dependencies
        if dependency.call is deps.get_current_account
        for method in route.methods
    }
    # The walk is real: it reaches the product routes this test is about.
    assert ("POST", "/media/upload") in active_only
    assert ("POST", "/inventory/items") in active_only
    assert ("GET", "/privacy/export") in active_only
    assert len(routes) > 100
    lenient = {
        (method, route.path)
        for route in routes
        for dependency in route.dependant.dependencies
        if dependency.call is deps.get_registered_account
        for method in route.methods
    }
    assert lenient == {
        ("DELETE", "/privacy/account"),
        ("GET", "/privacy/account-deletion"),
        ("POST", "/privacy/account-deletion/cancel"),
    }


# ---------------------------------------------------------------------------
# N — no new proactive work for a non-active account
# ---------------------------------------------------------------------------
def _record_work(monkeypatch) -> list:
    seen: list = []
    original = notification_worker.context_stage.gather

    async def gather(session, *, account_id, plan_date):
        seen.append(account_id)
        return await original(session, account_id=account_id, plan_date=plan_date)

    monkeypatch.setattr(notification_worker.context_stage, "gather", gather)
    return seen


async def _deliveries(account_id) -> int:
    async with _factory()() as session:
        return int(await session.scalar(
            select(func.count()).select_from(NotificationDelivery).where(NotificationDelivery.account_id == account_id)
        ) or 0)


async def test_n9_the_cycle_skips_a_deletion_requested_account(
    db_clean, registered_supabase_user, monkeypatch,
):
    _, active_id = await registered_supabase_user()
    _, leaving_id = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(active_id, hour=9)
    await _opt_in(leaving_id, hour=9)
    async with _factory()() as session:
        await deletion_service.request_deletion(session, leaving_id)
        await session.commit()
    sender = _CountingPush()
    monkeypatch.setattr(notification_worker.push, "send", sender.send)
    seen = _record_work(monkeypatch)

    summary = notification_worker.RunSummary()
    await notification_worker.process_once(now=moment, summary=summary)
    assert summary.accounts_considered == 1
    assert set(seen) == {active_id} and leaving_id not in seen
    assert await _deliveries(leaving_id) == 0

    # The gate is in process_account itself, not only in the selection.
    async with _factory()() as session:
        preference = await notification_worker.notifications.preferences_for(session, leaving_id, "Asia/Kolkata")
        assert await notification_worker.process_account(session, preference, now=moment) == 0
    assert leaving_id not in seen
    assert await _deliveries(leaving_id) == 0


async def test_n10_a_manual_run_cannot_bypass_the_lifecycle(
    db_clean, registered_supabase_user, monkeypatch,
):
    _, leaving_id = await _requested(registered_supabase_user)
    moment = _at_local_hour(9)
    await _opt_in(leaving_id, hour=9)
    monkeypatch.setenv("NOTIFICATION_TEST_ACCOUNT_IDS", str(leaving_id))
    sender = _CountingPush()
    monkeypatch.setattr(notification_worker.push, "send", sender.send)
    seen = _record_work(monkeypatch)
    summary = await notification_worker.run_for_account(str(leaving_id), now=moment)
    assert summary.notifications_sent == 0 and summary.accounts_failed == 0
    assert seen == [] and sender.messages_sent == 0
    assert await _deliveries(leaving_id) == 0


def test_n_one_definition_of_active_serves_both_worker_paths():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "workers" / "notifications.py").read_text()
    process_account = source[source.index("async def process_account"):source.index("async def process_once")]
    process_once = source[source.index("async def process_once"):source.index("# Observability")]
    assert "_account_is_active(" in process_account
    assert process_account.index("_account_is_active(") < process_account.index("context_stage.gather")
    assert "_active_accounts()" in process_once
    assert source.count("ACCOUNT_STATUS_ACTIVE") == 2  # the import and the one definition


# ---------------------------------------------------------------------------
# F — the final storage barrier
# ---------------------------------------------------------------------------
def _late_object_after_integrations(monkeypatch, storage, account_id, events) -> str:
    """An in-flight write lands after the first purge, deterministically."""
    late_key = f"{account_prefix(account_id)}late-upload.jpg"
    original = deletion_service._remove_external_integrations

    async def integrations(session, account):
        await original(session, account)
        events.append("integrations")
        storage.objects[late_key] = b"late"

    monkeypatch.setattr(deletion_service, "_remove_external_integrations", integrations)
    return late_key


def _record_account_row(monkeypatch, events) -> None:
    original = deletion_service._delete_account_row

    async def delete_row(session, account_id):
        events.append("account_row")
        await original(session, account_id)

    monkeypatch.setattr(deletion_service, "_delete_account_row", delete_row)


async def test_f11_a_late_object_is_caught_by_the_final_barrier(
    db_clean, registered_supabase_user, storage, admin, events, monkeypatch,
):
    _, account_id = await _requested(registered_supabase_user)
    storage.objects[f"{account_prefix(account_id)}early.jpg"] = b"early"
    late_key = _late_object_after_integrations(monkeypatch, storage, account_id, events)
    _record_account_row(monkeypatch, events)

    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert late_key not in storage.objects and not storage.objects
    assert await _account(account_id) is None
    assert admin.deleted == [str(account_id)]
    # F13: the order, including the second proof and Auth last.
    assert events == ["storage_purge", "integrations", "storage_purge", "account_row", "auth"]


async def test_f14_an_object_the_barrier_cannot_remove_blocks_completion(
    app_client, db_clean, registered_supabase_user, storage, admin, events, monkeypatch,
):
    token, account_id = await _requested(registered_supabase_user)
    late_key = _late_object_after_integrations(monkeypatch, storage, account_id, events)
    storage.stuck.add(late_key)

    for _ in range(3):
        summary = await account_deletion.run_cycle()
        assert summary.error_code == "storage_incomplete"
        job = await _job(account_id)
        assert job.state == STATE_FAILED_RETRYABLE
        assert job.last_error_stage == STATE_DATABASE_DELETING
        account = await _account(account_id)
        assert account is not None and account.status == ACCOUNT_STATUS_DELETION_REQUESTED
        assert admin.deleted == [] and "auth" not in events
        assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409
        await _make_due(account_id)

    # Emptiness can be proved once the object can go; only then does it finish.
    storage.stuck.clear()
    summary = await account_deletion.run_cycle()
    assert summary.ok
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert not storage.objects and admin.deleted == [str(account_id)]
    assert events[-1] == "auth"


@pytest.mark.parametrize("operation", ["delete_prefix", "list_prefix"])
@pytest.mark.parametrize("failure", [
    StorageTimeout("timeout"),
    StorageUnavailable("unavailable"),
    StorageUnauthorized("unauthorized"),
    StorageError("error"),
])
async def test_f12_a_failing_final_proof_fails_closed(
    app_client, db_clean, registered_supabase_user, storage, admin, events, operation, failure,
):
    token, account_id = await _requested(registered_supabase_user)
    storage.fail_on[(operation, 2)] = failure  # the second call is the final barrier
    summary = await account_deletion.run_cycle()
    assert summary.ok is False
    job = await _job(account_id)
    assert job.state == STATE_FAILED_RETRYABLE
    assert job.last_error_stage == STATE_DATABASE_DELETING
    account = await _account(account_id)
    assert account is not None and account.status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert admin.deleted == [] and "auth" not in events
    assert (await app_client.post(CANCEL, headers=auth(token))).status_code == 409


async def test_f12_a_misconfigured_final_proof_stops_before_the_database(
    db_clean, registered_supabase_user, storage, admin, events,
):
    _, account_id = await _requested(registered_supabase_user)
    storage.fail_on[("delete_prefix", 2)] = StorageMisconfigured("bucket gone")
    await account_deletion.run_cycle()
    assert (await _job(account_id)).state == STATE_FAILED_TERMINAL
    assert await _account(account_id) is not None
    assert admin.deleted == []


def test_f_the_barrier_uses_the_media_authority_before_any_database_deletion():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "domains" / "privacy" / "deletion_service.py").read_text()
    stage = source[source.index("if job.state == STATE_DATABASE_DELETING:"):]
    stage = stage[:stage.index("job.state = STATE_DATABASE_COMPLETE")]
    barrier = stage.index("media_service.purge_account_storage(job.account_id)")
    for deletion in ("_delete_ai_outputs", "_delete_analytics_events", "_scrub_audit_events",
                     "_withdraw_scan_observations", "_delete_account_row"):
        assert barrier < stage.index(deletion), deletion
    # The early purge is kept.
    storage_stage = source[source.index("if job.state == STATE_STORAGE_DELETING:"):]
    assert "purge_account_storage" in storage_stage[:storage_stage.index("STATE_STORAGE_COMPLETE")]


# ---------------------------------------------------------------------------
# S — a media write and the deletion request serialise on the account row
# ---------------------------------------------------------------------------
UPLOAD = "/api/v2/media/upload"

#: For a session that is meant to wait for another transaction to commit. It
#: never fires in a healthy run; a cycle fails the test instead of hanging it.
PATIENT_LOCK_TIMEOUT = "20s"

#: For a statement that can only succeed if the lock it needs is free. A
#: conflicting lock is held for the whole statement, so this always fires when
#: they conflict; when they do not, there is nothing to wait for.
SHORT_LOCK_TIMEOUT = "300ms"


@pytest.fixture
async def race():
    """Pauses and tasks for one race, all let go and finished at teardown.

    A failing assertion must not leave a paused coroutine holding a row lock:
    the next test's truncate would wait on it forever.
    """
    class Race:
        def __init__(self) -> None:
            self.pauses: list[_Pause] = []
            self.tasks: list[asyncio.Task] = []

        def pause(self) -> _Pause:
            pause = _Pause()
            self.pauses.append(pause)
            return pause

        def spawn(self, coroutine) -> asyncio.Task:
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

    state = Race()
    yield state
    for pause in state.pauses:
        pause.release.set()
    for task in state.tasks:
        if not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30)
            except BaseException:  # noqa: BLE001 - teardown: finish, whatever it says
                task.cancel()
        elif not task.cancelled():
            task.exception()  # retrieved, so an expected failure is not reported as unhandled


async def _pid(session) -> int:
    return int(await session.scalar(text("SELECT pg_backend_pid()")))


async def _patient(session) -> None:
    await session.execute(text(f"SET LOCAL lock_timeout = '{PATIENT_LOCK_TIMEOUT}'"))


async def _row_lock_wait(pid: int) -> tuple[list[int], set[str]]:
    """Who ``pid`` is waiting for, and which table's row it is waiting on.

    A row-lock waiter takes a tuple lock that names the relation and then
    waits on the holder's transaction, so the holders come from
    ``pg_blocking_pids`` and the table from the tuple lock.
    """
    async with _factory()() as watcher:
        blockers = list(await watcher.scalar(
            text("SELECT pg_blocking_pids(:pid)"), {"pid": pid},
        ) or [])
        relations = set((await watcher.execute(text(
            "SELECT c.relname FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
            "WHERE l.pid = :pid AND l.locktype = 'tuple'"
        ), {"pid": pid})).scalars().all())
    return blockers, relations


async def _until_waiting_on_an_account(pid: int) -> list[int]:
    """Poll until ``pid`` waits for an accounts row lock; return the holders."""
    for _ in range(3000):
        blockers, relations = await _row_lock_wait(pid)
        if blockers and "accounts" in relations:
            return blockers
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never waited on an accounts row")


async def _until_something_waits_on(holder: int) -> int:
    """Poll until another backend waits on ``holder`` for an accounts row."""
    for _ in range(3000):
        async with _factory()() as watcher:
            waiters = list((await watcher.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))"
            ), {"holder": holder})).scalars().all())
        for waiter in waiters:
            if "accounts" in (await _row_lock_wait(waiter))[1]:
                return waiter
        await asyncio.sleep(0.01)
    raise AssertionError(f"nothing ever waited on backend {holder}")


async def _upload(app_client, token):
    return await app_client.post(
        UPLOAD, headers=auth(token), files={"file": ("a.png", png_bytes(), "image/png")},
    )


def _upload_pauses_after_authentication(monkeypatch, race) -> _Pause:
    """Stop the upload route in its handler: authenticated, bytes read, nothing stored.

    The handler runs only after ``get_current_account`` accepted the account
    as active, so reaching this pause is the proof that it authenticated as
    active.
    """
    from app.api.v2 import media as media_api

    pause = race.pause()
    original = media_api._read_capped

    async def read_then_pause(upload):
        data = await original(upload)
        await pause()
        return data

    monkeypatch.setattr(media_api, "_read_capped", read_then_pause)
    return pause


def _worker_pauses(monkeypatch, race, stage: str, *, after: bool = False) -> _Pause:
    """Stop the deletion worker inside its run transaction, at ``stage``."""
    pause = race.pause()
    original = getattr(deletion_service, stage)

    async def paused(session, account_id):
        pause.pid = await _pid(session)
        if after:
            await original(session, account_id)
            await pause()
            return None
        await pause()
        return await original(session, account_id)

    monkeypatch.setattr(deletion_service, stage, paused)
    return pause


def _under(storage, account_id) -> list[str]:
    prefix = account_prefix(account_id)
    return [key for key in storage.objects if key.startswith(prefix)]


async def _media_rows(account_id) -> int:
    async with _factory()() as session:
        return int(await session.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.account_id == account_id)
        ) or 0)


async def test_s_a_an_upload_holding_the_account_makes_the_deletion_request_wait(
    app_client, db_clean, registered_supabase_user, storage, admin, events, race,
):
    """A. Media wins. The request waits for the upload's transaction to end,
    and the ordinary purge then removes what it wrote."""
    token, account_id = await registered_supabase_user()
    storage.put_pause = race.pause()
    upload = race.spawn(_upload(app_client, token))
    await storage.put_pause.wait_reached()  # past the gate, mid-write

    requesting = _factory()()
    try:
        await _patient(requesting)
        pid = await _pid(requesting)
        request = race.spawn(deletion_service.request_deletion(requesting, account_id))
        await _until_waiting_on_an_account(pid)
        assert not request.done()
        assert (await _account(account_id)).status == ACCOUNT_STATUS_ACTIVE
        assert await _job(account_id) is None

        storage.put_pause.release.set()
        response = await asyncio.wait_for(upload, timeout=30)
        assert response.status_code == 200, response.text
        job = await asyncio.wait_for(request, timeout=30)
        await requesting.commit()
    finally:
        await requesting.close()

    assert job.state == STATE_REQUESTED and job.started_at is None
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert _under(storage, account_id) == storage.puts and len(storage.puts) == 1
    assert await _media_rows(account_id) == 1

    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert _under(storage, account_id) == []
    assert await _account(account_id) is None and await _media_rows(account_id) == 0
    assert admin.deleted == [str(account_id)]
    assert events == ["storage_purge", "storage_purge", "auth"]


async def test_s_b_a_deletion_holding_the_transition_refuses_an_authenticated_upload(
    app_client, db_clean, registered_supabase_user, storage, race, monkeypatch,
):
    """B. Deletion wins. The upload authenticated while the account was still
    active, reaches the boundary, waits, and is refused before any byte."""
    token, account_id = await registered_supabase_user()
    from app.api.v2 import media as media_api

    in_handler = asyncio.Event()
    original = media_api._read_capped

    async def read(upload):
        in_handler.set()
        return await original(upload)

    monkeypatch.setattr(media_api, "_read_capped", read)

    requesting = _factory()()
    try:
        holder = await _pid(requesting)
        # The transition is made and held: status changed, job inserted, not committed.
        await deletion_service.request_deletion(requesting, account_id)
        upload = race.spawn(_upload(app_client, token))
        await _until_something_waits_on(holder)
        # Authenticated against the committed row, which still says active.
        assert in_handler.is_set()
        assert not upload.done() and storage.puts == []
        await requesting.commit()
    finally:
        await requesting.close()

    response = await asyncio.wait_for(upload, timeout=30)
    assert _inactive(response), response.text
    assert storage.puts == [], "the storage adapter must not be called"
    assert _under(storage, account_id) == []
    assert await _media_rows(account_id) == 0
    assert (await _job(account_id)).state == STATE_REQUESTED
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


async def test_s_c_an_upload_in_flight_across_the_final_proof_cannot_orphan_an_object(
    app_client, db_clean, registered_supabase_user, storage, admin, events, race, monkeypatch,
):
    """C. The race the final barrier alone admitted, built step by step.

    The upload authenticates while the account is active. Deletion is then
    requested, and the worker runs both storage proofs. Only then does the
    upload try to write. Before the lifecycle gate it wrote the object after
    the last proof, the worker completed, and the object outlived the
    account, its row and its Auth identity.
    """
    token, account_id = await registered_supabase_user()
    in_handler = _upload_pauses_after_authentication(monkeypatch, race)
    upload = race.spawn(_upload(app_client, token))
    await in_handler.wait_reached()

    assert (await app_client.delete(DELETE, headers=auth(token))).status_code == 202

    after_final_proof = _worker_pauses(monkeypatch, race, "_delete_ai_outputs")
    worker = race.spawn(account_deletion.run_cycle())
    await after_final_proof.wait_reached()
    assert events == ["storage_purge", "storage_purge"], "both proofs are made"
    assert storage.puts == []

    in_handler.release.set()
    response = await asyncio.wait_for(upload, timeout=30)
    after_final_proof.release.set()
    summary = await asyncio.wait_for(worker, timeout=30)

    orphans = _under(storage, account_id)
    assert orphans == [], (
        f"an object written after the final storage proof survived a completed deletion: {orphans}"
    )
    assert storage.puts == []
    assert _inactive(response), response.text
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert await _account(account_id) is None
    assert admin.deleted == [str(account_id)]
    assert events == ["storage_purge", "storage_purge", "auth"]


async def test_s_the_ordinary_route_still_refuses_after_the_request(
    app_client, db_clean, registered_supabase_user, storage,
):
    """After the request has committed, the authentication dependency refuses
    first. The lifecycle gate is a second boundary, not a replacement."""
    token, account_id = await _requested(registered_supabase_user)
    refused = await _upload(app_client, token)
    assert _inactive(refused), refused.text
    assert storage.puts == [] and await _media_rows(account_id) == 0


def test_s_the_gate_is_the_last_step_before_the_bytes():
    upload = inspect.getsource(media_service.upload)
    awaits = re.findall(r"await ([\w.]+)\(", upload)
    assert awaits[:2] == ["identity_service.hold_account_active", "storage.put"], awaits
    request = inspect.getsource(deletion_service.request_deletion)
    assert re.findall(r"await ([\w.]+)\(", request)[0] == "identity_service.lock_account_lifecycle"
    # The request reads the job; it never locks it (see the D tests).
    assert "with_for_update" not in request


# ---------------------------------------------------------------------------
# P — the lock modes, checked against PostgreSQL rather than assumed
# ---------------------------------------------------------------------------
async def test_p_the_helpers_ask_postgres_for_the_intended_modes(db_clean, registered_supabase_user):
    _, account_id = await registered_supabase_user()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        async with _factory()() as session:
            assert await identity_service.hold_account_active(session, account_id) is True
            assert (await identity_service.lock_account_lifecycle(session, account_id)).id == account_id
            await session.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    gate, transition = (s for s in statements if "FROM accounts" in s)
    assert gate.endswith(" FOR SHARE"), gate
    assert transition.endswith(" FOR NO KEY UPDATE"), transition


_STATUS_UPDATE = "UPDATE accounts SET status = 'deletion_requested' WHERE id = :id"
_ACCOUNT_DELETE = "DELETE FROM accounts WHERE id = :id"


@pytest.mark.parametrize(("held", "statement", "conflicts"), [
    # The upload gate stops the lifecycle status change and the worker's DELETE.
    ("hold_account_active", _STATUS_UPDATE, True),
    ("hold_account_active", _ACCOUNT_DELETE, True),
    # The transition lock stops them too.
    ("lock_account_lifecycle", _STATUS_UPDATE, True),
    ("lock_account_lifecycle", _ACCOUNT_DELETE, True),
    # FOR KEY SHARE, the Step 11C decision-append lock, stops the DELETE but
    # lets an ordinary status UPDATE straight through. That is why the gate
    # is not built on it.
    ("lock_account_against_delete", _STATUS_UPDATE, False),
    ("lock_account_against_delete", _ACCOUNT_DELETE, True),
])
async def test_p_which_held_locks_stop_the_lifecycle_statements(
    db_clean, registered_supabase_user, held, statement, conflicts,
):
    """A plain SQL status UPDATE, not the application's helper: even a path
    that forgot the explicit transition lock is serialised by the gate."""
    _, account_id = await registered_supabase_user()
    holder, other = _factory()(), _factory()()
    try:
        await getattr(identity_service, held)(holder, account_id)
        await other.execute(text(f"SET LOCAL lock_timeout = '{SHORT_LOCK_TIMEOUT}'"))
        if conflicts:
            with pytest.raises(DBAPIError) as refused:
                await other.execute(text(statement), {"id": account_id})
            assert "lock timeout" in str(refused.value).lower()
        else:
            await other.execute(text(statement), {"id": account_id})
        await other.rollback()
    finally:
        await holder.rollback()
        await holder.close()
        await other.close()


@pytest.mark.parametrize(("held", "attempt", "conflicts"), [
    # A transition in progress keeps new uploads out until it ends.
    ("lock_account_lifecycle", "hold_account_active", True),
    ("lock_account_lifecycle", "lock_account_lifecycle", True),
    # Concurrent uploads share the account; decision appends are not blocked
    # by an upload or by a transition.
    ("hold_account_active", "hold_account_active", False),
    ("hold_account_active", "lock_account_against_delete", False),
    ("lock_account_lifecycle", "lock_account_against_delete", False),
])
async def test_p_the_helpers_against_each_other(
    db_clean, registered_supabase_user, held, attempt, conflicts,
):
    _, account_id = await registered_supabase_user()
    holder, other = _factory()(), _factory()()
    try:
        await getattr(identity_service, held)(holder, account_id)
        await other.execute(text(f"SET LOCAL lock_timeout = '{SHORT_LOCK_TIMEOUT}'"))
        if conflicts:
            with pytest.raises(DBAPIError) as refused:
                await getattr(identity_service, attempt)(other, account_id)
            assert "lock timeout" in str(refused.value).lower()
        else:
            assert await getattr(identity_service, attempt)(other, account_id)
        await other.rollback()
    finally:
        await holder.rollback()
        await holder.close()
        await other.close()


async def test_p_a_waiting_gate_reads_the_status_the_transition_committed(
    db_clean, registered_supabase_user, race,
):
    """READ COMMITTED re-reads the newest row version after a locking wait.
    The gate therefore answers from the committed transition, not from the
    snapshot it started with."""
    _, account_id = await registered_supabase_user()
    requesting, uploading = _factory()(), _factory()()
    try:
        await deletion_service.request_deletion(requesting, account_id)
        await _patient(uploading)
        pid = await _pid(uploading)
        gate = race.spawn(identity_service.hold_account_active(uploading, account_id))
        await _until_waiting_on_an_account(pid)
        await requesting.commit()
        assert await asyncio.wait_for(gate, timeout=30) is False
    finally:
        await uploading.rollback()
        await uploading.close()
        await requesting.close()


# ---------------------------------------------------------------------------
# D — no deadlock across request, upload, cancellation and the worker
# ---------------------------------------------------------------------------
async def test_d_a_request_behind_a_cancellation_waits_then_requests_again(
    db_clean, registered_supabase_user, race,
):
    """Cancellation holds the job row and the account; the request waits on
    the account only, then decides from what cancellation committed."""
    _, account_id = await _requested(registered_supabase_user)
    cancelling, requesting = _factory()(), _factory()()
    try:
        await deletion_service.cancel_deletion(cancelling, account_id)  # job gone, account active: held
        await _patient(requesting)
        pid = await _pid(requesting)
        request = race.spawn(deletion_service.request_deletion(requesting, account_id))
        await _until_waiting_on_an_account(pid)
        await cancelling.commit()
        job = await asyncio.wait_for(request, timeout=30)
        await requesting.commit()
    finally:
        await cancelling.close()
        await requesting.close()
    assert job.state == STATE_REQUESTED and job.started_at is None
    assert (await _job(account_id)).id == job.id
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


async def test_d_a_cancellation_behind_a_request_holding_the_account_does_not_deadlock(
    db_clean, registered_supabase_user, race, monkeypatch,
):
    """The interleaving an inversion would deadlock on.

    The request holds the account and has not yet looked at the job.
    Cancellation locks the job and waits for the account. If the request then
    waited for the job row, each would wait for the other. It reads the job
    without a lock instead, returns, and cancellation completes after it.
    """
    _, account_id = await _requested(registered_supabase_user)
    holding_account = race.pause()
    original = identity_service.lock_account_lifecycle

    async def lock_then_pause(session, account):
        row = await original(session, account)
        await holding_account()
        return row

    monkeypatch.setattr(identity_service, "lock_account_lifecycle", lock_then_pause)
    requesting, cancelling = _factory()(), _factory()()
    try:
        await _patient(requesting)
        request = race.spawn(deletion_service.request_deletion(requesting, account_id))
        await holding_account.wait_reached()

        await _patient(cancelling)
        cancel_pid = await _pid(cancelling)
        cancel = race.spawn(deletion_service.cancel_deletion(cancelling, account_id))
        await _until_waiting_on_an_account(cancel_pid)

        holding_account.release.set()
        returned = await asyncio.wait_for(request, timeout=30)
        await requesting.commit()
        await asyncio.wait_for(cancel, timeout=30)
        await cancelling.commit()
    finally:
        await requesting.close()
        await cancelling.close()
    assert returned.state == STATE_REQUESTED
    # Cancellation was serialised after the request, and nothing was destroyed.
    assert await _job(account_id) is None
    assert (await _account(account_id)).status == ACCOUNT_STATUS_ACTIVE


async def test_d_a_request_while_the_worker_deletes_the_account_waits_then_reports_it(
    db_clean, registered_supabase_user, storage, admin, race, monkeypatch,
):
    _, account_id = await _requested(registered_supabase_user)
    deleted = _worker_pauses(monkeypatch, race, "_delete_account_row", after=True)
    worker = race.spawn(account_deletion.run_cycle())
    await deleted.wait_reached()  # the worker holds the job row and the account DELETE

    requesting = _factory()()
    try:
        await _patient(requesting)
        pid = await _pid(requesting)
        request = race.spawn(deletion_service.request_deletion(requesting, account_id))
        assert deleted.pid in await _until_waiting_on_an_account(pid)
        deleted.release.set()
        summary = await asyncio.wait_for(worker, timeout=30)
        job = await asyncio.wait_for(request, timeout=30)
        await requesting.commit()
    finally:
        await requesting.close()
    assert summary.ok, summary
    assert job.state == STATE_COMPLETE
    assert await _account(account_id) is None
    assert admin.deleted == [str(account_id)]


async def test_d_an_upload_behind_the_worker_account_delete_is_refused(
    app_client, db_clean, registered_supabase_user, storage, admin, events, race, monkeypatch,
):
    """The upload authenticated before the request and reaches the gate while
    the worker holds the account DELETE. It waits, finds no account, and
    writes nothing. The worker never waits on it."""
    token, account_id = await registered_supabase_user()
    in_handler = _upload_pauses_after_authentication(monkeypatch, race)
    upload = race.spawn(_upload(app_client, token))
    await in_handler.wait_reached()
    assert (await app_client.delete(DELETE, headers=auth(token))).status_code == 202

    deleted = _worker_pauses(monkeypatch, race, "_delete_account_row", after=True)
    worker = race.spawn(account_deletion.run_cycle())
    await deleted.wait_reached()

    in_handler.release.set()
    await _until_something_waits_on(deleted.pid)
    assert storage.puts == []
    deleted.release.set()
    summary = await asyncio.wait_for(worker, timeout=30)
    response = await asyncio.wait_for(upload, timeout=30)

    assert summary.ok, summary
    assert _inactive(response), response.text
    assert storage.puts == [] and _under(storage, account_id) == []
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert events == ["storage_purge", "storage_purge", "auth"]


# ---------------------------------------------------------------------------
# G — the notification worker's final lifecycle gate
# ---------------------------------------------------------------------------
_GATE_KEY = "agenda:final-gate"


def _one_queued_notice(monkeypatch) -> None:
    """The worker's decision is one real queued delivery, every time.

    Every earlier trigger is quiet, and the agenda queues through the real
    outbox, so the claim, the dedup and the suppression rules are the real
    ones.
    """
    notifications = notification_worker.notifications

    async def quiet(*args, **kwargs):
        return None

    for earlier in (
        "queue_for_product_watch",
        "queue_for_environment_crossing",
        "queue_for_protocol_day",
        "queue_for_running_out",
        "queue_for_deferred_purchase_relevance",
    ):
        monkeypatch.setattr(notifications, earlier, quiet)

    async def agenda(session, *, account_id, plan_date, timezone_name, moment):
        return await notifications.queue(
            session, account_id=account_id, plan_date=plan_date, notification_key=_GATE_KEY,
            title="Today", module="care", topic="care", timezone_name=timezone_name, moment=moment,
        )

    monkeypatch.setattr(notifications, "queue_for_agenda", agenda)


async def _delivery_rows(account_id) -> list[NotificationDelivery]:
    async with _factory()() as session:
        return list((await session.execute(
            select(NotificationDelivery).where(NotificationDelivery.account_id == account_id)
        )).scalars().all())


async def test_g_the_notice_under_test_is_really_sent_to_an_active_account(
    db_clean, registered_supabase_user, monkeypatch,
):
    """The control: without a deletion, this exact set-up reaches the provider."""
    _, account_id = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_id, hour=9)
    _one_queued_notice(monkeypatch)
    sender = _CountingPush()
    monkeypatch.setattr(notification_worker.push, "send", sender.send)
    assert await notification_worker.process_once(now=moment) == 1
    assert sender.messages_sent == 1
    [row] = await _delivery_rows(account_id)
    assert row.status == notification_worker.notifications.STATUS_PROVIDER_ACCEPTED


async def test_g_a_deletion_between_the_first_gate_and_the_send_sends_nothing(
    db_clean, registered_supabase_user, monkeypatch, race,
):
    _, account_id = await registered_supabase_user()
    moment = _at_local_hour(9)
    await _opt_in(account_id, hour=9)
    _one_queued_notice(monkeypatch)
    notifications = notification_worker.notifications
    sender = _CountingPush()
    monkeypatch.setattr(notification_worker.push, "send", sender.send)

    answers: list[bool] = []
    is_active = notification_worker._account_is_active

    async def observed(session, account):
        answers.append(await is_active(session, account))
        return answers[-1]

    monkeypatch.setattr(notification_worker, "_account_is_active", observed)
    claimed = race.pause()
    claim_delivery = notifications.claim_delivery

    async def claim_then_pause(session, delivery_id, **kwargs):
        claimed.value = await claim_delivery(session, delivery_id, **kwargs)
        await claimed()
        return claimed.value

    monkeypatch.setattr(notifications, "claim_delivery", claim_then_pause)

    # 1-2. The run starts while the account is active, passes the first gate,
    # compiles, queues and claims, and stops before the send boundary.
    run = race.spawn(notification_worker.process_once(now=moment))
    await claimed.wait_reached()
    assert answers == [True] and claimed.value is not None
    # 3. Deletion is requested and committed.
    async with _factory()() as session:
        await deletion_service.request_deletion(session, account_id)
        await session.commit()
    # 4. Resume.
    claimed.release.set()
    assert await asyncio.wait_for(run, timeout=30) == 0

    # 5. No provider call, and a coherent, terminal, unclaimed row.
    assert sender.batches == []
    assert answers == [True, False]
    [row] = await _delivery_rows(account_id)
    assert row.status == notifications.STATUS_SUPPRESSED
    assert row.suppressed_reason == notifications.SUPPRESSED_ACCOUNT_INACTIVE
    assert row.claim_token is None and row.claimed_at is None and row.sent_at is None

    # No later send: the next cycle, a direct run, a stale-claim sweep, and
    # even the same account made active again by a pristine cancellation.
    assert await notification_worker.process_once(now=moment) == 0
    async with _factory()() as session:
        preference = await notifications.preferences_for(session, account_id, "Asia/Kolkata")
        assert await notification_worker.process_account(session, preference, now=moment) == 0
    async with _factory()() as session:
        assert await claim_delivery(session, row.id, lease_seconds=0) is None
        await session.rollback()
    async with _factory()() as session:
        await deletion_service.cancel_deletion(session, account_id)
        await session.commit()
    assert (await _account(account_id)).status == ACCOUNT_STATUS_ACTIVE
    assert await notification_worker.process_once(now=moment) == 0
    assert sender.batches == []
    [row] = await _delivery_rows(account_id)
    assert row.status == notifications.STATUS_SUPPRESSED and row.claim_token is None


def test_g_the_final_gate_is_the_last_step_before_the_provider():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "workers" / "notifications.py").read_text()
    body = source[source.index("async def process_account"):source.index("async def process_once")]
    claim = body.index("await notifications.claim_delivery(")
    send = body.index("push.send(") + len("push.send(")
    awaits = re.findall(r"await ([\w.]+)\(", body[claim:send])
    assert awaits == [
        "notifications.claim_delivery", "session.rollback", "session.commit",
        "_settle_if_account_inactive", "push.send",
    ], awaits
    # The first gate is kept, before any work.
    assert body.index("_account_is_active(") < body.index("context_stage.gather")
    # The final gate's transaction closes before the provider is called.
    gate = source[source.index("async def _settle_if_account_inactive"):source.index("def _preference_due")]
    assert "async with session.begin():" in gate and "push.send(" not in gate
