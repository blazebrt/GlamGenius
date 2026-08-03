"""The storage contract every adapter implements."""
from __future__ import annotations

import uuid
from typing import Optional, Protocol, runtime_checkable


class StorageError(Exception):
    """A storage backend could not complete an operation."""


# Extension per accepted MIME type. Keys are generated, never taken from the
# uploaded filename — a caller-supplied name is how path traversal gets in.
EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def build_key(account_id: uuid.UUID, asset_id: uuid.UUID, content_type: str) -> str:
    """An opaque, collision-free, traversal-proof object key.

    Sharded by account so one prefix listing cannot enumerate the whole store,
    and built only from UUIDs and a known extension.
    """
    extension = EXTENSION_BY_MIME.get(content_type, "bin")
    return f"media/{account_id}/{asset_id}.{extension}"


@runtime_checkable
class MediaStorage(Protocol):
    """Byte storage. Knows nothing about users, permissions or databases —
    authorization happens in the service layer, above this."""

    backend_name: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def presigned_get_url(self, key: str, ttl_seconds: int) -> Optional[str]:
        """Short-lived signed URL for the caller to fetch bytes directly.

        Returns ``None`` when the adapter does not support signed URLs (the
        local filesystem adapter, for instance). The service layer falls back
        to backend streaming when this returns ``None`` — never returning a
        cleartext local path to a client.
        """
        ...
