"""Per-request correlation ID.

Audit finding F37: a user reporting "it failed" could not be traced to a log
line. Every response now carries ``X-Request-Id``, every log line for that
request carries the same value, and structured errors include it in the body so
a screenshot is enough to find the request.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_request_id: ContextVar[str] = ContextVar("request_id", default="")

HEADER = "X-Request-Id"


def get_request_id() -> str:
    return _request_id.get() or "-"


def set_request_id(value: str) -> None:
    _request_id.set(value)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Accept an inbound request id, or mint one, and echo it back."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get(HEADER, "").strip()
        # Only trust an inbound id that looks like one — an attacker-supplied
        # value ends up in log lines, so cap it and keep it boring.
        request_id = (
            incoming
            if incoming and len(incoming) <= 64 and incoming.isprintable()
            else uuid.uuid4().hex
        )
        set_request_id(request_id)
        response = await call_next(request)
        response.headers[HEADER] = request_id
        return response
