"""Picking the storage adapter from configuration.

Fix 9 (Work Package 2): in a production deployment the local filesystem
adapter is not acceptable. A pod's local disk is lost on redeploy, and no
other pod can serve the writes. `get_storage()` refuses to boot with
``MEDIA_STORAGE_BACKEND=local`` when ``APP_ENV=production`` unless the
operator has consciously set ``MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true`` in
the environment (which is only ever right for a single-pod dev-preview).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import (
    APP_ENV,
    MEDIA_ALLOW_LOCAL_IN_PRODUCTION,
    MEDIA_STORAGE_BACKEND,
)
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
        if APP_ENV == "production" and not MEDIA_ALLOW_LOCAL_IN_PRODUCTION:
            # Fail closed at boot rather than half-way through the first upload.
            # The message names the exact escape hatch so a knowledgeable
            # operator running a single-pod dev-preview can still proceed.
            raise StorageError(
                "MEDIA_STORAGE_BACKEND=local is not permitted when APP_ENV=production. "
                "Set MEDIA_STORAGE_BACKEND=s3 and provide S3_ENDPOINT_URL, S3_BUCKET, "
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY, or set "
                "MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true to acknowledge the risk. "
                "See docs/stabilisation/MEDIA_STORAGE_OPERATIONS.md."
            )
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
