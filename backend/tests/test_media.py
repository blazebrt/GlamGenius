"""Media upload, validation, ownership and deletion."""
from __future__ import annotations

import pytest

from tests.conftest import auth, png_bytes


def _file(data: bytes, name: str = "photo.png", content_type: str = "image/png"):
    return {"file": (name, data, content_type)}


async def _upload(client, token, data=None, content_type="image/png", purpose="inventory_item"):
    return await client.post(
        "/api/v2/media/upload",
        files=_file(data if data is not None else png_bytes(), content_type=content_type),
        data={"purpose": purpose},
        headers=auth(token),
    )


# --- Happy path -------------------------------------------------------------


async def test_upload_returns_metadata_without_leaking_the_storage_path(
    app_client, make_user, media_root
):
    _, token = await make_user()
    response = await _upload(app_client, token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["content_type"] == "image/png"
    assert body["byte_size"] > 0
    assert body["width"] == 1 and body["height"] == 1
    assert body["status"] == "active"

    # The audit called this out explicitly.
    assert "storage_key" not in body
    assert "storage_backend" not in body
    for value in body.values():
        assert "/data/media" not in str(value)


async def test_uploaded_bytes_come_back_unchanged(app_client, make_user, media_root):
    _, token = await make_user()
    asset_id = (await _upload(app_client, token)).json()["id"]

    response = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(token)
    )
    assert response.status_code == 200
    assert response.content == png_bytes()
    assert response.headers["content-type"].startswith("image/png")


# --- Validation -------------------------------------------------------------


async def test_non_image_upload_is_rejected(app_client, make_user, media_root):
    _, token = await make_user()
    response = await _upload(
        app_client, token, data=b"PK\x03\x04 this is a zip", content_type="image/png"
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_content_type_that_contradicts_the_bytes_is_rejected(
    app_client, make_user, media_root
):
    """A real PNG labelled as a JPEG. The label is caller-supplied, so we sniff."""
    _, token = await make_user()
    response = await _upload(app_client, token, content_type="image/jpeg")

    assert response.status_code == 415


async def test_empty_upload_is_rejected(app_client, make_user, media_root):
    _, token = await make_user()
    response = await _upload(app_client, token, data=b"")

    assert response.status_code == 415


async def test_oversized_upload_is_rejected(app_client, make_user, media_root, monkeypatch):
    import app.api.v2.media as media_routes

    monkeypatch.setattr(media_routes, "MEDIA_MAX_BYTES", 64)
    _, token = await make_user()

    response = await _upload(app_client, token, data=png_bytes() * 200)

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "MEDIA_TOO_LARGE"


async def test_unknown_purpose_is_rejected(app_client, make_user, media_root):
    _, token = await make_user()
    response = await _upload(app_client, token, purpose="something_else")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"


async def test_analysis_photos_cannot_enter_media_storage(
    app_client, make_user, media_root
):
    _, token = await make_user()
    response = await _upload(app_client, token, purpose="photo_analysis")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_FAILED"
    assert list(media_root.rglob("*.*")) == []


# --- Authorization ----------------------------------------------------------


async def test_another_user_cannot_read_your_media(app_client, make_user, media_root):
    _, owner_token = await make_user()
    _, other_token = await make_user()

    asset_id = (await _upload(app_client, owner_token)).json()["id"]

    metadata = await app_client.get(
        f"/api/v2/media/{asset_id}", headers=auth(other_token)
    )
    content = await app_client.get(
        f"/api/v2/media/{asset_id}/content", headers=auth(other_token)
    )

    # 404, not 403 — a 403 would confirm the id exists.
    assert metadata.status_code == 404
    assert content.status_code == 404


async def test_another_user_cannot_delete_your_media(app_client, make_user, media_root):
    _, owner_token = await make_user()
    _, other_token = await make_user()

    asset_id = (await _upload(app_client, owner_token)).json()["id"]

    response = await app_client.delete(
        f"/api/v2/media/{asset_id}", headers=auth(other_token)
    )
    assert response.status_code == 404

    # And it is genuinely still there for the owner.
    still_there = await app_client.get(
        f"/api/v2/media/{asset_id}", headers=auth(owner_token)
    )
    assert still_there.status_code == 200


async def test_media_requires_authentication(app_client, media_root):
    response = await app_client.get("/api/v2/media/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401


# --- Deletion ---------------------------------------------------------------


async def test_owner_can_delete_and_the_bytes_are_really_gone(
    app_client, make_user, media_root
):
    _, token = await make_user()
    asset_id = (await _upload(app_client, token)).json()["id"]

    files_before = list(media_root.rglob("*.png"))
    assert len(files_before) == 1

    response = await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    assert list(media_root.rglob("*.png")) == [], "the file must be erased from disk"

    gone = await app_client.get(f"/api/v2/media/{asset_id}", headers=auth(token))
    assert gone.status_code == 404


async def test_deleting_twice_is_not_an_error_the_second_time_it_is_a_404(
    app_client, make_user, media_root
):
    _, token = await make_user()
    asset_id = (await _upload(app_client, token)).json()["id"]

    assert (
        await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    ).status_code == 200
    assert (
        await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))
    ).status_code == 404


async def test_deletion_is_audited(app_client, make_user, media_root):
    from sqlalchemy import select

    from app.domains.audit.models import ACTION_MEDIA_DELETED, AuditEvent
    from app.shared.database import sql

    _, token = await make_user()
    asset_id = (await _upload(app_client, token)).json()["id"]
    await app_client.delete(f"/api/v2/media/{asset_id}", headers=auth(token))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditEvent).where(AuditEvent.action == ACTION_MEDIA_DELETED)
                )
            )
            .scalars()
            .all()
        )
    assert any(r.subject_id == asset_id for r in rows)


# --- Storage adapter unit tests --------------------------------------------


async def test_local_storage_refuses_path_traversal(tmp_path):
    from app.domains.media.storage.base import StorageError
    from app.domains.media.storage.local import LocalFilesystemStorage

    storage = LocalFilesystemStorage(root=str(tmp_path))

    for bad_key in ("../escape.png", "/etc/passwd", "media/../../escape.png"):
        with pytest.raises(StorageError):
            await storage.put(bad_key, b"x", "image/png")


async def test_generated_keys_are_scoped_to_the_account():
    import uuid

    from app.domains.media.storage.base import build_key

    account = uuid.uuid4()
    asset = uuid.uuid4()
    key = build_key(account, asset, "image/jpeg")

    assert key == f"media/{account}/{asset}.jpg"


async def test_s3_adapter_reports_missing_configuration_clearly(monkeypatch):
    """The production adapter exists and fails informatively, not mysteriously."""
    from app.domains.media.storage import s3
    from app.domains.media.storage.base import StorageError

    monkeypatch.setattr(s3, "S3_BUCKET", "")
    monkeypatch.setattr(s3, "S3_ACCESS_KEY_ID", "")
    monkeypatch.setattr(s3, "S3_SECRET_ACCESS_KEY", "")

    with pytest.raises(StorageError) as excinfo:
        s3.S3CompatibleStorage()

    assert "S3_BUCKET" in str(excinfo.value)
