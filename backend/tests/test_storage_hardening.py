"""Supabase storage adapter — hardening tests.

The Supabase SDK is mocked so tests are deterministic and never hit a real
provider. Focus is on failure classification: a missing object must not look
like an outage, an outage must not look like a missing object, and neither
must silently succeed.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.domains.media.storage.base import (
    StorageInvalidResponse,
    StorageMisconfigured,
    StorageObjectMissing,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
    account_prefix,
    build_key,
)
from app.domains.media.storage.supabase import SupabaseStorage


pytestmark = pytest.mark.asyncio


class _FakeBucket:
    """In-memory Supabase storage double.

    Mirrors the shape of ``supabase.storage.from_(bucket)``: it exposes
    ``upload``, ``download``, ``remove``, ``list`` and ``create_signed_url``.
    Every method can be told to raise or hang so the classifier tests can
    cover the full error tree.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.upload_error: Exception | None = None
        self.download_error: Exception | None = None
        self.remove_error: Exception | None = None
        self.signed_url_error: Exception | None = None
        self.hang_seconds: float = 0.0

    def _maybe_hang(self) -> None:
        if self.hang_seconds:
            # Blocking sleep because these methods run in a worker thread.
            import time
            time.sleep(self.hang_seconds)

    def upload(self, path: str, file: bytes, file_options: dict | None = None) -> None:
        self._maybe_hang()
        if self.upload_error:
            raise self.upload_error
        self.objects[path] = bytes(file)

    def download(self, path: str) -> bytes:
        self._maybe_hang()
        if self.download_error:
            raise self.download_error
        if path not in self.objects:
            raise Exception("Object not found")
        return self.objects[path]

    def remove(self, keys: list[str]) -> None:
        self._maybe_hang()
        if self.remove_error:
            raise self.remove_error
        for k in keys:
            self.objects.pop(k, None)

    def list(self, path: str = "", options: dict | None = None) -> list[dict]:
        options = options or {}
        limit = options.get("limit", 100)
        offset = options.get("offset", 0)
        prefix = f"{path}/" if path else ""
        subs: dict[str, None] = {}
        files: list[str] = []
        for key in sorted(self.objects.keys()):
            if not key.startswith(prefix):
                continue
            remainder = key[len(prefix):]
            if "/" in remainder:
                subs[remainder.split("/", 1)[0]] = None
            else:
                files.append(remainder)
        result = [{"name": name, "id": None, "metadata": None} for name in subs]
        result += [{"name": name, "id": str(uuid.uuid4()), "metadata": {"size": len(self.objects[f"{prefix}{name}"])}} for name in files]
        return result[offset:offset + limit]

    def create_signed_url(self, path: str, ttl: int) -> dict:
        if self.signed_url_error:
            raise self.signed_url_error
        return {"signedURL": f"https://signed.example/{path}?ttl={ttl}"}


@pytest.fixture
def fake_bucket(monkeypatch):
    bucket = _FakeBucket()

    class _FakeAdmin:
        class storage:
            @staticmethod
            def from_(_name):
                return bucket

    monkeypatch.setattr(
        "app.domains.media.storage.supabase.get_supabase_admin",
        lambda: _FakeAdmin,
    )
    return bucket


@pytest.fixture
def storage(fake_bucket):
    return SupabaseStorage(bucket="test-bucket")


def _mk_403():
    err = Exception("permission denied")
    err.status_code = 403
    return err


def _mk_404():
    err = Exception("object not found")
    err.status_code = 404
    return err


def _mk_503():
    err = Exception("service unavailable")
    err.status_code = 503
    return err


async def test_upload_and_get_roundtrip(storage):
    key = "media/deadbeef/asset.jpg"
    await storage.put(key, b"bytes", "image/jpeg")
    got = await storage.get(key)
    assert got == b"bytes"


async def test_missing_object_raises_object_missing(storage):
    with pytest.raises(StorageObjectMissing):
        await storage.get("media/none/none.jpg")


async def test_unauthorized_is_classified(storage, fake_bucket):
    fake_bucket.upload_error = _mk_403()
    with pytest.raises(StorageUnauthorized):
        await storage.put("k", b"x", "image/jpeg")


async def test_unavailable_is_classified(storage, fake_bucket):
    fake_bucket.upload_error = _mk_503()
    with pytest.raises(StorageUnavailable):
        await storage.put("k", b"x", "image/jpeg")


async def test_timeout_becomes_storage_timeout(storage, fake_bucket, monkeypatch):
    # Force the wrapper's timeout to fire immediately.
    monkeypatch.setattr(
        "app.domains.media.storage.supabase._STORAGE_TIMEOUT_SECONDS", 0.01
    )
    fake_bucket.hang_seconds = 0.5
    with pytest.raises(StorageTimeout):
        await storage.put("k", b"x", "image/jpeg")


async def test_invalid_response_from_signed_url_is_classified(storage, fake_bucket):
    fake_bucket.signed_url_error = None
    # Force the method to return an unexpected shape.
    fake_bucket.create_signed_url = lambda path, ttl: {"unexpected": "shape"}  # type: ignore[assignment]
    with pytest.raises(StorageInvalidResponse):
        await storage.presigned_get_url("k", 60)


async def test_signed_url_ttl_is_clamped(storage):
    # 24 hours would be a misconfiguration; the adapter clamps it to 900 s.
    url = await storage.presigned_get_url("media/x/y.jpg", 86_400)
    assert url is not None and "ttl=900" in url


async def test_signed_url_ttl_minimum_is_enforced(storage):
    url = await storage.presigned_get_url("media/x/y.jpg", 1)
    assert url is not None and "ttl=30" in url


async def test_list_prefix_walks_pages(storage):
    account = uuid.uuid4()
    prefix = account_prefix(account)
    for i in range(5):
        await storage.put(f"{prefix}/asset-{i}.jpg", b"x", "image/jpeg")
    keys = list(await storage.list_prefix(prefix))
    assert len(keys) == 5


async def test_delete_prefix_empties_the_account(storage):
    account = uuid.uuid4()
    prefix = account_prefix(account)
    other = uuid.uuid4()
    other_prefix = account_prefix(other)
    for i in range(3):
        await storage.put(f"{prefix}/asset-{i}.jpg", b"x", "image/jpeg")
    await storage.put(f"{other_prefix}/keep-me.jpg", b"x", "image/jpeg")

    removed = await storage.delete_prefix(prefix)
    assert removed == 3
    remaining = list(await storage.list_prefix(prefix))
    assert remaining == []
    # Other account's data is untouched.
    other_keys = list(await storage.list_prefix(other_prefix))
    assert len(other_keys) == 1


async def test_bucket_config_missing_raises_misconfigured(monkeypatch):
    # Force both the argument and the env-default to be empty.
    monkeypatch.setattr(
        "app.domains.media.storage.supabase.SUPABASE_STORAGE_BUCKET", ""
    )
    with pytest.raises(StorageMisconfigured):
        SupabaseStorage(bucket="")


async def test_delete_missing_object_is_not_an_error(storage):
    """Removing a key that isn't there is treated as success in the SDK."""
    # ``remove`` is a no-op in the fake for missing keys — the test proves the
    # adapter does not propagate a spurious error.
    await storage.delete("media/nonexistent/nowhere.jpg")


async def test_build_key_shards_by_account():
    account = uuid.uuid4()
    asset = uuid.uuid4()
    key = build_key(account, asset, "image/jpeg")
    assert key.startswith(f"media/{account}/")
    assert key.endswith(".jpg")
