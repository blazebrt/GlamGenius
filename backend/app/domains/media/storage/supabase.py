"""Supabase Storage adapter for the media domain.

**Private bucket.** Nothing here relies on public URLs; every read for an
authenticated user goes through a short-lived signed URL, and every write
happens through the service-role client held in memory server-side.

**Typed failures.** Every provider-side failure is mapped to a specific
subclass of :class:`StorageError`. The service layer uses those subclasses
to distinguish "the object is missing" (row/object inconsistency) from
"the provider is down right now" (retry) from "we are misconfigured"
(readiness failure). See ``docs/stabilisation/SUPABASE_HARDENING_PACKAGES_BCD.md``
for the mapping.

**Bounded network timeouts.** The Supabase Python client wraps ``httpx``;
per-operation calls are wrapped in ``asyncio.to_thread`` with a coarse
timeout so a hanging upload cannot hold a request open indefinitely.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from app.config import (
    MEDIA_SIGNED_URL_TTL_SECONDS,
    SUPABASE_STORAGE_BUCKET,
)
from app.domains.media.storage.base import (
    MediaStorage,
    StorageInvalidResponse,
    StorageMisconfigured,
    StorageObjectMissing,
    StorageTimeout,
    StorageUnauthorized,
    StorageUnavailable,
)
from app.shared.supabase_client import get_supabase_admin

logger = logging.getLogger(__name__)


_STORAGE_TIMEOUT_SECONDS = 20.0
# The Supabase Storage REST API returns paginated listings; 100 is the
# default. We loop until the page is short.
_LIST_PAGE_SIZE = 100
# Hard cap so a signed URL can never be issued for longer than fifteen
# minutes even if a caller passes an inflated ttl.
_SIGNED_URL_TTL_MAX_SECONDS = 900
_SIGNED_URL_TTL_MIN_SECONDS = 30


def _classify(exc: Exception) -> Exception:
    """Map a Supabase SDK exception onto our typed hierarchy.

    The Supabase Python SDK does not raise a stable exception tree; failures
    surface as ``StorageException`` (v2.x) or plain ``Exception`` with a
    ``status_code`` attribute. We inspect the attribute where present and
    fall back to string matching, which is unpleasant but the only option
    the SDK gives us today.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    message = str(exc).lower()
    if status in (401, 403) or "unauthorized" in message or "forbidden" in message or "invalid signature" in message:
        return StorageUnauthorized(str(exc))
    if status == 404 or "not found" in message or "does not exist" in message or "object not found" in message:
        return StorageObjectMissing(str(exc))
    if status in (502, 503, 504) or "unavailable" in message:
        return StorageUnavailable(str(exc))
    return StorageUnavailable(f"supabase_storage_provider_failure: {exc}")


class SupabaseStorage(MediaStorage):
    """Supabase Storage-backed implementation of :class:`MediaStorage`."""

    backend_name = "supabase"

    def __init__(self, bucket: str | None = None) -> None:
        self._bucket = bucket or SUPABASE_STORAGE_BUCKET
        if not self._bucket:
            raise StorageMisconfigured(
                "SUPABASE_STORAGE_BUCKET is not configured. Set it in backend/.env."
            )

    def _bucket_api(self):
        try:
            client = get_supabase_admin()
        except RuntimeError as exc:
            raise StorageMisconfigured(f"supabase_admin_not_configured: {exc}") from exc
        return client.storage.from_(self._bucket)

    async def _run(self, op: str, fn):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn), timeout=_STORAGE_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            logger.warning("supabase_storage_%s_timeout", op)
            raise StorageTimeout(f"supabase_storage_{op}_timeout") from exc
        except (StorageObjectMissing, StorageUnauthorized, StorageMisconfigured,
                StorageInvalidResponse, StorageUnavailable, StorageTimeout):
            raise
        except Exception as exc:  # noqa: BLE001 — external boundary
            logger.warning(
                "supabase_storage_%s_failed reason=%s", op, type(exc).__name__
            )
            raise _classify(exc) from exc

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        api = self._bucket_api()

        def _do() -> None:
            # ``upsert=True`` because the caller has already generated an
            # unpredictable UUID-based key, so any collision is a retry of
            # the same logical write.
            api.upload(
                path=key,
                file=data,
                file_options={
                    "content-type": content_type,
                    "upsert": "true",
                },
            )

        await self._run("upload", _do)

    async def get(self, key: str) -> bytes:
        api = self._bucket_api()

        def _do() -> bytes:
            resp = api.download(key)
            if resp is None:
                raise StorageObjectMissing("supabase_storage_get_empty")
            if isinstance(resp, (bytes, bytearray)):
                return bytes(resp)
            # Some SDK versions return a response-like object.
            data = getattr(resp, "content", None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            raise StorageInvalidResponse("supabase_storage_get_unexpected_response")

        return await self._run("get", _do)

    async def delete(self, key: str) -> None:
        api = self._bucket_api()

        def _do() -> None:
            api.remove([key])

        await self._run("delete", _do)

    async def exists(self, key: str) -> bool:
        api = self._bucket_api()

        def _do() -> bool:
            parent, _, name = key.rpartition("/")
            listing = api.list(path=parent or "") or []
            return any(
                entry.get("name") == name
                for entry in listing
                if isinstance(entry, dict)
            )

        return await self._run("exists", _do)

    async def presigned_get_url(self, key: str, ttl_seconds: int) -> str | None:
        api = self._bucket_api()
        raw_ttl = ttl_seconds or MEDIA_SIGNED_URL_TTL_SECONDS
        ttl = min(max(raw_ttl, _SIGNED_URL_TTL_MIN_SECONDS), _SIGNED_URL_TTL_MAX_SECONDS)

        def _do() -> str | None:
            resp = api.create_signed_url(key, ttl)
            if isinstance(resp, dict):
                signed = resp.get("signedURL") or resp.get("signed_url")
                if isinstance(signed, str) and signed:
                    return signed
            raise StorageInvalidResponse("supabase_storage_signed_url_missing")

        return await self._run("signed_url", _do)

    async def list_prefix(self, prefix: str) -> Iterable[str]:
        """List every object under ``prefix``.

        Supabase's ``list`` endpoint paginates and only returns entries at a
        single directory level, so we walk sub-folders explicitly. Every
        listing failure is a hard error — treating an outage as "no objects"
        would make account deletion look complete when it is not.
        """
        api = self._bucket_api()
        found: list[str] = []
        # We use a queue so nested prefixes are handled iteratively.
        queue: list[str] = [prefix.rstrip("/")]

        def _list_page(path: str, offset: int) -> list:
            listing = api.list(
                path=path or "",
                options={
                    "limit": _LIST_PAGE_SIZE,
                    "offset": offset,
                },
            )
            if listing is None:
                raise StorageInvalidResponse("supabase_storage_list_none")
            return listing

        while queue:
            path = queue.pop(0)
            offset = 0
            while True:
                page = await self._run("list", lambda p=path, o=offset: _list_page(p, o))
                if not page:
                    break
                for entry in page:
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("name")
                    if not isinstance(name, str) or not name:
                        continue
                    # Supabase marks folders with a null ``id``; when a folder
                    # appears at this level we recurse into it. Files have a
                    # non-null id and a metadata dict.
                    is_folder = entry.get("id") is None and entry.get("metadata") is None
                    child_path = f"{path}/{name}" if path else name
                    if is_folder:
                        queue.append(child_path)
                    else:
                        found.append(child_path)
                if len(page) < _LIST_PAGE_SIZE:
                    break
                offset += _LIST_PAGE_SIZE

        return found

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix``. Returns the count removed."""
        api = self._bucket_api()
        keys = list(await self.list_prefix(prefix))
        if not keys:
            return 0

        def _do() -> None:
            api.remove(keys)

        await self._run("delete_prefix", _do)
        return len(keys)
