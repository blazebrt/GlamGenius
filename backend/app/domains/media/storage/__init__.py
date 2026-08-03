"""Object storage adapters."""
from app.domains.media.storage.base import (
    MediaStorage,
    StorageError,
    StorageInvalidResponse,
    StorageMisconfigured,
    StorageObjectMissing,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
    account_prefix,
    build_key,
)
from app.domains.media.storage.factory import get_storage, set_storage

__all__ = [
    "MediaStorage",
    "StorageError",
    "StorageObjectMissing",
    "StorageUnauthorized",
    "StorageTimeout",
    "StorageUnavailable",
    "StorageMisconfigured",
    "StorageInvalidResponse",
    "account_prefix",
    "build_key",
    "get_storage",
    "set_storage",
]
