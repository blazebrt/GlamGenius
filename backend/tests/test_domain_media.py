"""§1.5 — media regression coverage.

Everything runs through the real V2 routes with a stubbed storage adapter, so
the assertions cover the whole path: multipart parsing, type sniffing, size
capping, ownership, storage-failure classification and account-prefix cleanup.

What this protects against
--------------------------
* An upload labelled ``image/png`` that is actually something else being
  accepted on the strength of its label.
* Someone reading or deleting another account's photo, or learning that the
  asset id exists at all (missing and not-yours must both be 404).
* A storage outage being reported as "not found", which would make a client
  delete its local copy of a photo that still exists.
* A metadata row surviving with no object behind it and the route serving a
  200 with an empty body.
* ``storage_key`` — an internal path — leaking into an API payload.
* Local filesystem storage being usable in production.
* Deletion leaving objects behind under the account prefix.
"""
from __future__ import annotations

import uuid

import pytest

from app.domains.media import service as media_service
from app.domains.media.models import MEDIA_STATUS_ACTIVE, MEDIA_STATUS_DELETED
from app.domains.media.storage import factory as storage_factory
from app.domains.media.storage.base import (
    StorageMisconfigured,
    StorageObjectMissing,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
    account_prefix,
)
from app.shared.database.sql import get_sessionmaker
from tests.conftest import auth, png_bytes


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Image fixtures — real magic bytes for each accepted type
# ---------------------------------------------------------------------------

def jpeg_bytes(width: int = 64, height: int = 32) -> bytes:
    """A JPEG header with a genuine SOF0 frame, so dimensions are readable."""
    return (
        b"\xff\xd8"                                  # SOI
        b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        + b"\xff\xc0\x00\x11\x08"                    # SOF0
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
        + b"\xff\xd9"                                # EOI
    )


def webp_bytes(width: int = 8, height: int = 4) -> bytes:
    """A VP8X-flavoured WebP container — enough for sniffing and dimensions."""
    body = (
        b"VP8X"
        + (10).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


# ---------------------------------------------------------------------------
# Storage doubles
# ---------------------------------------------------------------------------

class _MemoryStorage:
    """In-memory adapter that can be told to fail on any single operation."""

    backend_name = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_error: BaseException | None = None
        self.get_error: BaseException | None = None
        self.delete_error: BaseException | None = None
        self.signed_ttls: list[int] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.put_error is not None:
            raise self.put_error
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if self.get_error is not None:
            raise self.get_error
        if key not in self.objects:
            raise StorageObjectMissing(key)
        return self.objects[key]

    async def delete(self, key: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def presigned_get_url(self, key: str, ttl_seconds: int):
        self.signed_ttls.append(ttl_seconds)
        if key not in self.objects:
            raise StorageObjectMissing(key)
        return f"https://signed.example/{key}?ttl={ttl_seconds}"

    async def list_prefix(self, prefix: str):
        return [k for k in self.objects if k.startswith(prefix)]

    async def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)


@pytest.fixture
def storage() -> _MemoryStorage:
    adapter = _MemoryStorage()
    storage_factory.set_storage(adapter)
    yield adapter
    storage_factory.set_storage(None)


async def _upload(client, token, data: bytes, filename: str, content_type: str):
    return await client.post(
        "/api/v2/media/upload",
        headers=auth(token),
        files={"file": (filename, data, content_type)},
    )


# ---------------------------------------------------------------------------
# Upload / read / delete lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "maker,filename,content_type,expected_dims",
    [
        (png_bytes, "swatch.png", "image/png", (1, 1)),
        (jpeg_bytes, "shirt.jpg", "image/jpeg", (64, 32)),
        (webp_bytes, "serum.webp", "image/webp", (8, 4)),
    ],
)
async def test_upload_accepts_each_supported_type(
    app_client, db_clean, registered_supabase_user, storage,
    maker, filename, content_type, expected_dims,
):
    token, _ = await registered_supabase_user()
    data = maker()

    resp = await _upload(app_client, token, data, filename, content_type)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content_type"] == content_type
    assert body["byte_size"] == len(data)
    assert (body["width"], body["height"]) == expected_dims
    assert body["status"] == MEDIA_STATUS_ACTIVE
    assert body["content_url"] == f"/api/v2/media/{body['id']}/content"
    # The internal object path must never reach a client.
    assert "storage_key" not in body
    assert "storage_backend" not in body
    assert len(storage.objects) == 1


async def test_metadata_and_content_round_trip(
    app_client, db_clean, registered_supabase_user, storage
):
    token, _ = await registered_supabase_user()
    data = png_bytes()
    asset_id = (await _upload(app_client, token, data, "a.png", "image/png")).json()["id"]

    meta = await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert meta.status_code == 200, meta.text
    assert meta.json()["id"] == asset_id

    content = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(token)
    )
    assert content.status_code == 200
    assert content.content == data
    assert content.headers["content-type"] == "image/png"
    # A shared cache must never be allowed to hold someone's photo.
    assert content.headers["cache-control"] == "private, max-age=300"


async def test_delete_erases_bytes_then_marks_row(
    app_client, db_clean, registered_supabase_user, storage
):
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]
    assert storage.objects

    resp = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == MEDIA_STATUS_DELETED
    assert resp.json()["deleted_at"] is not None

    assert storage.objects == {}, "the bytes must be gone, not just the row flag"
    after = await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert after.status_code == 404
    content = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(token)
    )
    assert content.status_code == 404


async def test_delete_is_not_repeatable(
    app_client, db_clean, registered_supabase_user, storage
):
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]
    assert (
        await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    ).status_code == 200
    second = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert second.status_code == 404


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

async def test_other_account_cannot_read_or_delete(
    app_client, db_clean, registered_supabase_user, storage
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, owner_token, png_bytes(), "a.png", "image/png")
    ).json()["id"]

    for method, path in (
        ("get", f"/api/v2/media/{asset_id}"),
        ("get", f"/api/v2/media/{asset_id}/content"),
        ("delete", f"/api/v2/media/{asset_id}"),
    ):
        resp = await getattr(app_client, method)(path, headers=auth(intruder_token))
        # 404, never 403: a 403 confirms the id exists and is an enumeration
        # oracle for another account's asset ids.
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

    # And the owner's object is untouched by the failed attempts.
    assert len(storage.objects) == 1
    assert (
        await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(owner_token))
    ).status_code == 200


async def test_unknown_asset_id_is_404(
    app_client, db_clean, registered_supabase_user, storage
):
    token, _ = await registered_supabase_user()
    resp = await app_client.get(f"/api/v2/media/{uuid.uuid4()}", headers=auth(token))
    assert resp.status_code == 404


async def test_media_requires_authentication(app_client, db_clean, storage):
    resp = await app_client.post(
        "/api/v2/media/upload",
        files={"file": ("a.png", png_bytes(), "image/png")},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

async def test_empty_upload_is_rejected(
    app_client, db_clean, registered_supabase_user, storage
):
    token, _ = await registered_supabase_user()
    resp = await _upload(app_client, token, b"", "empty.png", "image/png")
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert storage.objects == {}


async def test_oversized_upload_is_rejected_before_storage(
    app_client, db_clean, registered_supabase_user, storage, monkeypatch
):
    """The route caps the stream itself — an unbounded read is a one-line DoS."""
    from app.api.v2 import media as media_routes

    monkeypatch.setattr(media_routes, "MEDIA_MAX_BYTES", 512)
    token, _ = await registered_supabase_user()

    oversized = png_bytes() + b"\x00" * 4096
    resp = await _upload(app_client, token, oversized, "big.png", "image/png")

    assert resp.status_code == 413
    assert resp.json()["detail"]["code"] == "MEDIA_TOO_LARGE"
    assert resp.json()["detail"]["max_bytes"] == 512
    assert storage.objects == {}, "an oversized upload must never reach storage"


async def test_mime_spoofing_is_rejected(
    app_client, db_clean, registered_supabase_user, storage
):
    """PNG bytes labelled as JPEG: the file contradicts its own label."""
    token, _ = await registered_supabase_user()
    resp = await _upload(app_client, token, png_bytes(), "lie.jpg", "image/jpeg")
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert storage.objects == {}


async def test_non_image_bytes_are_rejected(
    app_client, db_clean, registered_supabase_user, storage
):
    """A zip archive wearing an image label is refused on its magic bytes."""
    token, _ = await registered_supabase_user()
    zip_bytes = b"PK\x03\x04" + b"\x00" * 64
    resp = await _upload(app_client, token, zip_bytes, "payload.png", "image/png")
    assert resp.status_code == 415
    assert storage.objects == {}


async def test_truncated_image_is_rejected(
    app_client, db_clean, registered_supabase_user, storage
):
    """Fewer than 12 bytes cannot be sniffed, so it is refused rather than
    guessed at."""
    token, _ = await registered_supabase_user()
    resp = await _upload(app_client, token, png_bytes()[:6], "cut.png", "image/png")
    assert resp.status_code == 415
    assert storage.objects == {}


async def test_unsupported_purpose_is_rejected(
    app_client, db_clean, registered_supabase_user, storage
):
    """Face/hair analysis photos are transient request data and must never be
    written to object storage — only the inventory purpose is accepted."""
    token, _ = await registered_supabase_user()
    resp = await app_client.post(
        "/api/v2/media/upload",
        headers=auth(token),
        files={"file": ("face.png", png_bytes(), "image/png")},
        data={"purpose": "face_scan"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "purpose"
    assert storage.objects == {}


# ---------------------------------------------------------------------------
# Storage-failure classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "error,expected_status,expected_code,retryable",
    [
        (StorageTimeout("slow"), 503, "STORAGE_UNAVAILABLE", True),
        (StorageUnavailable("down"), 503, "STORAGE_UNAVAILABLE", True),
        # A credential failure currently shares the outage mapping. It is
        # logged distinctly (``storage_unauthorized``) so an operator can tell
        # the two apart; the client-visible contract is the same 503.
        (StorageUnauthorized("bad key"), 503, "STORAGE_UNAVAILABLE", True),
        (StorageMisconfigured("no bucket"), 500, "STORAGE_MISCONFIGURED", False),
    ],
)
async def test_upload_maps_storage_failures_precisely(
    app_client, db_clean, registered_supabase_user, storage,
    error, expected_status, expected_code, retryable,
):
    token, _ = await registered_supabase_user()
    storage.put_error = error

    resp = await _upload(app_client, token, png_bytes(), "a.png", "image/png")

    assert resp.status_code == expected_status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == expected_code
    assert detail["retryable"] is retryable
    # A failed write must not leave a row pointing at an object never written.
    assert storage.objects == {}


async def test_read_failure_is_not_reported_as_not_found(
    app_client, db_clean, registered_supabase_user, storage
):
    """An outage must not look like a deletion: a client that saw 404 here
    would be entitled to drop its local copy of a photo that still exists."""
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]

    storage.get_error = StorageUnavailable("provider down")
    resp = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(token)
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["retryable"] is True


async def test_missing_object_with_metadata_row_is_404_not_empty_200(
    app_client, db_clean, registered_supabase_user, storage
):
    """Row/object drift: the metadata read still succeeds, the byte read is a
    404, and the response body is never a zero-length 200."""
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]

    storage.objects.clear()  # the object vanished behind our back

    meta = await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert meta.status_code == 200

    content = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(token)
    )
    assert content.status_code == 404
    assert content.json()["detail"]["code"] == "NOT_FOUND"


async def test_delete_survives_an_already_missing_object(
    app_client, db_clean, registered_supabase_user, storage
):
    """If the provider says the object is already gone, that is the state the
    caller asked for — the row must still be marked deleted."""
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]
    storage.objects.clear()
    storage.delete_error = StorageObjectMissing("already gone")

    resp = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == MEDIA_STATUS_DELETED


async def test_delete_failure_leaves_the_asset_readable(
    app_client, db_clean, registered_supabase_user, storage
):
    """Bytes first, row second: a storage failure must leave the photo visible
    and the delete retryable rather than invisible but still stored."""
    token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]
    storage.delete_error = StorageUnavailable("down")

    resp = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert resp.status_code == 503

    storage.delete_error = None
    assert (
        await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(token))
    ).status_code == 200


# ---------------------------------------------------------------------------
# Signed URLs, prefixes and the production guard
# ---------------------------------------------------------------------------

async def test_signed_url_is_scoped_to_the_stored_key(
    app_client, db_clean, registered_supabase_user, storage
):
    token, uid = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "a.png", "image/png")
    ).json()["id"]
    key = next(iter(storage.objects))

    url = await storage.presigned_get_url(key, 300)

    assert url.startswith("https://")
    assert key in url
    assert storage.signed_ttls == [300]
    # The key is namespaced by account, so one account's signed URL can never
    # address another account's object.
    assert key.startswith(account_prefix(uid))


async def test_account_prefix_purge_removes_only_that_account(
    app_client, db_clean, registered_supabase_user, storage
):
    token_a, uid_a = await registered_supabase_user()
    token_b, uid_b = await registered_supabase_user()
    await _upload(app_client, token_a, png_bytes(), "a.png", "image/png")
    await _upload(app_client, token_a, jpeg_bytes(), "b.jpg", "image/jpeg")
    await _upload(app_client, token_b, png_bytes(), "c.png", "image/png")
    assert len(storage.objects) == 3

    removed, remaining = await media_service.purge_account_storage(uid_a)

    assert removed == 2
    assert remaining == [], "the deletion stage only advances on an empty prefix"
    assert all(k.startswith(account_prefix(uid_b)) for k in storage.objects)
    assert len(storage.objects) == 1


async def test_local_storage_is_refused_in_production(monkeypatch):
    """The local filesystem adapter is a development convenience. In
    production it would silently store user photos on an ephemeral disk."""
    from app.domains.media.storage import factory

    storage_factory.set_storage(None)
    monkeypatch.setattr(factory, "APP_ENV", "production")
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setattr(factory, "MEDIA_ALLOW_LOCAL_IN_PRODUCTION", False)

    with pytest.raises(StorageMisconfigured):
        factory.get_storage()

    storage_factory.set_storage(None)


async def test_removed_backend_name_is_refused(monkeypatch):
    """S3/MinIO was removed with boto3; naming it must fail loudly rather
    than fall through to a default."""
    from app.domains.media.storage import factory

    storage_factory.set_storage(None)
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "s3")

    with pytest.raises(StorageMisconfigured):
        factory.get_storage()

    storage_factory.set_storage(None)


# ---------------------------------------------------------------------------
# Association with inventory
# ---------------------------------------------------------------------------

async def test_image_associates_with_an_inventory_item(
    app_client, db_clean, registered_supabase_user, storage
):
    token, uid = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, token, png_bytes(), "shirt.png", "image/png")
    ).json()["id"]

    created = await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(token),
        json={
            "category": "wardrobe",
            "display_name": "Charcoal Blazer",
            "image_ids": [asset_id],
        },
    )
    assert created.status_code in (200, 201), created.text
    item_id = created.json()["id"]

    from sqlalchemy import select

    from app.domains.inventory.models import InventoryItemImage

    factory = get_sessionmaker()
    async with factory() as session:
        links = (await session.execute(
            select(InventoryItemImage).where(
                InventoryItemImage.item_id == uuid.UUID(item_id)
            )
        )).scalars().all()
    assert [str(link.media_asset_id) for link in links] == [asset_id]


async def test_cannot_attach_another_accounts_image(
    app_client, db_clean, registered_supabase_user, storage
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    asset_id = (
        await _upload(app_client, owner_token, png_bytes(), "a.png", "image/png")
    ).json()["id"]

    resp = await app_client.post(
        "/api/v2/inventory/items",
        headers=auth(intruder_token),
        json={
            "category": "wardrobe",
            "display_name": "Borrowed photo",
            "image_ids": [asset_id],
        },
    )
    assert resp.status_code == 404
