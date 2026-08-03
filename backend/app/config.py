"""V2 application configuration.

The single Supabase-backed application reads its environment here and validates
it once, at startup, so a malformed value fails fast with a message pointing at
the variable to fix.

**No payment settings live in this file.** Prices, subscriptions, Razorpay and
paid entitlements were removed in the Supabase cutover; Prompt 2 will delete
the remaining code paths that expected them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv


# Load the same .env file the rest of the backend uses. Path-anchored so this
# works from tests, Alembic and Uvicorn alike.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_ROOT / ".env")


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"{name} must be a whole number, got {raw!r}. "
            f"Fix your .env file or remove {name} to use the default ({default})."
        ) from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"{name} must be a number, got {raw!r}. "
            f"Fix your .env file or remove {name} to use the default ({default})."
        ) from exc


def _env_csv(name: str, default: str = "") -> List[str]:
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return [part.strip() for part in raw.split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Supabase — identity, database, storage
# ---------------------------------------------------------------------------
SUPABASE_URL = _env_str("SUPABASE_URL")
SUPABASE_ANON_KEY = _env_str("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _env_str("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_JWT_ISSUER = _env_str(
    "SUPABASE_JWT_ISSUER",
    f"{SUPABASE_URL}/auth/v1" if SUPABASE_URL else "",
)
SUPABASE_JWKS_URL = _env_str(
    "SUPABASE_JWKS_URL",
    f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else "",
)
# Optional HS256 fallback. Only used when JWKS does not match a token's kid,
# which is the case for legacy Supabase projects that still use the shared
# HS256 secret.
SUPABASE_JWT_SECRET = _env_str("SUPABASE_JWT_SECRET")
SUPABASE_STORAGE_BUCKET = _env_str("SUPABASE_STORAGE_BUCKET", "glamgenius-media")

SUPABASE_ADMIN_USER_IDS = {
    uid.lower() for uid in _env_csv("SUPABASE_ADMIN_USER_IDS", "")
}


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
# The user may paste a bare ``postgresql://`` URL from Supabase. asyncpg needs
# the ``+asyncpg`` driver suffix; Alembic needs the sync driver. We normalise
# both here so no code path has to think about it.
_raw_pg = _env_str(
    "POSTGRES_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)


def _to_async_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


def _to_sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


POSTGRES_URL = _to_async_url(_raw_pg)
POSTGRES_SYNC_URL = _to_sync_url(_raw_pg)
POSTGRES_POOL_SIZE = _env_int("POSTGRES_POOL_SIZE", 5)
POSTGRES_MAX_OVERFLOW = _env_int("POSTGRES_MAX_OVERFLOW", 5)
POSTGRES_ECHO = _env_bool("POSTGRES_ECHO", False)


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
V2_FEATURES = _env_csv("V2_FEATURES", "")


# ---------------------------------------------------------------------------
# Deployment tier
# ---------------------------------------------------------------------------
APP_ENV = _env_str("APP_ENV", "development").lower()


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------
MEDIA_STORAGE_BACKEND = _env_str("MEDIA_STORAGE_BACKEND", "supabase").lower()
MEDIA_LOCAL_ROOT = _env_str("MEDIA_LOCAL_ROOT", "/data/media")
MEDIA_MAX_BYTES = _env_int("MEDIA_MAX_BYTES", 8 * 1024 * 1024)
MEDIA_ALLOWED_MIME = _env_csv(
    "MEDIA_ALLOWED_MIME", "image/jpeg,image/png,image/webp"
)
MEDIA_SIGNED_URL_TTL_SECONDS = _env_int("MEDIA_SIGNED_URL_TTL_SECONDS", 300)
# Development/test only. A production pod loses uploads on redeploy so the
# storage factory refuses ``local`` when APP_ENV=production.
MEDIA_ALLOW_LOCAL_IN_PRODUCTION = _env_bool("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", False)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------
REQUIRE_ANALYSIS_CONSENT = _env_bool("REQUIRE_ANALYSIS_CONSENT", True)
CONSENT_VERSION = _env_str("CONSENT_VERSION", "2026-01-01")


# ---------------------------------------------------------------------------
# Invite gate
# ---------------------------------------------------------------------------
INVITE_REQUIRED = _env_bool("INVITE_REQUIRED", True)


# ---------------------------------------------------------------------------
# Beta usage controls — non-payment abuse and cost limits
# ---------------------------------------------------------------------------
BETA_AI_REQUESTS_PER_HOUR = _env_int("BETA_AI_REQUESTS_PER_HOUR", 60)
BETA_SCAN_LIMIT_PER_MONTH = _env_int("BETA_SCAN_LIMIT_PER_MONTH", 60)
BETA_STYLE_LIMIT_PER_MONTH = _env_int("BETA_STYLE_LIMIT_PER_MONTH", 60)
BETA_SHOPPING_CHECK_LIMIT_PER_MONTH = _env_int(
    "BETA_SHOPPING_CHECK_LIMIT_PER_MONTH", 60
)


# ---------------------------------------------------------------------------
# AI gateway
# ---------------------------------------------------------------------------
AI_TIMEOUT_SECONDS = _env_float("AI_TIMEOUT_SECONDS", 45.0)
AI_COST_PER_1K_INPUT_USD = _env_float("AI_COST_PER_1K_INPUT_USD", 0.0003)
AI_COST_PER_1K_OUTPUT_USD = _env_float("AI_COST_PER_1K_OUTPUT_USD", 0.0025)


# ---------------------------------------------------------------------------
# Request limits
# ---------------------------------------------------------------------------
MAX_IMAGE_BASE64_CHARS = _env_int("MAX_IMAGE_BASE64_CHARS", 12_000_000)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
_DEV_ORIGINS = "http://localhost:8081,http://localhost:19006,http://127.0.0.1:8081"
_allowed_origins_raw = _env_str("ALLOWED_ORIGINS", _DEV_ORIGINS)
ALLOWED_ORIGINS = [
    o.strip().rstrip("/") for o in _allowed_origins_raw.split(",") if o.strip()
]
ALLOWED_ORIGINS_IS_DEFAULT = "ALLOWED_ORIGINS" not in os.environ
