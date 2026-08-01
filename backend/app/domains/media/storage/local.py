"""Local filesystem adapter for development.

Writes under ``MEDIA_LOCAL_ROOT``. Every path is resolved and checked to be
inside that root before any file operation, so even a malformed key cannot
escape the directory.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config import MEDIA_LOCAL_ROOT
from app.domains.media.storage.base import StorageError

logger = logging.getLogger(__name__)


class LocalFilesystemStorage:
    backend_name = "local"

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or MEDIA_LOCAL_ROOT).resolve()

    def _path_for(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise StorageError("Invalid storage key")
        candidate = (self.root / key).resolve()
        # The belt-and-braces check. `..` in a key resolves away here, and the
        # result must still sit under the root or we refuse.
        if not candidate.is_relative_to(self.root):
            raise StorageError("Storage key escapes the media root")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path_for(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a temporary name and rename, so a crash mid-write cannot
            # leave a half-file that later reads as a valid asset.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(_write)

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)

        def _read() -> bytes:
            if not path.is_file():
                raise StorageError("Stored object is missing")
            return path.read_bytes()

        return await asyncio.to_thread(_read)

    async def delete(self, key: str) -> None:
        path = self._path_for(key)

        def _delete() -> None:
            # missing_ok: deleting something already gone is a success, not an
            # error. Retrying a failed deletion must not itself fail.
            path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        return await asyncio.to_thread(path.is_file)
