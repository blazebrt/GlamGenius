"""GlamGenius API — Personal Appearance Operating System.

Customer API routes are mounted under ``/api/v2`` and authenticated with a
verified Supabase JWT where the route requires it. ``/privacy`` and
``/support`` are deliberately public, static beta information pages. There is
no local password store or payment stack.
"""
from __future__ import annotations

from html import escape

# Sentry must be initialised before FastAPI so its middleware can attach to
# the exception path. A missing DSN is fine — init_sentry() no-ops.
from app.shared.observability.sentry_bootstrap import init_sentry

init_sentry()

import logging

from app.api.v2 import router as v2_router
from app.config import (
    ALLOWED_ORIGINS,
    ALLOWED_ORIGINS_IS_DEFAULT,
    CONSENT_VERSION,
    MAX_REQUEST_BODY_BYTES,
    MEDIA_STORAGE_BACKEND,
    SUPABASE_JWKS_URL,
    SUPABASE_URL,
    V2_FEATURES,
)
from app.shared.database import sql
from app.shared.errors.handlers import register_error_handlers
from app.shared.observability.logging import configure_logging
from app.shared.observability.request_id import RequestIdMiddleware
from app.shared.security.body_limit import BodySizeLimitMiddleware
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse

app = FastAPI(
    title="GlamGenius — Personal Appearance Operating System",
    version="2.0.0-supabase",
)

app.include_router(v2_router)

_PUBLIC_PAGE_HEADERS = {
    "Cache-Control": "public, max-age=3600",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'; img-src 'none'; "
        "connect-src 'none'; script-src 'none'"
    ),
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _public_page(title: str, body: str) -> HTMLResponse:
    """Return a dependency-free, no-input informational page."""
    return HTMLResponse(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>"
        "<style>body{max-width:46rem;margin:2rem auto;padding:0 1rem;"
        "font:16px/1.55 system-ui,sans-serif;color:#1f2937}h1{line-height:1.2}"
        "a{color:#155e75}</style></head><body>"
        f"{body}</body></html>",
        headers=_PUBLIC_PAGE_HEADERS,
    )


@app.get("/privacy", include_in_schema=False)
async def privacy_information() -> HTMLResponse:
    """Public privacy information; it never reads a database or request data."""
    return _public_page(
        "GlamGenius beta privacy information",
        f"<h1>GlamGenius beta privacy information</h1>"
        f"<p>Policy and consent version: {escape(CONSENT_VERSION)}.</p>"
        "<p>GlamGenius is an invite-only beta. It uses account and authentication "
        "information needed to operate an account; product or label images and "
        "other information you deliberately submit for analysis; profile and "
        "preference information used for personalised features; and the inventory, "
        "history and memory information you choose to save. If you enable "
        "notifications, device and notification information is used to provide them. "
        "We also keep operational and security records needed to run the service.</p>"
        "<p>Supabase provides application identity, database and private storage. "
        "Render hosts this service. Google Gemini may provide the AI reading and "
        "explanation path where applicable. Sentry provides governed operational "
        "error monitoring. Product information is not medical advice.</p>"
        "<p>You can inspect, correct, delete and export remembered information in "
        "the app's Profile and Memory controls. Analysis requires your consent. "
        "You can request account deletion from Profile; deletion is processed through "
        "the governed account-deletion workflow and is not represented as immediate.</p>"
        "<p>For self-service help, see <a href=\"/support\">Support</a>.</p>",
    )


@app.get("/support", include_in_schema=False)
async def support_information() -> HTMLResponse:
    """Public self-service beta support; no form or contact channel is implied."""
    return _public_page(
        "GlamGenius beta support",
        "<h1>GlamGenius beta support</h1>"
        "<p>GlamGenius is an invite-only private beta with self-service support.</p>"
        "<h2>Sign-in and account access</h2><p>Use the sign-in flow provided in the "
        "app. If access is unavailable, check that you are using the invited account "
        "and complete the account steps shown there.</p>"
        "<h2>Scanning and product analysis</h2><p>Use a clear, well-lit image of the "
        "product or label and review the information before saving it. If analysis is "
        "unavailable, try again after checking your connection and analysis consent.</p>"
        "<h2>Notifications</h2><p>Notifications are optional. Check the app setting and "
        "your device notification permission before expecting an alert.</p>"
        "<h2>Memory and account controls</h2><p>Profile and Memory controls let you "
        "inspect, correct, delete and export remembered information. You can request "
        "account deletion from Profile; the governed deletion workflow processes that "
        "request asynchronously.</p>"
        "<p>Read <a href=\"/privacy\">privacy information</a> for how GlamGenius "
        "handles account, analysis and saved information.</p>",
    )

# Structured, logged, correlated failures for every route.
register_error_handlers(app)

# Outermost, deliberately: Starlette buffers a whole body before any handler,
# dependency or authentication runs, so every other size limit in this app
# bounds what gets stored rather than what gets allocated. Measured, a 60 MB
# anonymous request cost ~220 MB of RSS and still came back 401. On one 512 MB
# instance that is all an attacker needs, and they need no account to do it.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

app.add_middleware(
    CORSMiddleware,
    # Only these websites may call the API from a browser. The Expo app is
    # not a browser and sends no Origin header, so this does not affect it.
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)
app.add_middleware(RequestIdMiddleware)

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def _startup() -> None:
    logger.info("Starting GlamGenius V2...")

    from app.config import validate_production_configuration
    validate_production_configuration()

    if not SUPABASE_URL:
        logger.error(
            "SUPABASE_URL is empty. Every V2 route will refuse to authenticate "
            "until you set SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY "
            "in backend/.env."
        )
    if not SUPABASE_JWKS_URL:
        logger.error(
            "SUPABASE_JWKS_URL is not set — JWT "
            "verification is not configured. All requests will return 401."
        )

    if ALLOWED_ORIGINS_IS_DEFAULT:
        logger.warning(
            "ALLOWED_ORIGINS is not set — only local development addresses can "
            "call this API from a browser."
        )

    postgres_ok = await sql.ping()
    from app.shared.flags import service as flags_service

    missing_essentials = flags_service.warn_if_essentials_disabled()
    logger.info(
        "V2: postgres=%s storage=%s features=%s essentials_off=%s",
        "up" if postgres_ok else "DOWN",
        MEDIA_STORAGE_BACKEND,
        ",".join(V2_FEATURES) or "(env not set — using stable beta defaults)",
        ",".join(missing_essentials) or "(none)",
    )
    if not postgres_ok:
        logger.warning(
            "PostgreSQL is not reachable — every V2 route will fail. Check "
            "POSTGRES_URL in backend/.env."
        )


@app.on_event("shutdown")
async def _shutdown() -> None:
    await sql.dispose_engine()
