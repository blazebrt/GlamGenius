"""Refuse an oversized request before reading it.

Starlette buffers a whole request body into memory before any handler, any
dependency, and any authentication runs. Every size limit in this application
— ``MAX_IMAGE_BASE64_CHARS``, ``MEDIA_MAX_BYTES``, a Pydantic ``max_length`` —
is checked *after* that has already happened, so they bound what is stored, not
what is allocated.

Measured against this app, on the route that needs no credentials:

    20 MB anonymous -> HTTP 401   peak RSS 253 MB
    60 MB anonymous -> HTTP 401   peak RSS 533 MB

The request is refused, and the refusal costs the caller nothing and the server
hundreds of megabytes. The pre-PMF runtime is a single 512 MB instance, so a
handful of concurrent requests from anybody at all is enough to stop it — no
account, no invite, no token.

So the size is checked where it has to be: at the front, before the body is
read. ``Content-Length`` is refused outright when it is over the cap. A request
that declares no length, or under-declares it, is counted as it streams and cut
off at the same ceiling — otherwise the check would be advisory, and the header
is written by the caller.

The cap is deliberately well above anything real: a full-size image arrives as
roughly 12 MB of base64, so 16 MB leaves room for the JSON around it while
still being two orders of magnitude below what hurts.
"""
from __future__ import annotations

import logging

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: What a 413 looks like. Written here rather than raised as an exception
#: because this runs before the exception handlers are in scope.
_TOO_LARGE_BODY = (
    b'{"detail":{"code":"request_too_large",'
    b'"message":"That request is too large. Please try a smaller photo.",'
    b'"retryable":false}}'
)


class BodySizeLimitMiddleware:
    """Cut off a request body that is larger than ``max_bytes``."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._refuse(send)
                    return
            except ValueError:
                # A malformed length is not a reason to read an unbounded body.
                await self._refuse(send)
                return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # The caller under-declared or declared nothing. Stop the
                    # stream rather than keep buffering it.
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLarge:
            await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        logger.info("request_rejected_too_large max_bytes=%s", self.max_bytes)
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})


class _BodyTooLarge(Exception):
    """Internal signal from the counting receive channel."""


__all__ = ["BodySizeLimitMiddleware"]
