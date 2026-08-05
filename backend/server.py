"""GlamGenius API — Personal Appearance Operating System

A single FastAPI application. **V2 only.** Every route is mounted under
``/api/v2`` and authenticated with a verified Supabase JWT. No MongoDB, no
local password store, no payment stack. Prompt 2 will remove the last few
legacy artefacts still lingering in the tree.
"""
from __future__ import annotations

# Sentry must be initialised before FastAPI so its middleware can attach to
# the exception path. A missing DSN is fine — init_sentry() no-ops.
from app.shared.observability.sentry_bootstrap import init_sentry

init_sentry()

import logging

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.v2 import router as v2_router
from app.config import (
    ALLOWED_ORIGINS,
    ALLOWED_ORIGINS_IS_DEFAULT,
    MEDIA_STORAGE_BACKEND,
    SUPABASE_JWT_SECRET,
    SUPABASE_JWKS_URL,
    SUPABASE_URL,
    V2_FEATURES,
)
from app.shared.database import sql
from app.shared.errors.handlers import register_error_handlers
from app.shared.observability.logging import configure_logging
from app.shared.observability.request_id import RequestIdMiddleware

app = FastAPI(
    title="GlamGenius — Personal Appearance Operating System",
    version="2.0.0-supabase",
)

app.include_router(v2_router)

# Structured, logged, correlated failures for every route.
register_error_handlers(app)

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
    logger.info("Starting GlamGenius V2 (Supabase cutover)...")

    from app.config import validate_production_configuration
    validate_production_configuration()

    if not SUPABASE_URL:
        logger.error(
            "SUPABASE_URL is empty. Every V2 route will refuse to authenticate "
            "until you set SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY "
            "in backend/.env."
        )
    if not (SUPABASE_JWKS_URL or SUPABASE_JWT_SECRET):
        logger.error(
            "Neither SUPABASE_JWKS_URL nor SUPABASE_JWT_SECRET is set — JWT "
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
