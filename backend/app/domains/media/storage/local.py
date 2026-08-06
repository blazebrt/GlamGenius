"""Local filesystem adapter for development and unit tests.

Writes under ``MEDIA_LOCAL_ROOT``. Every path is resolved and checked to be
inside that root before any file operation, so even a malformed key cannot
escape the directory.

Refused at startup when ``APP_ENV=production``; see ``factory.py``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from app.config import MEDIA_LOCAL_ROOT
from app.domains.media.storage.base import (
    StorageMisconfigured,
    StorageObjectMissing,
)

logger = logging.getLogger(__name__)


class LocalFilesystemStorage:
    backend_name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or MEDIA_LOCAL_ROOT).resolve()

    def _path_for(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise StorageMisconfigured("Invalid storage key")
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageMisconfigured("Storage key escapes the media root")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)

        def _read() -> bytes:
            if not path.is_file():
                raise StorageObjectMissing("Stored object is missing")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, key: str) -> None:
        path = self._path_for(key)

        def _delete() -> None:
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        return await asyncio.to_thread(path.is_file)

    async def presigned_get_url(self, key: str, ttl_seconds: int) -> Optional[str]:
        """Local storage has no cryptographically-signed URL scheme.

        Returning ``None`` here tells the service layer to fall back to
        backend streaming rather than emitting a cleartext filesystem path.
        """
        return None

    async def list_prefix(self, prefix: str) -> Iterable[str]:
        base = self._path_for(prefix) if prefix else self.root

        def _walk() -> list[str]:
            found: list[str] = []
            if not base.exists():
                return found
            for p in base.rglob("*"):
                if p.is_file():
                    found.append(str(p.relative_to(self.root)))
            return found

        return await asyncio.to_thread(_walk)

    async def delete_prefix(self, prefix: str) -> int:
        keys = list(await self.list_prefix(prefix))
        for key in keys:
            await self.delete(key)
        return len(keys)
