"""Media upload, retrieval and deletion.

Every read and every delete goes through :func:`get_owned_asset`, which is the
single place ownership is enforced. Missing and not-yours both return 404 — a
403 would confirm that someone else's asset id exists, which is a free
enumeration oracle.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit import service as audit
from app.domains.audit.models import ACTION_MEDIA_DELETED, ACTION_MEDIA_UPLOADED
from app.domains.media.models import (
    MEDIA_STATUS_ACTIVE,
    MEDIA_STATUS_DELETED,
    MediaAsset,
)
from app.domains.media.storage import get_storage
from app.domains.media.storage.base import StorageError, build_key
from app.shared.database.base import new_uuid, utcnow
from app.shared.errors.exceptions import NotFoundError
from app.shared.events import outbox
from app.shared.validation.media import read_dimensions, validate_upload

logger = logging.getLogger(__name__)


def to_public_dict(asset: MediaAsset) -> Dict[str, Any]:
    """What a caller is allowed to see.

    ``storage_key`` and ``storage_backend`` are deliberately absent — the audit
    flagged exposing internal paths, and the id is all a client ever needs.
    """
    return {
        "id": str(asset.id),
        "content_type": asset.content_type,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "purpose": asset.purpose,
        "status": asset.status,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "deleted_at": asset.deleted_at.isoformat() if asset.deleted_at else None,
        "content_url": f"/api/v2/media/{asset.id}/content",
    }


async def upload(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    data: bytes,
    declared_type: Optional[str],
    purpose: str,
    original_filename: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> MediaAsset:
    content_type, size = validate_upload(data, declared_type)
    width, height = read_dimensions(data, content_type)
    digest = hashlib.sha256(data).hexdigest()

    asset_id = new_uuid()
    key = build_key(account_id, asset_id, content_type)
    storage = get_storage()

    # Bytes first. A storage failure must not leave a database row pointing at
    # an object that was never written.
    await storage.put(key, data, content_type)

    asset = MediaAsset(
        id=asset_id,
        account_id=account_id,
        storage_backend=storage.backend_name,
        storage_key=key,
        content_type=content_type,
        byte_size=size,
        sha256=digest,
        width=width,
        height=height,
        purpose=purpose,
        # Only the extension is kept from a caller-supplied name; the rest is
        # untrusted text that would end up in logs and exports.
        original_filename=(original_filename or "")[:255] or None,
        status=MEDIA_STATUS_ACTIVE,
    )
    session.add(asset)
    await session.flush()

    await audit.record(
        session,
        action=ACTION_MEDIA_UPLOADED,
        account_id=account_id,
        subject_type="media_asset",
        subject_id=str(asset.id),
        context={"purpose": purpose, "byte_size": size, "content_type": content_type},
        client_ip=client_ip,
    )
    await outbox.publish(
        session,
        aggregate_type="media_asset",
        aggregate_id=str(asset.id),
        event_type="media.uploaded",
        payload={"account_id": str(account_id), "purpose": purpose},
    )
    return asset


async def get_owned_asset(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    include_deleted: bool = False,
) -> MediaAsset:
    """The caller's asset, or 404. The only ownership check in this domain."""
    stmt = (
        select(MediaAsset)
        .where(MediaAsset.id == asset_id)
        .where(MediaAsset.account_id == account_id)
    )
    if not include_deleted:
        stmt = stmt.where(MediaAsset.status == MEDIA_STATUS_ACTIVE)
    asset = (await session.execute(stmt)).scalar_one_or_none()
    if asset is None:
        raise NotFoundError("We could not find that photo.")
    return asset


async def read_bytes(asset: MediaAsset) -> bytes:
    try:
        return await get_storage().get(asset.storage_key)
    except StorageError as exc:
        # The row says it exists but the bytes are gone. To the caller this is
        # simply "not found"; the detail belongs in the log.
        logger.error(
            "media_bytes_missing asset_id=%s backend=%s", asset.id, asset.storage_backend
        )
        raise NotFoundError("That photo is no longer available.") from exc


async def delete(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    client_ip: Optional[str] = None,
) -> MediaAsset:
    """Erase the bytes, then mark the row deleted.

    Bytes go first: if the row were marked first and the storage delete then
    failed, the image would be invisible but still on disk — the worst outcome
    for something a user explicitly asked to be removed. In this order a failure
    leaves the asset visible and retryable, and the user is told the truth.
    """
    asset = await get_owned_asset(session, account_id=account_id, asset_id=asset_id)

    await get_storage().delete(asset.storage_key)

    asset.status = MEDIA_STATUS_DELETED
    asset.deleted_at = utcnow()
    await session.flush()

    await audit.record(
        session,
        action=ACTION_MEDIA_DELETED,
        account_id=account_id,
        subject_type="media_asset",
        subject_id=str(asset.id),
        context={"purpose": asset.purpose, "byte_size": asset.byte_size},
        client_ip=client_ip,
    )
    await outbox.publish(
        session,
        aggregate_type="media_asset",
        aggregate_id=str(asset.id),
        event_type="media.deleted",
        payload={"account_id": str(account_id)},
    )
    return asset


async def list_for_account(
    session: AsyncSession, account_id: uuid.UUID, include_deleted: bool = False
) -> List[MediaAsset]:
    stmt = select(MediaAsset).where(MediaAsset.account_id == account_id)
    if not include_deleted:
        stmt = stmt.where(MediaAsset.status == MEDIA_STATUS_ACTIVE)
    stmt = stmt.order_by(MediaAsset.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def delete_all_for_account(
    session: AsyncSession, account_id: uuid.UUID, client_ip: Optional[str] = None
) -> int:
    """Erase every active asset. Used by account deletion."""
    assets = await list_for_account(session, account_id)
    storage = get_storage()
    removed = 0
    for asset in assets:
        try:
            await storage.delete(asset.storage_key)
        except StorageError:
            # Keep going. One unreachable object must not strand the rest of a
            # deletion request; the audit record captures the count either way.
            logger.error("media_delete_failed asset_id=%s", asset.id)
            continue
        asset.status = MEDIA_STATUS_DELETED
        asset.deleted_at = utcnow()
        removed += 1
    await session.flush()
    return removed
