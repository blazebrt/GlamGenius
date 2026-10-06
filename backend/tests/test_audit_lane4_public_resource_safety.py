"""F04: real PostgreSQL quotas, real multipart boundary and bounded resources.

Race synchronization uses events and observed pg_blocking_pids, never a sleep
as proof that admission serialized. Storage is a fault-injectable adapter, not
a mocked quota/database. Existing commit/deletion invariants are exercised in
test_label_report_evidence_integrity against the same hardened service.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest
from app.api.v2 import product as route
from app.domains.product import report_policy as policy
from app.domains.product import service
from app.domains.product.models import LabelErrorReport
from app.shared.errors.exceptions import MediaTooLargeError, UnsupportedMediaTypeError
from app.shared.security.rate_limit import FixedWindowLimiter
from app.shared.validation.media import validate_upload
from fastapi import HTTPException, Request
from sqlalchemy import text

from tests.conftest import auth
from tests.image_fixtures import JPEG_WHITE, PNG, WEBP
from tests.test_account_deletion_integrity import _factory, _patient, _pid
from tests.test_label_report_evidence_integrity import REPORT, _device_id, _phone, _rows
from tests.test_label_report_evidence_integrity import admin as _admin_fixture
from tests.test_label_report_evidence_integrity import events as _events_fixture
from tests.test_label_report_evidence_integrity import race as _race_fixture
from tests.test_label_report_evidence_integrity import storage as _storage_fixture

# Reuse fixtures locally; do not install an unrelated test module as a global
# pytest plugin or change fixture resolution elsewhere in the full suite.
admin = _admin_fixture
events = _events_fixture
race = _race_fixture
storage = _storage_fixture


async def _post(client, phone, report_id, photo=PNG, *, token=None, declared="image/png"):
    return await client.post(REPORT, headers={**phone, **(auth(token) if token else {})},
                            data={"client_report_id": report_id, "subject": "Sugar", "reason": "wrong_number"},
                            files={"photo": ("untrusted.zip", photo, declared)} if photo is not None else None)


async def _file(session, device_id, report_id, account_id=None):
    return await service.file_label_error_report(session, device_id=device_id, account_id=account_id,
        client_report_id=report_id, subject="Sugar", reason="wrong_number", photo=PNG, photo_content_type="image/png")


async def test_fresh_ids_hit_device_count_and_replay_at_ceiling_is_free(app_client, db_clean, storage, monkeypatch):
    monkeypatch.setattr(policy, "DEVICE_REPORT_COUNT_LIMIT", 1)
    phone = await _phone(app_client)
    first = await _post(app_client, phone, "quota-original")
    assert first.status_code == 201, first.text
    replay = await _post(app_client, phone, "quota-original", JPEG_WHITE, declared="image/jpeg")
    assert replay.status_code == 201 and not replay.json()["created"], replay.text
    assert replay.json()["report_id"] == first.json()["report_id"]
    refused = await _post(app_client, phone, "quota-fresh-id")
    assert refused.status_code == 429, refused.text
    assert refused.json()["detail"]["reason"] == "device_report_count_limit"
    assert len(storage.puts) == len(storage.objects) == len(await _rows()) == 1
    assert next(iter(storage.objects.values())) == PNG


async def test_device_bytes_and_device_isolation(app_client, db_clean, storage, monkeypatch):
    monkeypatch.setattr(policy, "DEVICE_REPORT_BYTE_LIMIT", len(PNG))
    a, b = await _phone(app_client), await _phone(app_client)
    assert (await _post(app_client, a, "device-a-first")).status_code == 201
    refused = await _post(app_client, a, "device-a-second")
    assert refused.status_code == 429 and refused.json()["detail"]["reason"] == "device_report_photo_byte_limit"
    assert (await _post(app_client, b, "device-b-first")).status_code == 201
    assert len(storage.puts) == 2


@pytest.mark.parametrize("ceiling,reason", [("ACCOUNT_REPORT_COUNT_LIMIT", "account_report_count_limit"),
                                           ("ACCOUNT_REPORT_BYTE_LIMIT", "account_report_photo_byte_limit")])
async def test_account_ceiling_spans_devices_but_does_not_own_anonymous_reports(
    app_client, db_clean, registered_supabase_user, storage, monkeypatch, ceiling, reason,
):
    monkeypatch.setattr(policy, ceiling, 1 if "REPORT_COUNT" in ceiling else len(PNG))
    token, account_id = await registered_supabase_user()
    a, b = await _phone(app_client), await _phone(app_client)
    assert (await _post(app_client, a, "account-a-first", token=token)).status_code == 201
    refused = await _post(app_client, b, "account-b-second", token=token)
    assert refused.status_code == 429 and refused.json()["detail"]["reason"] == reason
    assert (await _post(app_client, b, "anonymous-is-device-only")).status_code == 201
    assert len(await _rows(account_id=account_id)) == 1
    assert len(storage.puts) == 2


async def test_legacy_unknown_photo_costs_full_cap_not_zero(app_client, db_clean, storage, monkeypatch):
    monkeypatch.setattr(policy, "DEVICE_REPORT_BYTE_LIMIT", policy.MAX_REPORT_PHOTO_BYTES)
    phone = await _phone(app_client)
    device_id = await _device_id(phone)
    async with _factory()() as session:
        session.add(LabelErrorReport(device_id=device_id, client_report_id="old-unknown-size", subject="Sugar",
                                    reason="wrong_number", photo_key="label-reports/legacy.jpg"))
        await session.commit()
    refused = await _post(app_client, phone, "legacy-budget-full")
    assert refused.status_code == 429, refused.text
    assert not storage.puts and len(await _rows()) == 1


@pytest.mark.parametrize("scope", ["device", "account"])
async def test_two_independent_postgres_transactions_cannot_over_admit(
    app_client, db_clean, registered_supabase_user, storage, race, monkeypatch, scope,
):
    monkeypatch.setattr(policy, f"{scope.upper()}_REPORT_COUNT_LIMIT", 1)
    account_id = (await registered_supabase_user())[1] if scope == "account" else None
    first_device = await _device_id(await _phone(app_client))
    second_device = await _device_id(await _phone(app_client)) if account_id else first_device
    pause = race.pause()
    first, second = _factory()(), _factory()()
    original_admit = policy.admit_report
    async def admit(session, **kwargs):
        await original_admit(session, **kwargs)
        if session is first:
            # Pause after the admitted snapshot but BEFORE its independently
            # durable reservation. A post-reservation storage pause alone can
            # hide a missing quota lock because the new reservation is visible.
            await pause()
    monkeypatch.setattr(policy, "admit_report", admit)
    async def write(session, device_id, report_id):
        try:
            result = await _file(session, device_id, report_id, account_id)
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            raise
    async with first, second:
        await _patient(first)
        await _patient(second)
        first_pid, second_pid = await _pid(first), await _pid(second)
        assert first_pid != second_pid
        one = race.spawn(write(first, first_device, "concurrent-report-one"))
        await asyncio.wait_for(pause.reached.wait(), 10)
        two = race.spawn(write(second, second_device, "concurrent-report-two"))
        # Race ends when PostgreSQL proves the quota lock waits OR the second
        # writer reaches storage (a deterministic serialization regression).
        async def observed_wait():
            for _ in range(10000):
                async with _factory()() as watcher:
                    blockers = await watcher.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid})
                if first_pid in blockers:
                    return
                assert not storage.puts, "second fresh identity reached storage before quota lock released"
                await asyncio.sleep(0)  # scheduler yield; the lock observation is the proof
            raise AssertionError("quota waiter never observed")
        try:
            await asyncio.wait_for(observed_wait(), 10)
        finally:
            pause.release.set()
            # Even when a mutant violates the assertion, finish both writers
            # before closing their sessions; cleanup must not mask the failure.
            await asyncio.gather(one, two, return_exceptions=True)
        await one
        with pytest.raises(policy.ReportQuotaExceeded):
            await two
    assert len(storage.puts) == len(storage.objects) == len(await _rows()) == 1


def test_admission_order_and_no_early_device_flush():
    source = inspect.getsource(service.file_label_error_report)
    body = source[source.index('"""', source.index('"""') + 3):]
    operations = ["lock_label_report_identity(", "_existing_label_report(", "hold_account_active(",
                  "lock_report_quotas(", "admit_report(", "label_report_photo_key(", "report_resources.reserve(",
                  "upload_report_photo(", "session.add(row)", "session.flush()"]
    positions = [body.index(op) for op in operations]
    assert positions == sorted(positions)
    assert "session.flush" not in body[:body.index("admit_report(")]


class _Stream:
    def __init__(self, size):
        self.remaining = size
        self.reads = []
        self.consumed = 0

    async def read(self, size=-1):
        self.reads.append(size)
        amount = self.remaining if size < 0 else min(size, self.remaining)
        self.remaining -= amount
        self.consumed += amount
        return b"x" * amount


async def test_photo_reader_stops_at_cap_plus_one_and_never_requests_unbounded_read(monkeypatch):
    monkeypatch.setattr(route, "MAX_REPORT_PHOTO_BYTES", 73)
    stream = _Stream(10000)
    with pytest.raises(MediaTooLargeError):
        await route._read_report_photo(stream)
    assert stream.consumed == 74
    assert all(0 < n <= policy.REPORT_READ_CHUNK_BYTES for n in stream.reads)
    exact = _Stream(73)
    assert len(await route._read_report_photo(exact)) == 73


@pytest.mark.parametrize("payload,declared", [
    (b"", "image/png"), (b"not a photo at all", "image/jpeg"), (PNG, "image/jpeg"),
    (b"\xff\xd8\xff" + b"x" * 30, "image/jpeg"), (PNG[:24], "image/png"),
    (b"RIFF\x16\x00\x00\x00WEBPVP8X" + b"\x00" * 14, "image/webp"),
    (JPEG_WHITE[:-2], "image/jpeg"), (PNG[:-1], "image/png"), (WEBP[:-1], "image/webp"),
])
async def test_invalid_photo_never_reaches_storage_or_database(app_client, db_clean, storage, payload, declared):
    phone = await _phone(app_client)
    response = await _post(app_client, phone, "invalid-image-report", payload, declared=declared)
    assert response.status_code == 415, response.text
    assert not storage.puts and not await _rows()


@pytest.mark.parametrize("payload,mime,extension", [(PNG, "image/png", ".png"), (JPEG_WHITE, "image/jpeg", ".jpg"), (WEBP, "image/webp", ".webp")])
async def test_actual_mime_exact_size_and_server_extension(app_client, db_clean, storage, monkeypatch, payload, mime, extension):
    writes = []
    original = storage.put
    async def put(key, data, content_type):
        writes.append(content_type)
        await original(key, data, content_type)
    monkeypatch.setattr(storage, "put", put)
    phone = await _phone(app_client)
    response = await _post(app_client, phone, "canonical-mime-report", payload, declared=mime.upper() + "; charset=binary")
    assert response.status_code == 201, response.text
    row = (await _rows())[0]
    assert row.photo_key.endswith(extension) and row.photo_byte_size == len(payload)
    assert writes == [mime]


def _request(peer, forwarded=""):
    return Request({"type": "http", "client": (peer, 123), "headers": [(b"x-forwarded-for", forwarded.encode())]})


def test_report_limiter_uses_trusted_ip_and_is_bounded_independently(monkeypatch):
    from app.shared.security import network
    monkeypatch.setattr(network, "TRUSTED_PROXY_HOPS", 0)
    monkeypatch.setattr(network, "APP_ENV", "test")
    monkeypatch.setattr(policy, "REPORT_IP_RATE_LIMIT", 1)
    monkeypatch.setattr(policy, "REPORT_DEVICE_RATE_LIMIT", 100)
    limiter = FixedWindowLimiter(window_seconds=3600, max_per_window=100, max_keys=4)
    monkeypatch.setattr(policy, "report_limiter", limiter)
    route._limit_label_report(_request("192.0.2.10", "1.1.1.1"), uuid.uuid4(), None)
    with pytest.raises(HTTPException) as caught:
        route._limit_label_report(_request("192.0.2.10", "2.2.2.2"), uuid.uuid4(), None)
    assert caught.value.status_code == 429 and caught.value.detail["reason"] == "ip"
    assert int(caught.value.headers["Retry-After"]) > 0
    route._limit_label_report(_request("192.0.2.11"), uuid.uuid4(), None)
    for n in range(200):
        with pytest.raises(HTTPException):
            route._limit_label_report(_request(f"198.51.100.{n}"), uuid.uuid4(), None)
    assert len(limiter.state) == 4
    assert not route._device_registration_limiter.state


def test_shared_validator_rejects_signature_only_files():
    for data, mime in [(PNG[:24], "image/png"), (JPEG_WHITE[:40], "image/jpeg"), (WEBP[:30], "image/webp")]:
        with pytest.raises(UnsupportedMediaTypeError):
            validate_upload(data, mime)


async def test_privacy_export_includes_exact_bytes_without_storage_path(
    app_client, db_clean, registered_supabase_user, storage,
):
    from app.domains.privacy.export import _label_error_report_row
    token, _ = await registered_supabase_user()
    phone = await _phone(app_client)
    assert (await _post(app_client, phone, "export-exact-size", token=token)).status_code == 201
    row = (await _rows())[0]
    exported = _label_error_report_row(row)
    assert exported["photo_byte_size"] == len(PNG) and exported["photo_attached"] is True
    assert "photo_key" not in exported and row.photo_key not in str(exported)


async def test_real_privacy_deletion_erases_canonical_report_objects(
    app_client, db_clean, registered_supabase_user, storage, admin,
):
    from app.workers import account_deletion
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    assert (await _post(app_client, phone, "delete-canonical-png", token=token)).status_code == 201
    assert (await _post(app_client, phone, "delete-canonical-webp", WEBP, token=token, declared="image/webp")).status_code == 201
    assert len(storage.objects) == 2
    response = await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    assert response.status_code == 202, response.text
    result = await account_deletion.run_cycle()
    assert result.ok, result
    assert not storage.objects and not await _rows(account_id=account_id)
    assert admin.deleted


@pytest.mark.parametrize("scope", ["device", "account"])
def test_rate_limit_cannot_be_evaded_by_changing_ip(scope, monkeypatch):
    monkeypatch.setattr(policy, "REPORT_DEVICE_RATE_LIMIT", 1 if scope == "device" else 20)
    monkeypatch.setattr(policy, "REPORT_ACCOUNT_RATE_LIMIT", 1)
    device, account = uuid.uuid4(), uuid.uuid4() if scope == "account" else None
    route._limit_label_report(_request("192.0.2.40"), device, account)
    with pytest.raises(HTTPException) as caught:
        route._limit_label_report(_request("192.0.2.41"), device if scope == "device" else uuid.uuid4(), account)
    assert caught.value.detail["reason"] == scope


async def test_oversized_photo_refused_before_any_storage_or_row(app_client, db_clean, storage, monkeypatch):
    monkeypatch.setattr(route, "MAX_REPORT_PHOTO_BYTES", len(PNG) - 1)
    phone = await _phone(app_client)
    response = await _post(app_client, phone, "oversize-photo-report")
    assert response.status_code == 413, response.text
    assert not storage.puts and not await _rows()


@pytest.mark.parametrize("cleanup_available", [True, False], ids=["cleanup-available", "cleanup-unavailable"])
async def test_storage_put_acknowledgement_failure_compensates_already_written_object(
    app_client, db_clean, storage, monkeypatch, caplog, cleanup_available,
):
    from app.domains.media.storage.base import StorageUnavailable

    original = storage.put

    async def put_then_lose_acknowledgement(key, data, content_type):
        await original(key, data, content_type)
        if not cleanup_available:
            storage.delete_failures[key] = StorageUnavailable("cleanup temporarily unavailable")
        raise StorageUnavailable("upload acknowledgement lost")

    monkeypatch.setattr(storage, "put", put_then_lose_acknowledgement)
    phone = await _phone(app_client)
    response = await _post(app_client, phone, "put-acknowledgement-lost")
    assert response.status_code == 503, response.text
    assert len(storage.puts) == 1 and not await _rows()
    if cleanup_available:
        assert not storage.objects, "a completed object write must not be orphaned by a failed upload acknowledgement"
    else:
        assert len(storage.objects) == 1
        assert "label_report_photo_compensation_failed" in caplog.text
