"""F04 storage-write ambiguity: events order the late write, not elapsed time."""
from __future__ import annotations

import asyncio
import subprocess
import sys

import httpx
import pytest
from app.domains.media.storage.base import StorageTimeout, StorageUnavailable
from app.domains.media.storage.supabase import SupabaseStorage
from app.domains.product import report_policy as policy
from app.domains.product import report_resources
from app.shared.database import sql
from sqlalchemy import delete, text

from tests.conftest import auth
from tests.image_fixtures import PNG
from tests.test_account_deletion_integrity import _account, _factory, _make_due
from tests.test_audit_lane4_public_resource_safety import _file, _post
from tests.test_label_report_evidence_integrity import _device_id, _phone, _rows
from tests.test_label_report_evidence_integrity import admin as _admin_fixture
from tests.test_label_report_evidence_integrity import events as _events_fixture
from tests.test_label_report_evidence_integrity import storage as _storage_fixture

events = _events_fixture
storage = _storage_fixture
admin = _admin_fixture


async def _resources():
    async with _factory()() as session:
        if not await session.scalar(text("SELECT to_regclass('label_report_resources')")):
            return []
        return (await session.execute(text("SELECT * FROM label_report_resources"))).mappings().all()


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancellation"])
async def test_late_write_after_failure_is_never_untracked(app_client, db_clean, storage, monkeypatch, cancel):
    started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = storage.put
    late = []

    async def put(key, data, content_type):
        async def remote_write():
            started.set()
            await release.wait()
            await original(key, data, content_type)
            completed.set()
        # Represents the provider still processing after the caller abandons
        # its request. The test owns/joins it; production must use DB authority.
        late.append(asyncio.create_task(remote_write()))
        await started.wait()
        if cancel:
            await asyncio.Event().wait()
        raise StorageTimeout("caller deadline; provider still processing")

    monkeypatch.setattr(storage, "put", put)
    phone = await _phone(app_client)
    try:
        if cancel:
            async with _factory()() as session:
                task = asyncio.create_task(_file(session, await _device_id(phone), "late-cancel"))
                await asyncio.wait_for(started.wait(), 10)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await session.rollback()
        else:
            response = await _post(app_client, phone, "late-timeout")
            assert response.status_code == 503
            # A delete-before-completion is not evidence of terminal absence.
            # The corrected path may refrain from that futile deletion.
        assert not storage.objects, "write has not finished yet"
        release.set()
        await asyncio.wait_for(completed.wait(), 10)
        assert not await _rows()
        tracked = await _resources()
        assert not storage.objects or (
            len(tracked) == 1 and tracked[0]["photo_key"] in storage.objects
        ), "a late object exists after cleanup with no durable resource authority"
        assert tracked[0]["photo_byte_size"] == len(PNG)
        # The provider has now visibly completed its one write. A fresh
        # request/transaction can prove exact-key removal and retire authority.
        assert await report_resources.reconcile(tracked[0]["photo_key"])
        assert not storage.objects and not await _resources()
    finally:
        release.set()
        await asyncio.gather(*late, return_exceptions=True)


@pytest.mark.parametrize("scope,limit,expected", [
    ("device", "COUNT", "device_report_count_limit"),
    ("device", "BYTE", "device_report_photo_byte_limit"),
    ("account", "COUNT", "account_report_count_limit"),
    ("account", "BYTE", "account_report_photo_byte_limit"),
])
async def test_unknown_uploads_remain_in_durable_quota_across_restarts(
    app_client, db_clean, storage, monkeypatch, registered_supabase_user, scope, limit, expected,
):
    token = (await registered_supabase_user())[0] if scope == "account" else None
    monkeypatch.setattr(policy, f"{scope.upper()}_REPORT_{limit}_LIMIT", 1 if limit == "COUNT" else len(PNG))
    attempts = []
    async def uncertain(key, data, content_type):
        attempts.append(key)
        raise StorageTimeout("provider outcome unknown")
    monkeypatch.setattr(storage, "put", uncertain)
    phone = await _phone(app_client)
    assert (await _post(app_client, phone, "unknown-first", token=token)).status_code == 503
    first = await _resources()
    assert len(first) == 1 and first[0]["photo_byte_size"] == len(PNG)
    # No in-memory task/map is necessary: destroy the pool and recreate it.
    await sql.dispose_engine()
    for _ in range(3):
        assert (await _post(app_client, phone, "unknown-first", token=token)).status_code == 503
    other_phone = await _phone(app_client) if scope == "account" else phone
    refused = await _post(app_client, other_phone, "unknown-fresh", token=token)
    assert refused.status_code == 429 and refused.json()["detail"]["reason"] == expected
    assert attempts == [first[0]["photo_key"]], "uncertain retry allocated another upload key"
    assert len(await _resources()) == 1 and not await _rows()


async def test_exact_key_cleanup_and_unavailable_reconciliation_keep_authority(app_client, db_clean, storage, monkeypatch):
    original = storage.put
    async def lose_ack(key, data, content_type):
        await original(key, data, content_type)
        storage.delete_failures[key] = StorageUnavailable("provider unavailable")
        raise StorageTimeout("lost acknowledgement")
    monkeypatch.setattr(storage, "put", lose_ack)
    phone = await _phone(app_client)
    assert (await _post(app_client, phone, "unavailable-reconcile")).status_code == 503
    resource = (await _resources())[0]
    key = resource["photo_key"]
    assert key in storage.objects and resource["photo_byte_size"] == len(PNG)
    async def forbidden_listing(*args):
        raise AssertionError("prefix/exists listing is not exact-key proof")
    monkeypatch.setattr(storage, "exists", forbidden_listing)
    monkeypatch.setattr(storage, "list_prefix", forbidden_listing)
    storage.stuck.add(key)
    assert not await report_resources.reconcile(key)
    assert len(await _resources()) == 1 and key in storage.objects
    storage.stuck.clear()
    assert await report_resources.reconcile(key)
    assert not await _resources() and key not in storage.objects


async def test_uncertain_retry_never_allocates_another_key(app_client, db_clean, storage, monkeypatch):
    attempts = []
    async def uncertain(key, data, content_type):
        attempts.append(key)
        raise StorageTimeout("provider outcome unknown")
    monkeypatch.setattr(storage, "put", uncertain)
    phone = await _phone(app_client)
    for _ in range(4):
        assert (await _post(app_client, phone, "one-uncertain-identity")).status_code == 503
    resources = await _resources()
    assert len(attempts) == len(resources) == 1, "uncertain identity allocated more than one resource/key"
    assert resources[0]["photo_key"] == attempts[0]
    assert not await _rows()


async def test_privacy_deletion_retains_unknown_absent_then_erases_late_resource(
    app_client, db_clean, storage, admin, monkeypatch, registered_supabase_user,
):
    from app.workers import account_deletion
    token, account_id = await registered_supabase_user()
    phone = await _phone(app_client, token)
    async def uncertain(key, data, content_type):
        raise StorageTimeout("provider still processing")
    monkeypatch.setattr(storage, "put", uncertain)
    assert (await _post(app_client, phone, "delete-unknown", token=token)).status_code == 503
    key = (await _resources())[0]["photo_key"]
    assert (await app_client.delete("/api/v2/privacy/account", headers=auth(token))).status_code == 202
    result = await account_deletion.run_cycle()
    assert not result.ok and result.error_code == "storage_incomplete"
    assert await _account(account_id) is not None and not admin.deleted
    assert len(await _resources()) == 1, "account cascade discarded unresolved upload authority"
    # The remote operation now completes. The existing deletion worker sees
    # its durable exact key before prefix purge, deletes/proves absence, then
    # proceeds with the ordinary database and Auth lifecycle.
    storage.objects[key] = PNG
    await _make_due(account_id)
    result = await account_deletion.run_cycle()
    assert result.ok, result
    assert not storage.objects and not await _resources()
    assert await _account(account_id) is None and admin.deleted == [str(account_id)]


@pytest.mark.parametrize("cancel", [False, True], ids=["timeout", "cancellation"])
async def test_supabase_report_transport_never_detaches_local_mutation(monkeypatch, cancel):
    from app.domains.media.storage import supabase as adapter
    started, release, terminal = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes = []
    async def transport(request):
        assert request.method == "POST" and request.headers["x-upsert"] == "false"
        assert request.extensions["timeout"]["write"] == adapter._STORAGE_TIMEOUT_SECONDS
        started.set()
        try:
            await release.wait()
            writes.append(request.url.path)
            return httpx.Response(200, json={"Key": "test-bucket/key.png"})
        finally:
            terminal.set()
    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(adapter.httpx, "AsyncClient", client)
    monkeypatch.setattr(adapter, "_STORAGE_TIMEOUT_SECONDS", 0.05 if not cancel else 20.0)
    storage = SupabaseStorage("test-bucket")
    task = asyncio.create_task(storage.put_label_report("key.png", PNG, "image/png"))
    try:
        await asyncio.wait_for(started.wait(), 10)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(StorageTimeout):
                await task
        assert terminal.is_set(), "transport mutation remains alive after timeout/cancellation"
        release.set()
        await asyncio.sleep(0)  # yield only; terminal event is the lifecycle proof
        assert not writes
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_supabase_report_uses_async_sdk_and_exact_info_not_listing(monkeypatch):
    from app.domains.media.storage import supabase as adapter
    calls, objects = [], set()
    async def transport(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            objects.add("key.png")
            return httpx.Response(200, json={"Key": "test-bucket/key.png"})
        if request.method == "DELETE":
            objects.clear()
            return httpx.Response(200, json=[])
        if objects:
            return httpx.Response(200, json={"id": "object-id"})
        return httpx.Response(404, json={"message": "missing", "error": "NotFound", "statusCode": "404"})
    original = httpx.AsyncClient
    monkeypatch.setattr(adapter.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    storage = SupabaseStorage("test-bucket")
    await storage.put_label_report("key.png", PNG, "image/png")
    assert await storage.label_report_exists("key.png")
    await storage.delete_label_report("key.png")
    assert not await storage.label_report_exists("key.png")
    assert calls == [("POST", "/storage/v1/object/test-bucket/key.png"),
                     ("GET", "/storage/v1/object/info/test-bucket/key.png"),
                     ("DELETE", "/storage/v1/object/test-bucket"),
                     ("GET", "/storage/v1/object/info/test-bucket/key.png")]


async def test_real_route_selects_supabase_report_transport(app_client, db_clean, monkeypatch):
    from app.domains.media.storage import factory
    from app.domains.media.storage import supabase as adapter
    uploads = []
    async def transport(request):
        assert request.method == "POST" and request.headers["x-upsert"] == "false"
        uploads.append(request.url.path)
        return httpx.Response(200, json={"Key": request.url.path})
    original = httpx.AsyncClient
    monkeypatch.setattr(adapter.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(transport), **kw))
    storage = SupabaseStorage("test-bucket")
    async def forbidden_thread(*args):
        raise AssertionError("label upload reached the detached-thread API")
    monkeypatch.setattr(storage, "put", forbidden_thread)
    factory.set_storage(storage)
    try:
        phone = await _phone(app_client)
        response = await _post(app_client, phone, "real-async-adapter")
        assert response.status_code == 201, response.text
        assert len(uploads) == 1 and len(await _rows()) == 1 and not await _resources()
    finally:
        factory.set_storage(None)


async def test_uncertain_resource_schema_downgrade_refuses_authority_loss(app_client, db_clean, storage, monkeypatch):
    from app.domains.product.models import LabelReportResource
    async def uncertain(*args):
        raise StorageTimeout("unknown")
    monkeypatch.setattr(storage, "put", uncertain)
    phone = await _phone(app_client)
    assert (await _post(app_client, phone, "populated-downgrade")).status_code == 503
    before = await _resources()
    result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "downgrade", "n2o3p4q5r6"],
                                     capture_output=True, text=True, check=False)
    assert result.returncode != 0 and "durable upload authority cannot be discarded" in result.stderr
    assert [r["photo_key"] for r in await _resources()] == [r["photo_key"] for r in before]
    async with _factory()() as session:
        await session.execute(delete(LabelReportResource))
        await session.commit()
    try:
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "downgrade", "n2o3p4q5r6"],
                                         capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    finally:
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"],
                                         capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    assert not await _resources()
