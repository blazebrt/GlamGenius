"""Picking the storage adapter from configuration.

Production default is ``supabase``. The ``local`` adapter is retained for
tests only. The ``s3`` adapter is retained temporarily so the existing MinIO
integration test still passes; Prompt 2 removes it.
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
    if backend == "supabase":
        from app.domains.media.storage.supabase import SupabaseStorage

        _storage = SupabaseStorage()
    elif backend == "local":
        if APP_ENV == "production" and not MEDIA_ALLOW_LOCAL_IN_PRODUCTION:
            raise StorageError(
                "MEDIA_STORAGE_BACKEND=local is not permitted when APP_ENV=production. "
                "Set MEDIA_STORAGE_BACKEND=supabase (recommended) or acknowledge the "
                "single-pod trade-off with MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true."
            )
        _storage = LocalFilesystemStorage()
    elif backend == "s3":
        # Retained until Prompt 2 for the MinIO integration test.
        from app.domains.media.storage.s3 import S3CompatibleStorage

        _storage = S3CompatibleStorage()
    else:
        raise StorageError(
            f"MEDIA_STORAGE_BACKEND must be 'supabase', 'local' or 's3', got {backend!r}"
        )

    logger.info("media_storage_backend=%s", _storage.backend_name)
    return _storage


def set_storage(storage: Optional[MediaStorage]) -> None:
    """Override the adapter. Tests use this; nothing else should."""
    global _storage
    _storage = storage
