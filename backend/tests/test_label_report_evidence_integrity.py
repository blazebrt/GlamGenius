"""Lane C: a label-error report's photo is evidence, and evidence is immutable.

``client_report_id`` is the phone's idempotency key, unique only per device.
It used to be the object key too (``label-reports/{client_report_id}.jpg``):
two phones choosing one id overwrote each other's photograph, a replay wrote
its bytes before discovering the report already existed, and the global
namespace sat outside account erasure. Here, against PostgreSQL 16:

* K — the object key is server-owned: the report's own UUID, in the claimed
  account's canonical prefix or an anonymous device-scoped namespace
* I — idempotency is resolved before any byte, and serialised across retries
* L — a claimed device's report and the account's deletion request serialise
  on the account row, exactly as a media upload does
* E — erasure removes legacy global report photos before the row cascade, and
  resolves historically collided keys in favour of privacy
* C — ordinary failures compensate rather than orphan

Every pause is an event the test sets; every wait is on something PostgreSQL
reports (``pg_blocking_pids``, ``pg_locks``). No sleep decides an outcome.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest
from app.domains.identity.models import ACCOUNT_STATUS_ACTIVE, ACCOUNT_STATUS_DELETION_REQUESTED
from app.domains.media.storage import factory as storage_factory
from app.domains.media.storage.base import (
    StorageObjectMissing,
    StorageUnavailable,
    account_prefix,
)
from app.domains.privacy import deletion_service
from app.domains.privacy.models import (
    STATE_COMPLETE,
    STATE_DATABASE_DELETING,
    STATE_FAILED_RETRYABLE,
    STATE_STORAGE_DELETING,
)
from app.domains.product import service as product_service
from app.domains.product.devices import _hash
from app.domains.product.models import LabelErrorReport, ScanDevice
from app.workers import account_deletion
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import auth
from tests.test_account_deletion_integrity import (
    _account,
    _factory,
    _job,
    _make_due,
    _patient,
    _Pause,
    _pid,
    _until_something_waits_on,
    _until_waiting_on_an_account,
)

REPORT = "/api/v2/reports/label-error"
DELETE = "/api/v2/privacy/account"
PHOTO_A = b"\xff\xd8photo-from-phone-A"
PHOTO_B = b"\xff\xd8photo-from-phone-B"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------
class _EvidenceStorage:
    """Object storage with overwrite semantics, a call log and scripted faults.

    ``put`` overwrites, as the real adapters do (Supabase uploads with
    ``upsert``). ``get`` of a missing key raises ``StorageObjectMissing``, as
    both real adapters do.
    """

    backend_name = "evidence"

    def __init__(self, events: list[str]) -> None:
        self.objects: dict[str, bytes] = {}
        self.events = events
        self.puts: list[str] = []
        self.put_pause: _Pause | None = None
        self.put_failure: Exception | None = None
        #: keys whose delete silently does nothing (a read still finds them)
        self.stuck: set[str] = set()
        #: exception raised by the next delete of a key, then forgotten
        self.delete_failures: dict[str, Exception] = {}

    async def put(self, key, data, content_type):
        if self.put_failure is not None:
            raise self.put_failure
        self.puts.append(key)
        if self.put_pause is not None:
            await self.put_pause()
        self.objects[key] = data

    async def get(self, key):
        if key not in self.objects:
            raise StorageObjectMissing(key)
        return self.objects[key]

    async def delete(self, key):
        failure = self.delete_failures.pop(key, None)
        if failure is not None:
            raise failure
        self.events.append(f"delete:{key}")
        if key not in self.stuck:
            self.objects.pop(key, None)

    async def exists(self, key):
        return key in self.objects

    async def presigned_get_url(self, key, ttl):
        return None

    async def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix):
        self.events.append("storage_purge")
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


class _Admin:
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
async def race():
    """Pauses and tasks for one race, all let go and finished at teardown.

    The same discipline as the deletion-integrity suite: a failing assertion
    must not leave a paused coroutine holding a lock the next test's truncate
    would wait on.
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
            task.exception()


@pytest.fixture
def events() -> list[str]:
    return []


@pytest.fixture
def storage(events):
    double = _EvidenceStorage(events)
    storage_factory.set_storage(double)
    yield double
    storage_factory.set_storage(None)


@pytest.fixture
def admin(monkeypatch, events):
    double = _Admin(events)
    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: double)
    return double


@pytest.fixture
def account_row_events(monkeypatch, events):
    original = deletion_service._delete_account_row

    async def delete_row(session, account_id):
        events.append("account_row")
        await original(session, account_id)

    monkeypatch.setattr(deletion_service, "_delete_account_row", delete_row)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _phone(app_client, token: str | None = None) -> dict[str, str]:
    registered = await app_client.post(
        "/api/v2/scan/device", json={"device_key": uuid.uuid4().hex, "platform": "android"},
    )
    assert registered.status_code == 201, registered.text
    headers = {"X-Device-Token": registered.json()["token"]}
    if token is not None:
        claimed = await app_client.post("/api/v2/scan/device/claim", headers={**headers, **auth(token)})
        assert claimed.status_code == 200, claimed.text
    return headers


async def _device_id(headers) -> uuid.UUID:
    async with _factory()() as session:
        return (await session.execute(
            select(ScanDevice.id).where(ScanDevice.token_hash == _hash(headers["X-Device-Token"]))
        )).scalar_one()


async def _report(app_client, phone, client_report_id, photo: bytes | None = PHOTO_A, **fields):
    data = {"client_report_id": client_report_id, "subject": "Sugar per 100 g", "reason": "wrong_number",
            "barcode": "8905000000010", **fields}
    files = {"photo": ("label.jpg", photo, "image/jpeg")} if photo is not None else None
    return await app_client.post(REPORT, headers=phone, data=data, files=files)


async def _rows(**where) -> list[LabelErrorReport]:
    async with _factory()() as session:
        statement = select(LabelErrorReport)
        for column, value in where.items():
            statement = statement.where(getattr(LabelErrorReport, column) == value)
        return list((await session.execute(statement.order_by(LabelErrorReport.created_at))).scalars().all())


async def _row(report_id: str) -> LabelErrorReport | None:
    async with _factory()() as session:
        return await session.get(LabelErrorReport, uuid.UUID(report_id))


async def _legacy_report(*, account_id, device_id, client_report_id, storage, data: bytes | None) -> str:
    """A report as the old route filed it: the global, caller-keyed object."""
    key = f"{product_service.LEGACY_LABEL_REPORT_PREFIX}/{client_report_id}.jpg"
    async with _factory()() as session:
        session.add(LabelErrorReport(
            device_id=device_id, account_id=account_id, client_report_id=client_report_id,
            barcode="8905000000010", subject="legacy report", reason="wrong_number", photo_key=key,
        ))
        await session.commit()
    if data is not None:
        storage.objects[key] = data
    return key


async def _request_deletion(app_client, token):
    response = await app_client.delete(DELETE, headers=auth(token))
    assert response.status_code == 202, response.text


def _inactive(response) -> bool:
    return response.status_code == 403 and response.json()["detail"]["code"] == "ACCOUNT_INACTIVE"


# ---------------------------------------------------------------------------
# K — the object key is server-owned
# ---------------------------------------------------------------------------
async def test_k_a_two_phones_with_one_client_report_id_keep_two_photographs(
    app_client, db_clean, storage,
):
    """A. The collision. Each report keeps its own bytes under its own key."""
    phone_a = await _phone(app_client)
    phone_b = await _phone(app_client)
    first = await _report(app_client, phone_a, "shared-report-id", PHOTO_A)
    second = await _report(app_client, phone_b, "shared-report-id", PHOTO_B)
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["created"] is True and second.json()["created"] is True

    row_a, row_b = await _row(first.json()["report_id"]), await _row(second.json()["report_id"])
    assert row_a.id != row_b.id and row_a.photo_key != row_b.photo_key
    assert storage.objects[row_a.photo_key] == PHOTO_A
    assert storage.objects[row_b.photo_key] == PHOTO_B
    assert "shared-report-id" not in row_a.photo_key and "shared-report-id" not in row_b.photo_key


async def test_k_a_two_accounts_with_one_client_report_id_keep_two_photographs(
    app_client, db_clean, storage, registered_supabase_user,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _phone(app_client, token_a)
    phone_b = await _phone(app_client, token_b)
    first = await _report(app_client, phone_a, "same-id-two-accounts", PHOTO_A)
    second = await _report(app_client, phone_b, "same-id-two-accounts", PHOTO_B)
    row_a, row_b = await _row(first.json()["report_id"]), await _row(second.json()["report_id"])
    assert storage.objects[row_a.photo_key] == PHOTO_A
    assert storage.objects[row_b.photo_key] == PHOTO_B
    assert row_a.photo_key.startswith(f"{account_prefix(account_a)}/")
    assert row_b.photo_key.startswith(f"{account_prefix(account_b)}/")


async def test_k_d_a_claimed_phones_photo_lives_under_its_account_prefix(
    app_client, db_clean, storage, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    response = await _report(app_client, phone, "claimed-report")
    assert response.status_code == 201, response.text
    row = await _row(response.json()["report_id"])
    assert row.account_id == account_id
    assert row.photo_key == f"{account_prefix(account_id)}/label-reports/{row.id}.jpg"
    assert storage.objects[row.photo_key] == PHOTO_A
    assert storage.puts == [row.photo_key]


async def test_k_e_an_anonymous_phones_photo_is_device_scoped_and_claims_no_account(
    app_client, db_clean, storage,
):
    phone = await _phone(app_client)
    device_id = await _device_id(phone)
    response = await _report(app_client, phone, "anonymous-report")
    assert response.status_code == 201, response.text
    row = await _row(response.json()["report_id"])
    assert row.account_id is None
    assert row.photo_key == f"label-reports/devices/{device_id}/{row.id}.jpg"
    assert not row.photo_key.startswith("media/")
    assert "anonymous-report" not in row.photo_key


async def test_k_a_hostile_client_report_id_cannot_steer_the_object_key(app_client, db_clean, storage):
    phone = await _phone(app_client)
    response = await _report(app_client, phone, "../../media/victim")
    assert response.status_code == 201, response.text
    row = await _row(response.json()["report_id"])
    assert ".." not in row.photo_key and "victim" not in row.photo_key
    assert storage.puts == [row.photo_key]


async def test_k_a_report_without_a_photo_writes_nothing(app_client, db_clean, storage):
    phone = await _phone(app_client)
    response = await _report(app_client, phone, "no-photo-report", photo=None)
    assert response.status_code == 201, response.text
    assert (await _row(response.json()["report_id"])).photo_key is None
    assert storage.puts == []


# ---------------------------------------------------------------------------
# I — idempotency before bytes, serialised across retries
# ---------------------------------------------------------------------------
async def test_i_b_a_replay_returns_the_original_and_never_touches_its_photo(
    app_client, db_clean, storage, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    phone = await _phone(app_client, token)
    first = await _report(app_client, phone, "replayed-report", PHOTO_A)
    # The retry carries different bytes: an idempotent retry must not replace evidence.
    replay = await _report(app_client, phone, "replayed-report", PHOTO_B)
    assert first.status_code == replay.status_code == 201
    assert replay.json() == {"report_id": first.json()["report_id"], "created": False}
    row = await _row(first.json()["report_id"])
    assert storage.puts == [row.photo_key], "a replay wrote to storage"
    assert storage.objects[row.photo_key] == PHOTO_A
    assert len(await _rows(client_report_id="replayed-report")) == 1


async def test_i_c_concurrent_retries_of_one_report_upload_exactly_once(
    app_client, db_clean, storage, race,
):
    """Two transactions carrying one (device, client_report_id) really overlap.

    Sessions are driven directly: over HTTP each request also locks its device
    row while resolving the token, which would serialise them before the
    advisory lock was ever reached. The first holds the idempotency lock while
    its upload is paused; the second must wait on that lock, then find the
    first report and write nothing.
    """
    phone = await _phone(app_client)
    device_id = await _device_id(phone)
    storage.put_pause = race.pause()

    async def file(session: AsyncSession, photo: bytes):
        result = await product_service.file_label_error_report(
            session, device_id=device_id, account_id=None, client_report_id="concurrent-report",
            subject="Sugar", reason="wrong_number", photo=photo, photo_content_type="image/jpeg",
        )
        await session.commit()
        return result

    first_session, second_session = _factory()(), _factory()()
    try:
        first = race.spawn(file(first_session, PHOTO_A))
        await storage.put_pause.wait_reached()  # holds the idempotency lock, mid-upload
        await _patient(second_session)
        second_pid = await _pid(second_session)
        second = race.spawn(file(second_session, PHOTO_B))
        await _until_waiting_on_advisory_lock(second_pid)
        assert not second.done() and len(storage.puts) == 1

        storage.put_pause.release.set()
        report_one, created_one, key_one = await asyncio.wait_for(first, timeout=30)
        report_two, created_two, key_two = await asyncio.wait_for(second, timeout=30)
    finally:
        await first_session.close()
        await second_session.close()

    assert created_one is True and created_two is False
    assert report_one.id == report_two.id and key_two is None
    assert storage.puts == [key_one], "the loser uploaded an object"
    assert list(storage.objects) == [key_one] and storage.objects[key_one] == PHOTO_A
    assert len(await _rows(client_report_id="concurrent-report")) == 1


async def _until_waiting_on_advisory_lock(pid: int) -> None:
    for _ in range(3000):
        async with _factory()() as watcher:
            waiting = await watcher.scalar(text(
                "SELECT count(*) FROM pg_locks WHERE pid = :pid AND locktype = 'advisory' AND NOT granted"
            ), {"pid": pid})
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} never waited on the report idempotency lock")


# ---------------------------------------------------------------------------
# L — a claimed report and the deletion request serialise on the account row
# ---------------------------------------------------------------------------
async def test_l_f_deletion_first_the_report_writes_no_byte_and_no_row(
    app_client, db_clean, storage, registered_supabase_user, race,
):
    """F. Deletion wins: the report waits at the account row, then is refused."""
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    requesting = _factory()()
    try:
        holder = await _pid(requesting)
        await deletion_service.request_deletion(requesting, account_id)  # held, uncommitted
        report = race.spawn(_report(app_client, phone, "deletion-wins"))
        await _until_something_waits_on(holder)
        assert not report.done() and storage.puts == []
        await requesting.commit()
    finally:
        await requesting.close()

    response = await asyncio.wait_for(report, timeout=30)
    assert _inactive(response), response.text
    assert storage.puts == [] and storage.objects == {}
    assert await _rows(client_report_id="deletion-wins") == []
    assert (await _account(account_id)).status == ACCOUNT_STATUS_DELETION_REQUESTED


async def test_l_g_report_first_the_deletion_waits_then_erases_it(
    app_client, db_clean, storage, admin, events, registered_supabase_user, race,
):
    """G. Report wins: the request waits, the report commits, the worker erases it."""
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    storage.put_pause = race.pause()
    report = race.spawn(_report(app_client, phone, "report-wins"))
    await storage.put_pause.wait_reached()  # past the lifecycle hold, mid-write

    requesting = _factory()()
    try:
        await _patient(requesting)
        pid = await _pid(requesting)
        request = race.spawn(deletion_service.request_deletion(requesting, account_id))
        await _until_waiting_on_an_account(pid)
        assert not request.done()
        assert (await _account(account_id)).status == ACCOUNT_STATUS_ACTIVE

        storage.put_pause.release.set()
        response = await asyncio.wait_for(report, timeout=30)
        assert response.status_code == 201, response.text
        await asyncio.wait_for(request, timeout=30)
        await requesting.commit()
    finally:
        await requesting.close()

    row = await _row(response.json()["report_id"])
    assert row.photo_key.startswith(f"{account_prefix(account_id)}/")
    assert storage.objects[row.photo_key] == PHOTO_A

    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert row.photo_key not in storage.objects
    assert await _row(response.json()["report_id"]) is None
    assert admin.deleted == [str(account_id)] and events[-1] == "auth"


async def test_l_a_waiting_report_holds_no_device_row_the_account_delete_needs(
    app_client, db_clean, storage, admin, events, registered_supabase_user, race, monkeypatch,
):
    """Account first, then the device row — never the other way round.

    Resolving the device token dirties ``last_seen_at``; flushed early, that
    UPDATE would lock the device row before the account hold. The worker's
    account DELETE holds the account and cascades into that same device row
    (``claimed_by_account_id`` SET NULL), so a report holding the device row
    while it waited for the account would be one half of a deadlock. Here the
    report is stopped exactly at the account hold and the whole worker cycle,
    DELETE included, must run to completion past it.
    """
    from app.domains.identity import service as identity_service

    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    await _request_deletion(app_client, token)

    at_hold = race.pause()
    original = identity_service.hold_account_active

    async def paused(session, account):
        await at_hold()
        return await original(session, account)

    monkeypatch.setattr(identity_service, "hold_account_active", paused)
    report = race.spawn(_report(app_client, phone, "lock-order-report"))
    await at_hold.wait_reached()  # idempotency resolved, account not yet held

    # A ceiling on a broken build: a correct order finishes at once.
    summary = await asyncio.wait_for(account_deletion.run_cycle(), timeout=30)
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert await _account(account_id) is None

    at_hold.release.set()
    response = await asyncio.wait_for(report, timeout=30)
    assert _inactive(response), response.text
    assert storage.puts == [] and await _rows(client_report_id="lock-order-report") == []


async def test_l_an_anonymous_report_takes_no_account_lock(app_client, db_clean, storage, monkeypatch):
    from app.domains.identity import service as identity_service

    calls: list = []
    original = identity_service.hold_account_active

    async def recording(session, account_id):
        calls.append(account_id)
        return await original(session, account_id)

    monkeypatch.setattr(identity_service, "hold_account_active", recording)
    phone = await _phone(app_client)
    assert (await _report(app_client, phone, "anonymous-no-lock")).status_code == 201
    assert calls == []


def test_l_the_order_is_idempotency_then_lifecycle_then_bytes():
    """The contract, read from the source: lock, lookup, hold, key, put."""
    source = inspect.getsource(product_service.file_label_error_report)
    body = source[source.index('"""', source.index('"""') + 3):]
    order = [
        body.index("lock_label_report_identity("),
        body.index("_existing_label_report("),
        body.index("identity_service.hold_account_active("),
        body.index("label_report_photo_key("),
        body.index(".put("),
    ]
    assert order == sorted(order), order


# ---------------------------------------------------------------------------
# E — erasure of report evidence
# ---------------------------------------------------------------------------
async def test_e_h_deletion_erases_a_legacy_global_photo_before_the_row_cascade(
    app_client, db_clean, storage, admin, events, account_row_events, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    legacy_key = await _legacy_report(
        account_id=account_id, device_id=await _device_id(phone),
        client_report_id="legacy-report", storage=storage, data=b"legacy bytes",
    )
    await _request_deletion(app_client, token)
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert legacy_key not in storage.objects
    assert await _rows(client_report_id="legacy-report") == []
    assert events.index(f"delete:{legacy_key}") < events.index("account_row") < events.index("auth")


async def test_e_i_a_failing_legacy_erase_stops_before_the_database_and_auth(
    app_client, db_clean, storage, admin, events, registered_supabase_user,
):
    """I. The first proof fails closed, retries, then completes."""
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    legacy_key = await _legacy_report(
        account_id=account_id, device_id=await _device_id(phone),
        client_report_id="legacy-flaky", storage=storage, data=b"legacy bytes",
    )
    storage.delete_failures[legacy_key] = StorageUnavailable("storage down")
    await _request_deletion(app_client, token)

    summary = await account_deletion.run_cycle()
    assert summary.ok is False
    job = await _job(account_id)
    assert job.state == STATE_FAILED_RETRYABLE and job.last_error_stage == STATE_STORAGE_DELETING
    assert await _account(account_id) is not None and admin.deleted == []
    assert storage.objects[legacy_key] == b"legacy bytes"
    assert [row.photo_key for row in await _rows(client_report_id="legacy-flaky")] == [legacy_key]

    await _make_due(account_id)
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert legacy_key not in storage.objects and admin.deleted == [str(account_id)]


async def test_e_i_an_object_that_survives_its_delete_blocks_the_final_proof(
    app_client, db_clean, storage, admin, events, registered_supabase_user, monkeypatch,
):
    """I/K. At the final barrier: legacy evidence still readable → no database, no Auth.

    The legacy row appears after the first proof, so it is the final proof
    alone that must find it.
    """
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    device_id = await _device_id(phone)
    late: dict[str, str] = {}
    original = deletion_service._remove_external_integrations

    async def integrations(session, account):
        await original(session, account)
        if not late:
            late["key"] = await _legacy_report(
                account_id=account_id, device_id=device_id, client_report_id="legacy-late",
                storage=storage, data=b"late legacy bytes",
            )
            storage.stuck.add(late["key"])

    monkeypatch.setattr(deletion_service, "_remove_external_integrations", integrations)
    await _request_deletion(app_client, token)

    summary = await account_deletion.run_cycle()
    assert summary.error_code == "storage_incomplete"
    job = await _job(account_id)
    assert job.state == STATE_FAILED_RETRYABLE and job.last_error_stage == STATE_DATABASE_DELETING
    account = await _account(account_id)
    assert account is not None and account.status == ACCOUNT_STATUS_DELETION_REQUESTED
    assert admin.deleted == [] and "auth" not in events
    # The row still names the object, so the retry can still find it.
    assert [row.photo_key for row in await _rows(client_report_id="legacy-late")] == [late["key"]]

    storage.stuck.clear()
    await _make_due(account_id)
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert (await _job(account_id)).state == STATE_COMPLETE
    assert late["key"] not in storage.objects and admin.deleted == [str(account_id)]


async def test_e_j_a_historically_collided_key_is_erased_and_the_survivor_stops_claiming_it(
    app_client, db_clean, storage, admin, registered_supabase_user,
):
    """J. Two accounts' old reports named one object. Nobody can say whose it is.

    Erasing A must not preserve A's photograph under B, and B's report must
    not go on claiming a photograph that no longer exists.
    """
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    phone_a = await _phone(app_client, token_a)
    phone_b = await _phone(app_client, token_b)
    shared = await _legacy_report(
        account_id=account_a, device_id=await _device_id(phone_a),
        client_report_id="collided-id", storage=storage, data=b"whichever upload came last",
    )
    again = await _legacy_report(
        account_id=account_b, device_id=await _device_id(phone_b),
        client_report_id="collided-id", storage=storage, data=None,
    )
    assert again == shared
    anonymous_phone = await _phone(app_client)
    await _legacy_report(
        account_id=None, device_id=await _device_id(anonymous_phone),
        client_report_id="collided-id", storage=storage, data=None,
    )
    before = set(storage.objects)

    await _request_deletion(app_client, token_a)
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert shared not in storage.objects
    assert set(storage.objects) == before - {shared}, "nothing was copied anywhere"
    survivors = await _rows(client_report_id="collided-id")
    assert {row.account_id for row in survivors} == {account_b, None}
    assert all(row.photo_key is None for row in survivors)
    # Everything else the surviving reports said is kept.
    assert all(row.subject == "legacy report" and row.reason == "wrong_number" for row in survivors)
    # B is untouched otherwise and still a normal account.
    assert (await _account(account_b)).status == ACCOUNT_STATUS_ACTIVE


async def test_e_a_legacy_key_that_escapes_its_namespace_is_never_erased_automatically(
    app_client, db_clean, storage, admin, events, registered_supabase_user,
):
    """The old keys embedded the caller's id verbatim, so one can point anywhere.

    A row whose key leaves ``label-reports/`` is not the worker's to guess
    about: nothing is deleted under another account's prefix, and the job
    fails closed for a person to look at.
    """
    token_a, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    phone = await _phone(app_client, token_a)
    victim = f"{account_prefix(account_b)}/victim.jpg"
    storage.objects[victim] = b"somebody else's photograph"
    async with _factory()() as session:
        session.add(LabelErrorReport(
            device_id=await _device_id(phone), account_id=account_a, client_report_id="escape-id",
            subject="legacy report", reason="wrong_number",
            photo_key=f"label-reports/../{account_prefix(account_b)}/victim.jpg",
        ))
        await session.commit()
    await _request_deletion(app_client, token_a)

    summary = await account_deletion.run_cycle()
    assert summary.error_code == "storage_incomplete"
    job = await _job(account_a)
    assert job.state == STATE_FAILED_RETRYABLE and job.last_error_stage == STATE_STORAGE_DELETING
    assert storage.objects[victim] == b"somebody else's photograph"
    assert not [event for event in events if event.startswith("delete:")]
    assert await _account(account_a) is not None and admin.deleted == []


async def test_e_k_the_final_proof_covers_the_prefix_and_legacy_evidence_together(
    app_client, db_clean, storage, admin, events, account_row_events, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    current = await _report(app_client, phone, "current-report")
    assert current.status_code == 201
    current_key = (await _row(current.json()["report_id"])).photo_key
    legacy_key = await _legacy_report(
        account_id=account_id, device_id=await _device_id(phone),
        client_report_id="old-report", storage=storage, data=b"old",
    )
    await _request_deletion(app_client, token)
    summary = await account_deletion.run_cycle()
    assert summary.ok, summary
    assert current_key not in storage.objects and legacy_key not in storage.objects
    assert not [k for k in storage.objects if k.startswith(account_prefix(account_id))]
    assert events.index("account_row") > events.index(f"delete:{legacy_key}")
    assert events[-1] == "auth"


def test_e_k_both_storage_proofs_erase_report_evidence_before_any_database_deletion():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "domains" / "privacy" / "deletion_service.py").read_text()
    stage = source[source.index("if job.state == STATE_DATABASE_DELETING:"):]
    stage = stage[:stage.index("job.state = STATE_DATABASE_COMPLETE")]
    purge = stage.index("media_service.purge_account_storage(job.account_id)")
    legacy = stage.index("_erase_external_report_photos(session, job.account_id)")
    assert purge < legacy
    for deletion in ("_delete_ai_outputs", "_delete_analytics_events", "_scrub_audit_events",
                     "_withdraw_scan_observations", "_delete_account_row"):
        assert legacy < stage.index(deletion), deletion
    first = source[source.index("if job.state == STATE_STORAGE_DELETING:"):]
    first = first[:first.index("job.state = STATE_STORAGE_COMPLETE")]
    assert "_erase_external_report_photos(session, job.account_id)" in first


# ---------------------------------------------------------------------------
# C — ordinary failures compensate; nothing claims created=true falsely
# ---------------------------------------------------------------------------
async def test_c_a_storage_failure_files_nothing(app_client, db_clean, storage):
    phone = await _phone(app_client)
    storage.put_failure = StorageUnavailable("down")
    response = await _report(app_client, phone, "storage-down")
    assert response.status_code == 503, response.text
    assert await _rows(client_report_id="storage-down") == []
    assert storage.objects == {}
    # The phone's retry then files it once.
    storage.put_failure = None
    retry = await _report(app_client, phone, "storage-down")
    assert retry.status_code == 201 and retry.json()["created"] is True


async def test_c_a_row_failure_after_the_bytes_removes_them(app_client, db_clean, storage):
    phone = await _phone(app_client)
    device_id = await _device_id(phone)

    class _Boom(Exception):
        pass

    async with _factory()() as session:
        original_flush = session.flush

        async def failing_flush(*args, **kwargs):
            if any(isinstance(obj, LabelErrorReport) for obj in session.new):
                raise _Boom()
            return await original_flush(*args, **kwargs)

        session.flush = failing_flush  # type: ignore[method-assign]
        with pytest.raises(_Boom):
            await product_service.file_label_error_report(
                session, device_id=device_id, account_id=None, client_report_id="row-fails",
                subject="Sugar", reason="wrong_number", photo=PHOTO_A,
            )
        await session.rollback()
    assert len(storage.puts) == 1 and storage.objects == {}, "the written object was not compensated"
    assert await _rows(client_report_id="row-fails") == []


async def test_c_a_commit_failure_after_the_bytes_removes_them(app_client, db_clean, storage, monkeypatch):
    phone = await _phone(app_client)
    original_commit = AsyncSession.commit
    failed = {"done": False}

    async def failing_commit(self):
        if not failed["done"] and any(isinstance(obj, LabelErrorReport) for obj in self.identity_map.values()):
            failed["done"] = True
            raise RuntimeError("commit failed")
        return await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError, match="commit failed"):
        await _report(app_client, phone, "commit-fails")
    monkeypatch.setattr(AsyncSession, "commit", original_commit)
    assert failed["done"] and len(storage.puts) == 1
    assert storage.objects == {}, "the written object outlived a report that never committed"
    assert await _rows(client_report_id="commit-fails") == []


async def test_c_committed_report_survives_lost_ack_and_retry(app_client, db_clean, storage, monkeypatch):
    phone = await _phone(app_client)
    original_commit = AsyncSession.commit
    failed = False

    async def lost_ack(self):
        nonlocal failed
        is_report = any(isinstance(obj, LabelErrorReport) for obj in self.identity_map.values())
        await original_commit(self)
        if is_report and not failed:
            failed = True
            raise RuntimeError("commit acknowledgement lost")

    monkeypatch.setattr(AsyncSession, "commit", lost_ack)
    response = await _report(app_client, phone, "ack-lost")
    assert failed and response.status_code == 201, response.text
    assert response.json()["created"] is False
    row = await _row(response.json()["report_id"])
    assert storage.objects[row.photo_key] == PHOTO_A
    assert not any(event.startswith("delete:") for event in storage.events)
    original = (row.id, row.photo_key, row.subject, row.reason, row.barcode)
    retry = await _report(app_client, phone, "ack-lost", PHOTO_B, subject="Different")
    assert retry.status_code == 201 and retry.json() == response.json()
    row = await _row(retry.json()["report_id"])
    assert (row.id, row.photo_key, row.subject, row.reason, row.barcode) == original
    assert storage.objects[row.photo_key] == PHOTO_A and len(storage.puts) == 1


async def test_c_unknown_commit_lookup_preserves_evidence(app_client, db_clean, storage, monkeypatch):
    phone = await _phone(app_client)
    original_commit = AsyncSession.commit

    async def lost_ack(self):
        is_report = any(isinstance(obj, LabelErrorReport) for obj in self.identity_map.values())
        await original_commit(self)
        if is_report:
            raise RuntimeError("commit acknowledgement lost")

    def unavailable_verification():
        raise RuntimeError("verification unavailable")

    monkeypatch.setattr(AsyncSession, "commit", lost_ack)
    monkeypatch.setattr(product_service, "get_sessionmaker", unavailable_verification)
    with pytest.raises(RuntimeError, match="commit acknowledgement lost"):
        await _report(app_client, phone, "unknown-commit")
    [row] = await _rows(client_report_id="unknown-commit")
    assert storage.objects[row.photo_key] == PHOTO_A
    assert len(storage.puts) == 1
    assert not any(event.startswith("delete:") for event in storage.events)


async def test_c_cleanup_failure_keeps_original_failure_and_logs_no_evidence(
    app_client, db_clean, storage, monkeypatch, caplog,
):
    phone = await _phone(app_client)
    original_commit = AsyncSession.commit

    async def failing_commit(self):
        reports = [obj for obj in self.identity_map.values() if isinstance(obj, LabelErrorReport)]
        if reports:
            storage.delete_failures[reports[0].photo_key] = StorageUnavailable("delete unavailable")
            raise RuntimeError("original commit failure")
        return await original_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", failing_commit)
    with pytest.raises(RuntimeError, match="original commit failure"):
        await _report(app_client, phone, "cleanup-failure")
    assert await _rows(client_report_id="cleanup-failure") == []
    assert len(storage.objects) == 1
    assert "label_report_photo_compensation_failed" in caplog.text
    assert all(key not in caplog.text for key in storage.objects)
