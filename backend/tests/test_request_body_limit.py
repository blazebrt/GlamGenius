"""An oversized request is refused before it is read.

Starlette buffers a whole request body into memory before any handler, any
dependency and any authentication runs. Every other size limit in this
application — ``MAX_IMAGE_BASE64_CHARS``, ``MEDIA_MAX_BYTES``, a Pydantic
``max_length`` — is checked after that, so they bound what gets stored, not
what gets allocated.

Measured on the route that needs no credentials at all, before this existed:

    20 MB anonymous -> HTTP 401   peak RSS 253 MB
    60 MB anonymous -> HTTP 401   peak RSS 533 MB

The request was refused and the refusal cost the caller nothing and the server
hundreds of megabytes. The pre-PMF runtime is one 512 MB instance, so a few
concurrent requests from anybody at all were enough to stop it.

These tests assert the property that matters and that RSS cannot show inside a
single process: the application is never handed the body.
"""
from __future__ import annotations

import pytest
from app.config import MAX_REQUEST_BODY_BYTES
from app.shared.security.body_limit import BodySizeLimitMiddleware
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

SMALL_CAP = 4096


@pytest.fixture
def capped_app():
    """A tiny app behind the middleware, so 'did the app see it?' is observable."""
    seen: list[int] = []

    async def echo(request: Request):
        body = await request.body()
        seen.append(len(body))
        return JSONResponse({"read": len(body)})

    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=SMALL_CAP)
    return TestClient(app, raise_server_exceptions=False), seen


class TestTheApplicationNeverSeesAnOversizedBody:
    def test_a_body_over_the_cap_is_refused(self, capped_app):
        client, seen = capped_app
        response = client.post("/echo", content=b"x" * (SMALL_CAP * 4))
        assert response.status_code == 413
        assert seen == [], "the handler read a body it should never have been given"

    def test_the_refusal_says_what_happened_without_naming_internals(self, capped_app):
        client, _ = capped_app
        detail = client.post("/echo", content=b"x" * (SMALL_CAP * 4)).json()["detail"]
        assert detail["code"] == "request_too_large"
        assert detail["retryable"] is False
        assert "max_bytes" not in str(detail)

    def test_a_body_under_the_cap_is_delivered_untouched(self, capped_app):
        client, seen = capped_app
        payload = b"x" * (SMALL_CAP // 2)
        response = client.post("/echo", content=payload)
        assert response.status_code == 200
        assert seen == [len(payload)]

    def test_a_body_exactly_at_the_cap_is_allowed(self, capped_app):
        client, seen = capped_app
        response = client.post("/echo", content=b"x" * SMALL_CAP)
        assert response.status_code == 200
        assert seen == [SMALL_CAP]

    def test_an_empty_body_is_fine(self, capped_app):
        client, seen = capped_app
        assert client.post("/echo", content=b"").status_code == 200
        assert seen == [0]


class TestTheHeaderIsNotTrusted:
    """``Content-Length`` is written by the caller, so it cannot be the only check."""

    def test_a_chunked_body_with_no_declared_length_is_still_cut_off(self, capped_app):
        client, seen = capped_app

        def chunks():
            for _ in range(8):
                yield b"x" * SMALL_CAP

        response = client.post("/echo", content=chunks())
        assert response.status_code == 413
        assert seen == [], "a body with no Content-Length was read in full"

    def test_a_malformed_length_is_refused_rather_than_read(self, capped_app):
        client, seen = capped_app
        response = client.post(
            "/echo", content=b"x" * 10, headers={"content-length": "not-a-number"}
        )
        assert response.status_code in (400, 413)
        assert seen == []


class TestTheConfiguredCap:
    def test_the_cap_leaves_room_for_a_full_size_photo(self):
        """A full-size image arrives as roughly 12 MB of base64, inside JSON."""
        from app.config import MAX_IMAGE_BASE64_CHARS

        assert MAX_REQUEST_BODY_BYTES > MAX_IMAGE_BASE64_CHARS, (
            "the body cap is below the largest image the app accepts, so a "
            "legitimate photo would be refused"
        )

    def test_the_cap_leaves_room_for_a_media_upload(self):
        from app.config import MEDIA_MAX_BYTES

        assert MAX_REQUEST_BODY_BYTES > MEDIA_MAX_BYTES


class TestThroughTheRealApplication:
    @pytest.mark.asyncio
    async def test_an_anonymous_oversized_request_is_refused(self, app_client):
        """No account, no invite, no token — and no memory spent either."""
        response = await app_client.post(
            "/api/v2/access/reserve",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1024),
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "request_too_large"

    @pytest.mark.asyncio
    async def test_an_ordinary_request_is_unaffected(self, app_client):
        response = await app_client.post(
            "/api/v2/access/reserve", json={"email": "a@example.com", "invite_code": "NOPE"}
        )
        assert response.status_code != 413
