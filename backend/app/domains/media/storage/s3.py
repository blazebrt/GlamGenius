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
from typing import Any

from app.config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_REGION,
    S3_SECRET_ACCESS_KEY,
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
            client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
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
