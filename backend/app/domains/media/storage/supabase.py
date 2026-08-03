"""Supabase Storage adapter for the media domain.

**Private bucket.** Nothing here relies on public URLs; every read for an
authenticated user goes through a short-lived signed URL, and every write
happens through the service-role client held in memory server-side.

**Fail closed** on authorization errors: a credential outage does *not* look
like a "not found" — it looks like a real `StorageError` that the service
layer surfaces as 5xx.

**Bounded network timeouts.** The Supabase Python client wraps `httpx`;
per-operation calls are wrapped in an ``asyncio.to_thread`` with a coarse
timeout so a hanging upload cannot hold a request open indefinitely.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import (
    MEDIA_SIGNED_URL_TTL_SECONDS,
    SUPABASE_STORAGE_BUCKET,
)
from app.domains.media.storage.base import MediaStorage, StorageError
from app.shared.supabase_client import get_supabase_admin

logger = logging.getLogger(__name__)


_STORAGE_TIMEOUT_SECONDS = 20.0


class SupabaseStorage(MediaStorage):
    """Supabase Storage-backed implementation of :class:`MediaStorage`."""

    backend_name = "supabase"

    def __init__(self, bucket: Optional[str] = None) -> None:
        self._bucket = bucket or SUPABASE_STORAGE_BUCKET
        if not self._bucket:
            raise StorageError(
                "SUPABASE_STORAGE_BUCKET is not configured. Set it in backend/.env."
            )

    def _bucket_api(self):
        try:
            client = get_supabase_admin()
        except RuntimeError as exc:
            raise StorageError(f"supabase_not_configured: {exc}") from exc
        return client.storage.from_(self._bucket)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        api = self._bucket_api()

        def _do() -> None:
            # ``upsert=True`` because the caller has already generated an
            # unpredictable UUID-based key, so any collision is a retry of the
            # same logical write.
            api.upload(
                path=key,
                file=data,
                file_options={
                    "content-type": content_type,
                    "upsert": "true",
                },
            )

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_do), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise StorageError("supabase_storage_upload_timeout") from exc
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_upload_failed key=%s reason=%s",
                key, type(exc).__name__,
            )
            raise StorageError(f"supabase_storage_upload_failed: {exc}") from exc

    async def get(self, key: str) -> bytes:
        api = self._bucket_api()

        def _do() -> bytes:
            resp = api.download(key)
            if resp is None:
                raise StorageError("supabase_storage_get_empty")
            if isinstance(resp, (bytes, bytearray)):
                return bytes(resp)
            # Some SDK versions return a response-like object.
            data = getattr(resp, "content", None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            raise StorageError("supabase_storage_get_unexpected_response")

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_do), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise StorageError("supabase_storage_get_timeout") from exc
        except StorageError:
            raise
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_get_failed key=%s reason=%s",
                key, type(exc).__name__,
            )
            raise StorageError(f"supabase_storage_get_failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        api = self._bucket_api()

        def _do() -> None:
            api.remove([key])

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_do), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise StorageError("supabase_storage_delete_timeout") from exc
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_delete_failed key=%s reason=%s",
                key, type(exc).__name__,
            )
            raise StorageError(f"supabase_storage_delete_failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        api = self._bucket_api()

        def _do() -> bool:
            # There is no direct HEAD in supabase-py; list the parent and
            # check for the filename. Cheap for our per-account sharded keys.
            parent, _, name = key.rpartition("/")
            listing = api.list(path=parent or "") or []
            return any(entry.get("name") == name for entry in listing if isinstance(entry, dict))

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_do), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise StorageError("supabase_storage_exists_timeout") from exc
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_exists_failed key=%s reason=%s",
                key, type(exc).__name__,
            )
            raise StorageError(f"supabase_storage_exists_failed: {exc}") from exc

    async def presigned_get_url(self, key: str, ttl_seconds: int) -> Optional[str]:
        api = self._bucket_api()
        ttl = min(max(ttl_seconds or MEDIA_SIGNED_URL_TTL_SECONDS, 30), 3600)

        def _do() -> Optional[str]:
            resp = api.create_signed_url(key, ttl)
            if isinstance(resp, dict):
                signed = resp.get("signedURL") or resp.get("signed_url")
                if isinstance(signed, str):
                    return signed
            return None

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_do), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise StorageError("supabase_storage_signed_url_timeout") from exc
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_signed_url_failed key=%s reason=%s",
                key, type(exc).__name__,
            )
            raise StorageError(f"supabase_storage_signed_url_failed: {exc}") from exc
