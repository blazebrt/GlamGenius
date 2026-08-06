"""Picking the storage adapter from configuration.

Production **must** use ``supabase``. The ``local`` adapter is retained for
unit tests and local development only, and is refused at startup when
``APP_ENV=production``.
"""
from __future__ import annotations

import logging

from app.config import (
    APP_ENV,
    MEDIA_ALLOW_LOCAL_IN_PRODUCTION,
    MEDIA_STORAGE_BACKEND,
)
from app.domains.media.storage.base import (
    MediaStorage,
    StorageMisconfigured,
)
from app.domains.media.storage.local import LocalFilesystemStorage

logger = logging.getLogger(__name__)

_storage: MediaStorage | None = None


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
            raise StorageMisconfigured(
                "MEDIA_STORAGE_BACKEND=local is not permitted when APP_ENV=production. "
                "Set MEDIA_STORAGE_BACKEND=supabase."
            )
        _storage = LocalFilesystemStorage()
    else:
        raise StorageMisconfigured(
            f"MEDIA_STORAGE_BACKEND must be 'supabase' or 'local', got {backend!r}. "
            "Cloud Object Storage support was removed."
        )

    logger.info("media_storage_backend=%s", _storage.backend_name)
    return _storage


def set_storage(storage: MediaStorage | None) -> None:
    """Override the adapter. Tests use this; nothing else should."""
    global _storage
    _storage = storage
