"""The storage contract every adapter implements.

Supabase Storage is the only production media backend. The local adapter is
retained for unit tests and local development, and is refused at startup when
``APP_ENV=production``.

Failures are surfaced through a small, typed exception hierarchy so the
service layer can map them to the right HTTP status without swallowing the
distinction between "the row is inconsistent with the object store" and "the
provider is unreachable right now".
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Optional, Protocol, runtime_checkable


class StorageError(Exception):
    """Base for every storage-adapter failure."""


class StorageObjectMissing(StorageError):
    """The provider confirmed the object does not exist."""


class StorageUnauthorized(StorageError):
    """The provider refused the request (bad credentials / RLS / policy)."""


class StorageTimeout(StorageError):
    """A single provider call did not return before the deadline."""


class StorageUnavailable(StorageError):
    """The provider is reachable but degraded, or the network failed."""


class StorageMisconfigured(StorageError):
    """A required setting is missing or nonsensical.

    Distinct from ``StorageUnavailable``: a misconfiguration is not a transient
    incident and must be visible at startup rather than as a per-request 5xx.
    """


class StorageInvalidResponse(StorageError):
    """The provider returned a shape the adapter cannot parse."""


# Extension per accepted MIME type. Keys are generated, never taken from the
# uploaded filename — a caller-supplied name is how path traversal gets in.
EXTENSION_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# Every object owned by an account sits under this prefix. Deletion depends
# on it being the only such prefix so a single ``list`` walk finds everything.
ACCOUNT_OBJECT_PREFIX_TEMPLATE = "media/{account_id}"


def build_key(account_id: uuid.UUID, asset_id: uuid.UUID, content_type: str) -> str:
    """An opaque, collision-free, traversal-proof object key.

    Sharded by account so one prefix listing cannot enumerate the whole store,
    and built only from UUIDs and a known extension.
    """
    extension = EXTENSION_BY_MIME.get(content_type, "bin")
    return f"{ACCOUNT_OBJECT_PREFIX_TEMPLATE.format(account_id=account_id)}/{asset_id}.{extension}"


def account_prefix(account_id: uuid.UUID) -> str:
    return ACCOUNT_OBJECT_PREFIX_TEMPLATE.format(account_id=account_id)


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

    async def list_prefix(self, prefix: str) -> Iterable[str]:
        """Every key stored under ``prefix``.

        The deletion state machine uses this to prove an account's storage is
        empty before the Supabase Auth identity is removed. Adapters that
        cannot enumerate must raise ``StorageMisconfigured`` — silently
        returning an empty list would make deletion look complete when it is
        not.
        """
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix``. Returns the count removed."""
        ...
