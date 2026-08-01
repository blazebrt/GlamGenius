"""Media routes.

Deviation from the brief, documented: the listed ``GET /api/v2/media/{id}``
returns *metadata*, and the bytes are served from
``GET /api/v2/media/{id}/content``. One route cannot sensibly return both a JSON
description and an image body, and the app needs the metadata (size, type,
dimensions) before deciding whether to fetch the bytes.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MEDIA_MAX_BYTES
from app.domains.media import service as media_service
from app.domains.media.models import MEDIA_PURPOSE_INVENTORY
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import MediaTooLargeError, ValidationFailedError
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    require_flag,
)

router = APIRouter(dependencies=[Depends(require_flag("v2_media"))])

# The media domain stores user-owned product/inventory images. Photos supplied
# for face, hair, hands, or full-person analysis are transient request data and
# must never enter object storage.
ALLOWED_PURPOSES = {MEDIA_PURPOSE_INVENTORY}

# Refuse a stream that overruns the limit rather than buffering it all first.
# Reading an unbounded upload into memory is a denial-of-service in one line.
_CHUNK = 64 * 1024


async def _read_capped(upload: UploadFile) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > MEDIA_MAX_BYTES:
            limit_mb = MEDIA_MAX_BYTES / (1024 * 1024)
            raise MediaTooLargeError(
                f"That photo is larger than the {limit_mb:.0f} MB limit. "
                "Please choose a smaller one.",
                max_bytes=MEDIA_MAX_BYTES,
            )
    return bytes(buffer)


@router.post("/media/upload")
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Form(MEDIA_PURPOSE_INVENTORY),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    if purpose not in ALLOWED_PURPOSES:
        raise ValidationFailedError(
            "That is not a purpose we accept for an upload.", field="purpose"
        )

    data = await _read_capped(file)

    asset = await media_service.upload(
        session,
        account_id=current.account_id,
        data=data,
        declared_type=file.content_type,
        purpose=purpose,
        original_filename=file.filename,
        client_ip=client_ip(request),
    )
    await session.commit()
    return media_service.to_public_dict(asset)


@router.get("/media/{asset_id}")
async def get_media(
    asset_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Metadata for one of the caller's own assets."""
    asset = await media_service.get_owned_asset(
        session, account_id=current.account_id, asset_id=asset_id
    )
    return media_service.to_public_dict(asset)


@router.get("/media/{asset_id}/content")
async def get_media_content(
    asset_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The bytes, after the same ownership check."""
    asset = await media_service.get_owned_asset(
        session, account_id=current.account_id, asset_id=asset_id
    )
    data = await media_service.read_bytes(asset)
    return Response(
        content=data,
        media_type=asset.content_type,
        headers={
            # Someone else's browser cache must never be able to serve this.
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{asset.id}"',
        },
    )


@router.delete("/media/{asset_id}")
async def delete_media(
    asset_id: uuid.UUID,
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    asset = await media_service.delete(
        session,
        account_id=current.account_id,
        asset_id=asset_id,
        client_ip=client_ip(request),
    )
    await session.commit()
    return {
        "id": str(asset.id),
        "status": asset.status,
        "deleted_at": asset.deleted_at.isoformat() if asset.deleted_at else None,
        "message": "That photo has been deleted.",
    }
