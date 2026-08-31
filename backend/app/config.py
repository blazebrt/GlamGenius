"""V2 application configuration.

The single Supabase-backed application reads its environment here and validates
it once, at startup, so a malformed value fails fast with a message pointing at
the variable to fix.

**No financial settings live in this file.** Prices, recurring plans and premium
plan machinery were removed by the Supabase cutover; adding them back is
outside the scope of the current architecture.
"""
from __future__ import annotations

import os
from pathlib import Path

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


def _env_csv(name: str, default: str = "") -> list[str]:
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

# VC-05 Google Calendar. Disabled is the safe default. The OAuth callback and
# return URI are fixed server configuration; clients can never supply either.
GOOGLE_CALENDAR_ENABLED = _env_bool("GOOGLE_CALENDAR_ENABLED", False)
GOOGLE_CALENDAR_CLIENT_ID = _env_str("GOOGLE_CALENDAR_CLIENT_ID")
GOOGLE_CALENDAR_CLIENT_SECRET = _env_str("GOOGLE_CALENDAR_CLIENT_SECRET")
GOOGLE_CALENDAR_REDIRECT_URI = _env_str("GOOGLE_CALENDAR_REDIRECT_URI")
GOOGLE_CALENDAR_APP_RETURN_URI = _env_str("GOOGLE_CALENDAR_APP_RETURN_URI", "glamgenius://calendar-result")
GOOGLE_CALENDAR_CREDENTIAL_STORE = _env_str("GOOGLE_CALENDAR_CREDENTIAL_STORE", "disabled").lower()
GOOGLE_CALENDAR_STATE_TTL_SECONDS = _env_int("GOOGLE_CALENDAR_STATE_TTL_SECONDS", 600)
GOOGLE_CALENDAR_INITIAL_HORIZON_DAYS = _env_int("GOOGLE_CALENDAR_INITIAL_HORIZON_DAYS", 90)
GOOGLE_CALENDAR_TIMEOUT_SECONDS = _env_float("GOOGLE_CALENDAR_TIMEOUT_SECONDS", 8.0)
GOOGLE_OAUTH_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_EVENTS_ENDPOINT = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"

# Live environment context.  The provider is deliberately opt-in: disabled is
# the safe default for local/test deployments, and commercial mode requires a
# non-empty key before a staging/production process can start.
LIVE_ENVIRONMENT_PROVIDER = _env_str("LIVE_ENVIRONMENT_PROVIDER")
OPEN_METEO_MODE = _env_str("OPEN_METEO_MODE", "disabled").lower()
OPEN_METEO_API_KEY = _env_str("OPEN_METEO_API_KEY")
OPEN_METEO_TIMEOUT_SECONDS = _env_float("OPEN_METEO_TIMEOUT_SECONDS", 5.0)
ENVIRONMENT_CACHE_TTL_SECONDS = _env_int("ENVIRONMENT_CACHE_TTL_SECONDS", 3600)
ENVIRONMENT_STALE_MAX_SECONDS = _env_int("ENVIRONMENT_STALE_MAX_SECONDS", 21600)


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
# Push delivery safety
# ---------------------------------------------------------------------------
# "live"    — the notification worker may call Expo. The production setting.
# "dry_run" — every push is logged and dropped at the transport boundary. No
#             socket is opened, so nothing can reach a real device. This is a
#             kill switch, not a preference: it is read inside push.send(), so
#             no caller can route around it.
PUSH_DELIVERY_MODE = _env_str("PUSH_DELIVERY_MODE", "live").lower()

# Accounts a human is allowed to target with a manual worker run. The manual
# trigger refuses to run against anything outside this list, and refuses to run
# at all when the list is empty — so "test the worker by hand" cannot become
# "notify every customer".
NOTIFICATION_TEST_ACCOUNT_IDS = {
    value.strip() for value in _env_csv("NOTIFICATION_TEST_ACCOUNT_IDS", "") if value.strip()
}


# ---------------------------------------------------------------------------
# Open Food Facts (Store A) — see docs/architecture/ODBL_DATA_WALL.md
# ---------------------------------------------------------------------------
# Open Food Facts data is ODbL licensed with a share-alike clause. Combining it
# with proprietary data into one database would oblige us to publish the whole
# thing. OFF_DATABASE_URL is what keeps the two stores physically apart: point
# it at a different server in production. Left empty it falls back to the
# application database, which is fine for development and logs a warning.
OFF_DATABASE_URL = _to_async_url(_env_str("OFF_DATABASE_URL")) if _env_str("OFF_DATABASE_URL") else ""

# Open Food Facts asks every API caller to identify itself. These build the
# User-Agent header; anonymous traffic gets rate-limited or blocked.
OFF_APP_NAME = _env_str("OFF_APP_NAME", "GlamGenius")
OFF_APP_VERSION = _env_str("OFF_APP_VERSION", "1.0")
OFF_CONTACT_EMAIL = _env_str("OFF_CONTACT_EMAIL")
# Kept below the app's own lookup timeout (LOOKUP_TIMEOUT_MS in
# frontend/src/services/productScan.ts). A budget the client will not wait
# out is worse than a short one: the phone abandons a lookup that was
# about to succeed, shows an offline answer and queues a sync for it.
OFF_TIMEOUT_SECONDS = _env_float("OFF_TIMEOUT_SECONDS", 4.0)

# Where the ODbL export job writes the redistributable dataset.
OFF_EXPORT_DIR = _env_str("OFF_EXPORT_DIR", "/data/off-export")


# ---------------------------------------------------------------------------
# Policy and Support URLs
# ---------------------------------------------------------------------------
PRIVACY_POLICY_URL = _env_str("PRIVACY_POLICY_URL", "https://glamgenius.placeholder/privacy")
SUPPORT_URL = _env_str("SUPPORT_URL", "https://glamgenius.placeholder/support")


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
GEMINI_API_KEY = _env_str("GEMINI_API_KEY") or _env_str("GOOGLE_API_KEY")
GEMINI_MODEL = _env_str("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = _env_csv(
    "GEMINI_FALLBACK_MODELS", "gemini-2.5-flash,gemini-2.5-pro"
)
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


def validate_production_configuration() -> None:
    """Validate that the environment is safe for production use.
    
    Raises RuntimeError if a critical production invariant is missing or unsafe.
    """
    if PUSH_DELIVERY_MODE not in ("live", "dry_run"):
        raise RuntimeError("CRITICAL: PUSH_DELIVERY_MODE must be live or dry_run.")
    if OPEN_METEO_MODE not in ("disabled", "evaluation", "commercial"):
        raise RuntimeError("CRITICAL: OPEN_METEO_MODE must be disabled, evaluation, or commercial.")
    if LIVE_ENVIRONMENT_PROVIDER and LIVE_ENVIRONMENT_PROVIDER != "open_meteo":
        raise RuntimeError("CRITICAL: LIVE_ENVIRONMENT_PROVIDER must be open_meteo or empty.")
    if APP_ENV not in ("production", "staging"):
        return
    if PUSH_DELIVERY_MODE == "dry_run":
        raise RuntimeError(
            "CRITICAL: PUSH_DELIVERY_MODE=dry_run silently drops every notification. "
            "It is a testing switch and must not be set in staging or production."
        )
    if OPEN_METEO_MODE == "evaluation" and APP_ENV in ("production", "staging"):
        raise RuntimeError("CRITICAL: OPEN_METEO_MODE=evaluation is not permitted in staging or production.")
    if OPEN_METEO_MODE == "commercial" and not OPEN_METEO_API_KEY:
        raise RuntimeError("CRITICAL: OPEN_METEO_API_KEY is required when OPEN_METEO_MODE=commercial.")
    if GOOGLE_CALENDAR_ENABLED:
        if not all((GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, GOOGLE_CALENDAR_REDIRECT_URI)):
            raise RuntimeError("CRITICAL: Google Calendar requires client ID, secret, and redirect URI when enabled.")
        if not GOOGLE_CALENDAR_APP_RETURN_URI or "?" in GOOGLE_CALENDAR_APP_RETURN_URI or "#" in GOOGLE_CALENDAR_APP_RETURN_URI:
            raise RuntimeError("CRITICAL: GOOGLE_CALENDAR_APP_RETURN_URI must be a fixed URI without query or fragment.")
        if GOOGLE_CALENDAR_CREDENTIAL_STORE != "supabase_vault":
            raise RuntimeError("CRITICAL: Google Calendar requires GOOGLE_CALENDAR_CREDENTIAL_STORE=supabase_vault.")

    # 1. Reject HS256 by default. Production must use asymmetric JWKS.
    if not SUPABASE_JWKS_URL:
        raise RuntimeError(
            "CRITICAL: SUPABASE_JWKS_URL is required in production. "
            "Production tokens must be verified asymmetrically."
        )
        
    # Validate JWKS URL format and issuer match
    import urllib.parse
    jwks_parsed = urllib.parse.urlparse(SUPABASE_JWKS_URL)
    if not jwks_parsed.scheme or not jwks_parsed.netloc:
        raise RuntimeError("CRITICAL: SUPABASE_JWKS_URL is malformed.")

    # 2. Critical variables must be set.
    if not SUPABASE_URL or not SUPABASE_ANON_KEY or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "CRITICAL: SUPABASE_URL, SUPABASE_ANON_KEY, and SUPABASE_SERVICE_ROLE_KEY "
            "must all be set in production."
        )
        
    sb_parsed = urllib.parse.urlparse(SUPABASE_URL)
    if sb_parsed.scheme != "https":
        raise RuntimeError("CRITICAL: SUPABASE_URL must be an HTTPS URL.")
    if not sb_parsed.netloc or "example.com" in sb_parsed.netloc or "placeholder" in sb_parsed.netloc:
        raise RuntimeError("CRITICAL: SUPABASE_URL cannot be a placeholder or example URL.")
        
    if "placeholder" in SUPABASE_ANON_KEY.lower() or "fake" in SUPABASE_ANON_KEY.lower():
        raise RuntimeError("CRITICAL: SUPABASE_ANON_KEY cannot be a placeholder.")
    if "placeholder" in SUPABASE_SERVICE_ROLE_KEY.lower() or "fake" in SUPABASE_SERVICE_ROLE_KEY.lower():
        raise RuntimeError("CRITICAL: SUPABASE_SERVICE_ROLE_KEY cannot be a placeholder.")

    if not SUPABASE_JWT_ISSUER:
        raise RuntimeError("CRITICAL: SUPABASE_JWT_ISSUER must be set in production.")
        
    iss_parsed = urllib.parse.urlparse(SUPABASE_JWT_ISSUER)
    if not iss_parsed.scheme or not iss_parsed.netloc:
        raise RuntimeError("CRITICAL: SUPABASE_JWT_ISSUER is malformed.")

    if not POSTGRES_URL:
        raise RuntimeError("CRITICAL: POSTGRES_URL must be set in production.")
    if not OFF_DATABASE_URL:
        raise RuntimeError("CRITICAL: OFF_DATABASE_URL is required in staging and production.")
    if OFF_DATABASE_URL == POSTGRES_URL:
        raise RuntimeError("CRITICAL: OFF_DATABASE_URL must be physically distinct from POSTGRES_URL.")
    
    import ipaddress
    parsed = urllib.parse.urlparse(POSTGRES_URL)
    
    if parsed.scheme not in ("postgresql", "postgresql+asyncpg", "postgres"):
        raise RuntimeError(f"CRITICAL: POSTGRES_URL scheme '{parsed.scheme}' is not allowed in production.")
    
    if not parsed.hostname:
        raise RuntimeError("CRITICAL: POSTGRES_URL must have a valid hostname.")
    if parsed.hostname.lower() == "localhost" or "placeholder" in parsed.hostname.lower():
        raise RuntimeError("CRITICAL: POSTGRES_URL cannot be localhost or a placeholder in production.")
    try:
        if parsed.port and (parsed.port < 1 or parsed.port > 65535):
            raise RuntimeError("CRITICAL: POSTGRES_URL has a malformed port.")
    except ValueError:
        raise RuntimeError("CRITICAL: POSTGRES_URL has a malformed port.")
        
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_loopback or ip.is_unspecified:
            raise RuntimeError(f"CRITICAL: POSTGRES_URL cannot be a loopback or unspecified IP ({parsed.hostname}).")
        if str(ip).startswith("127."):
             raise RuntimeError(f"CRITICAL: POSTGRES_URL cannot be in 127.0.0.0/8 ({parsed.hostname}).")
    except ValueError:
        pass
        
    if parsed.username == "postgres" and parsed.password == "postgres":
        raise RuntimeError("CRITICAL: POSTGRES_URL cannot use example credentials (postgres:postgres).")

    if not SUPABASE_STORAGE_BUCKET:
        raise RuntimeError("CRITICAL: SUPABASE_STORAGE_BUCKET must be set in production.")

    if not GEMINI_API_KEY:
        raise RuntimeError("CRITICAL: GEMINI_API_KEY must be set in production.")

    import os
    sentry_dsn = os.environ.get("SENTRY_BACKEND_DSN", "").strip()
    if not sentry_dsn:
        raise RuntimeError("CRITICAL: SENTRY_BACKEND_DSN must be set in production.")
        
    sentry_parsed = urllib.parse.urlparse(sentry_dsn)
    if not sentry_parsed.scheme or not sentry_parsed.netloc or not sentry_parsed.username:
        raise RuntimeError("CRITICAL: SENTRY_BACKEND_DSN format is invalid.")

    if not INVITE_REQUIRED:
        raise RuntimeError("CRITICAL: INVITE_REQUIRED=true is mandatory in production.")

    if not REQUIRE_ANALYSIS_CONSENT or not CONSENT_VERSION:
        raise RuntimeError("CRITICAL: REQUIRE_ANALYSIS_CONSENT=true and CONSENT_VERSION are mandatory in production.")

    if not PRIVACY_POLICY_URL or "placeholder" in PRIVACY_POLICY_URL:
        raise RuntimeError("CRITICAL: PRIVACY_POLICY_URL must be a real URL in production.")
        
    if not SUPPORT_URL or "placeholder" in SUPPORT_URL:
        raise RuntimeError("CRITICAL: SUPPORT_URL must be a real URL in production.")


    if MEDIA_STORAGE_BACKEND.lower() != "supabase":
        raise RuntimeError(
            "CRITICAL: MEDIA_STORAGE_BACKEND must be 'supabase' in production."
        )

    if ALLOWED_ORIGINS_IS_DEFAULT or not ALLOWED_ORIGINS:
        raise RuntimeError("CRITICAL: ALLOWED_ORIGINS must be set and non-empty in production.")

    if "*" in ALLOWED_ORIGINS:
        raise RuntimeError("CRITICAL: ALLOWED_ORIGINS cannot contain wildcards in production.")
        
    for origin in ALLOWED_ORIGINS:
        o_parsed = urllib.parse.urlparse(origin)
        if o_parsed.scheme not in ("http", "https") or not o_parsed.netloc:
            raise RuntimeError(f"CRITICAL: Invalid origin scheme or format: {origin}")
        if o_parsed.path and o_parsed.path != "/":
            raise RuntimeError(f"CRITICAL: Origin cannot contain paths: {origin}")
        if o_parsed.username or o_parsed.password:
            raise RuntimeError(f"CRITICAL: Origin cannot contain credentials: {origin}")
        host = o_parsed.hostname.lower() if o_parsed.hostname else ""
        if host == "localhost" or host == "127.0.0.1":
            raise RuntimeError(f"CRITICAL: ALLOWED_ORIGINS cannot contain localhost/loopback in production: {origin}")
            
    # Validate Admin User IDs
    import uuid
    for admin_id in SUPABASE_ADMIN_USER_IDS:
        try:
            uuid.UUID(admin_id)
        except ValueError:
            raise RuntimeError(f"CRITICAL: Invalid UUID in SUPABASE_ADMIN_USER_IDS: {admin_id}")

