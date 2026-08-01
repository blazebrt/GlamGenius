"""Picking the storage adapter from configuration."""
from __future__ import annotations

import logging
from typing import Optional

from app.config import MEDIA_STORAGE_BACKEND
from app.domains.media.storage.base import MediaStorage, StorageError
from app.domains.media.storage.local import LocalFilesystemStorage

logger = logging.getLogger(__name__)

_storage: Optional[MediaStorage] = None


def get_storage() -> MediaStorage:
    """The configured storage adapter, built once."""
    global _storage
    if _storage is not None:
        return _storage

    backend = MEDIA_STORAGE_BACKEND
    if backend == "local":
        _storage = LocalFilesystemStorage()
    elif backend == "s3":
        from app.domains.media.storage.s3 import S3CompatibleStorage

        _storage = S3CompatibleStorage()
    else:
        raise StorageError(
            f"MEDIA_STORAGE_BACKEND must be 'local' or 's3', got {backend!r}"
        )

    logger.info("media_storage_backend=%s", _storage.backend_name)
    return _storage


def set_storage(storage: Optional[MediaStorage]) -> None:
    """Override the adapter. Tests use this; nothing else should."""
    global _storage
    _storage = storage
