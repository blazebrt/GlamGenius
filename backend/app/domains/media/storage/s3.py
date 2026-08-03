"""S3-compatible adapter for production (R2, S3, Spaces, MinIO).

``boto3`` is imported lazily and is **not** in ``requirements.txt``. It is a
production-only dependency worth tens of megabytes, and Phase 1 runs on the
local adapter. The interface is complete and exercised by tests; installing the
package and setting ``MEDIA_STORAGE_BACKEND=s3`` is all that switching needs.

Keeping the import lazy also means a missing package produces a clear
configuration error at first use rather than an import crash at boot.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
    S3_SERVER_SIDE_ENCRYPTION,
)
from app.domains.media.storage.base import StorageError

logger = logging.getLogger(__name__)

MISSING_BOTO3 = (
    "S3 storage is selected but the 'boto3' package is not installed. "
    "Add boto3 to backend/requirements.txt, or set MEDIA_STORAGE_BACKEND=local."
)


class S3CompatibleStorage:
    backend_name = "s3"

    def __init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("S3_BUCKET", S3_BUCKET),
                ("S3_ACCESS_KEY_ID", S3_ACCESS_KEY_ID),
                ("S3_SECRET_ACCESS_KEY", S3_SECRET_ACCESS_KEY),
            )
            if not value
        ]
        if missing:
            raise StorageError(
                "S3 storage is selected but these settings are missing: "
                + ", ".join(missing)
            )
        self.bucket = S3_BUCKET
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # noqa: PLC0415 - deliberately lazy, see module docstring
        except ImportError as exc:
            raise StorageError(MISSING_BOTO3) from exc

        self._client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL or None,
            region_name=S3_REGION or None,
            aws_access_key_id=S3_ACCESS_KEY_ID,
            aws_secret_access_key=S3_SECRET_ACCESS_KEY,
        )
        return self._client

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        client = self._get_client()

        def _put() -> None:
            # Every write asks the server to encrypt at rest. When the target
            # bucket already has default encryption configured, the header is
            # honoured silently; when it does not, the write is refused, which
            # is the outcome we want (fail-closed on missing encryption). Set
            # S3_SERVER_SIDE_ENCRYPTION="" to disable this behaviour when the
            # provider does not accept the header (see
            # docs/stabilisation/MEDIA_STORAGE_OPERATIONS.md).
            extra: dict = {}
            if S3_SERVER_SIDE_ENCRYPTION:
                extra["ServerSideEncryption"] = S3_SERVER_SIDE_ENCRYPTION
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                **extra,
            )

        # boto3 is synchronous; a thread keeps it off the event loop.
        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        client = self._get_client()

        def _get() -> bytes:
            try:
                response = client.get_object(Bucket=self.bucket, Key=key)
            except Exception as exc:  # noqa: BLE001 - botocore errors are dynamic
                raise StorageError(f"Could not read object: {type(exc).__name__}") from exc
            return response["Body"].read()

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        client = self._get_client()

        def _delete() -> None:
            client.delete_object(Bucket=self.bucket, Key=key)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        client = self._get_client()

        def _head() -> bool:
            try:
                client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001 - a 404 is an exception in botocore
                return False

        return await asyncio.to_thread(_head)

    async def presigned_get_url(self, key: str, ttl_seconds: int) -> Optional[str]:
        """Short-lived signed URL the client can fetch directly.

        Returning ``None`` from here would force every read through the
        backend; returning a URL keeps the app server out of the byte path
        for photo delivery. The TTL is clamped at the low end to prevent a
        misconfiguration granting a multi-day link.
        """
        client = self._get_client()
        # 60 seconds is the shortest TTL that survives realistic clock skew;
        # anything longer than 15 minutes is treated as a misconfiguration
        # and clamped. If a legitimate use case needs longer, add a new
        # setting rather than raising this ceiling.
        ttl = max(60, min(ttl_seconds, 900))

        def _sign() -> Optional[str]:
            try:
                return client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=ttl,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("presigned_url_failed key=%s error=%s", key, type(exc).__name__)
                return None

        return await asyncio.to_thread(_sign)
