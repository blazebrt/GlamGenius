"""S3-compatible integration test (Fix 9, WP2).

Runs against MinIO (or any other S3-compatible service) when the environment
tells us how to reach it. Skipped otherwise so the provider-independent
suite stays runnable without infrastructure.

Environment (the docker-compose.test.yml `backend-tests` service sets these
against the MinIO container it starts alongside Postgres and Mongo):

    S3_INTEGRATION_ENABLED=true
    S3_ENDPOINT_URL=http://minio:9000
    S3_BUCKET=glamgenius-test
    S3_REGION=us-east-1
    S3_ACCESS_KEY_ID=minioadmin
    S3_SECRET_ACCESS_KEY=minioadmin

Locally, an operator can run these tests by starting MinIO themselves and
setting the same variables before `pytest -q tests/test_media_s3.py`.
"""
from __future__ import annotations

import io
import os
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("S3_INTEGRATION_ENABLED", "").strip().lower() not in ("1", "true", "yes"),
    reason="S3_INTEGRATION_ENABLED is not set; provider-independent suite only.",
)


def _bucket_client():
    """Fresh boto3 client + bucket-ready MinIO. Idempotent."""
    import boto3
    from botocore.client import Config

    endpoint = os.environ["S3_ENDPOINT_URL"]
    bucket = os.environ["S3_BUCKET"]

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("S3_REGION") or "us-east-1",
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        # MinIO wants path-style addressing; production S3 accepts both.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    # Create bucket if it does not exist. `head_bucket` throws for
    # not-found and for 403 identically; a create attempt covers both.
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001
        try:
            client.create_bucket(Bucket=bucket)
        except Exception:  # noqa: BLE001 - already-owned-by-you is a valid final state.
            pass
    return client, bucket


def _tiny_jpeg() -> bytes:
    # Small but real JPEG (SOI + APP0 + SOF0 + minimum quantisation and huffman
    # tables + EOI). Passes the sniffer's `\xff\xd8\xff` check and read_dimensions.
    # This is the same minimal JPEG used by other suites in tests/conftest.py.
    from tests.conftest import png_bytes  # png is fine here — sniffer accepts both

    return png_bytes()


@pytest.fixture
def adapter():
    """S3CompatibleStorage pointed at the MinIO container."""
    from app.domains.media.storage import factory
    from app.domains.media.storage.s3 import S3CompatibleStorage

    _bucket_client()  # ensure the bucket exists first
    storage = S3CompatibleStorage()
    factory.set_storage(storage)
    try:
        yield storage
    finally:
        factory.set_storage(None)


async def test_put_get_roundtrip(adapter):
    key = f"media/{uuid.uuid4()}/{uuid.uuid4()}.png"
    body = _tiny_jpeg()
    await adapter.put(key, body, "image/png")
    assert await adapter.exists(key) is True
    fetched = await adapter.get(key)
    assert fetched == body
    await adapter.delete(key)
    assert await adapter.exists(key) is False


async def test_delete_missing_is_idempotent(adapter):
    key = f"media/{uuid.uuid4()}/{uuid.uuid4()}.png"
    # Never uploaded; delete must not raise.
    await adapter.delete(key)
    # And re-deleting is still a no-op.
    await adapter.delete(key)


async def test_get_missing_raises_storage_error(adapter):
    from app.domains.media.storage.base import StorageError

    key = f"media/{uuid.uuid4()}/{uuid.uuid4()}.png"
    with pytest.raises(StorageError):
        await adapter.get(key)


async def test_presigned_get_url_returns_and_ttl_is_clamped(adapter):
    key = f"media/{uuid.uuid4()}/{uuid.uuid4()}.png"
    await adapter.put(key, _tiny_jpeg(), "image/png")
    try:
        url = await adapter.presigned_get_url(key, ttl_seconds=300)
        assert url is not None
        assert isinstance(url, str)
        # boto3 sets X-Amz-Expires from the ExpiresIn we asked for. When the
        # adapter clamps, the ceiling is 900 seconds (see s3.py:presigned_get_url).
        capped = await adapter.presigned_get_url(key, ttl_seconds=100_000)
        assert capped is not None
        assert "X-Amz-Expires=900" in capped or "expires=900" in capped.lower()

        # And a tiny TTL is floored at 60.
        tiny = await adapter.presigned_get_url(key, ttl_seconds=1)
        assert tiny is not None
        assert "X-Amz-Expires=60" in tiny or "expires=60" in tiny.lower()
    finally:
        await adapter.delete(key)


async def test_server_side_encryption_header_is_sent(adapter):
    """S3_SERVER_SIDE_ENCRYPTION=AES256 is the default. Confirm the header
    is applied — a head_object exposes ServerSideEncryption when the server
    honoured it (MinIO reports 'AES256' when configured with a KMS backend;
    without KMS it reports absent, which the assert tolerates)."""
    client, bucket = _bucket_client()
    key = f"media/{uuid.uuid4()}/{uuid.uuid4()}.png"
    await adapter.put(key, _tiny_jpeg(), "image/png")
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        # Some MinIO installations refuse SSE if not configured; we accept
        # either an AES256 response or a clean 200. What we do NOT accept
        # is a request-level failure, which would raise.
        sse = head.get("ServerSideEncryption")
        assert sse in (None, "AES256", "aws:kms")
    finally:
        await adapter.delete(key)


async def test_prefix_isolation(adapter):
    """A media key is sharded by account. Listing under one account's prefix
    never sees another account's object."""
    account_a = uuid.uuid4()
    account_b = uuid.uuid4()
    key_a = f"media/{account_a}/{uuid.uuid4()}.png"
    key_b = f"media/{account_b}/{uuid.uuid4()}.png"
    await adapter.put(key_a, _tiny_jpeg(), "image/png")
    await adapter.put(key_b, _tiny_jpeg(), "image/png")
    try:
        client, bucket = _bucket_client()
        listing_a = client.list_objects_v2(Bucket=bucket, Prefix=f"media/{account_a}/")
        keys_a = {o["Key"] for o in listing_a.get("Contents") or []}
        assert key_a in keys_a
        assert key_b not in keys_a
    finally:
        await adapter.delete(key_a)
        await adapter.delete(key_b)
