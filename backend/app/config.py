"""V2 configuration.

Reads the same ``.env`` the V1 ``settings.py`` reads, in the same plain style, so
there is one environment file and one way of doing things. Values are parsed and
validated here rather than at the point of use, so a bad value fails at startup
with a message that says what to fix.
"""
from __future__ import annotations

import os
from typing import List


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
            f"Fix it in your .env file or remove it to use the default ({default})."
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
            f"Fix it in your .env file or remove it to use the default ({default})."
        ) from exc


def _env_csv(name: str, default: str = "") -> List[str]:
    raw = os.environ.get(name)
    if raw is None:
        raw = default
    return [part.strip() for part in raw.split(",") if part.strip()]


# --- PostgreSQL (V2 only — MongoDB is untouched and still authoritative for V1) ---
# Async driver. asyncpg speaks the binary protocol and is what SQLAlchemy 2.x
# expects behind create_async_engine.
POSTGRES_URL = _env_str(
    "POSTGRES_URL",
    "postgresql+asyncpg://glamgenius:glamgenius@postgres:5432/glamgenius_v2",
)
POSTGRES_POOL_SIZE = _env_int("POSTGRES_POOL_SIZE", 5)
POSTGRES_MAX_OVERFLOW = _env_int("POSTGRES_MAX_OVERFLOW", 5)
POSTGRES_ECHO = _env_bool("POSTGRES_ECHO", False)

# --- Feature flags -----------------------------------------------------------
# Comma-separated flag keys switched on at boot. The database table can override
# these at runtime; this is the bootstrap default so a fresh deployment starts
# closed rather than open.
V2_FEATURES = _env_csv("V2_FEATURES", "")

# --- Media storage -----------------------------------------------------------
MEDIA_STORAGE_BACKEND = _env_str("MEDIA_STORAGE_BACKEND", "local").lower()
MEDIA_LOCAL_ROOT = _env_str("MEDIA_LOCAL_ROOT", "/data/media")
MEDIA_MAX_BYTES = _env_int("MEDIA_MAX_BYTES", 8 * 1024 * 1024)
MEDIA_ALLOWED_MIME = _env_csv(
    "MEDIA_ALLOWED_MIME", "image/jpeg,image/png,image/webp"
)

S3_ENDPOINT_URL = _env_str("S3_ENDPOINT_URL")
S3_BUCKET = _env_str("S3_BUCKET")
S3_REGION = _env_str("S3_REGION", "auto")
S3_ACCESS_KEY_ID = _env_str("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = _env_str("S3_SECRET_ACCESS_KEY")

# --- Consent -----------------------------------------------------------------
# Off by default so existing accounts are not locked out the moment this ships.
# The app collects consent from this phase onward; flipping this to true is a
# one-line change once enough accounts have a recorded answer.
REQUIRE_ANALYSIS_CONSENT = _env_bool("REQUIRE_ANALYSIS_CONSENT", False)
CONSENT_VERSION = _env_str("CONSENT_VERSION", "2026-08-01")

# --- Billing -----------------------------------------------------------------
# Single source of truth for whether any paid action may be offered. The app
# reads this from /api/v2/config and hides payment UI when it is false.
SUBSCRIPTIONS_AVAILABLE = _env_bool("SUBSCRIPTIONS_AVAILABLE", False)

# --- AI gateway --------------------------------------------------------------
AI_TIMEOUT_SECONDS = _env_float("AI_TIMEOUT_SECONDS", 45.0)
# Rough per-1K-token prices used only to estimate spend for the ai_runs ledger.
# Not billing. Override when Google changes pricing.
AI_COST_PER_1K_INPUT_USD = _env_float("AI_COST_PER_1K_INPUT_USD", 0.0003)
AI_COST_PER_1K_OUTPUT_USD = _env_float("AI_COST_PER_1K_OUTPUT_USD", 0.0025)

# --- Request limits ----------------------------------------------------------
# A base64 image arrives as a JSON string. Without a ceiling, one request can
# push an arbitrarily large payload straight to a paid provider.
MAX_IMAGE_BASE64_CHARS = _env_int("MAX_IMAGE_BASE64_CHARS", 12_000_000)
