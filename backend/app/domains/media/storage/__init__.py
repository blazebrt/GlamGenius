"""Object storage adapters."""
from app.domains.media.storage.base import MediaStorage, StorageError
from app.domains.media.storage.factory import get_storage

__all__ = ["MediaStorage", "StorageError", "get_storage"]
