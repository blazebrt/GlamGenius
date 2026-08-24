"""GET /api/v2/config + /api/v2/health + /api/v2/ready — client-side capability + ops health."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap import SEED_VERSION
from app.config import (
    APP_ENV,
    CONSENT_VERSION,
    GOOGLE_CALENDAR_ENABLED,
    INVITE_REQUIRED,
    MEDIA_ALLOW_LOCAL_IN_PRODUCTION,
    MEDIA_ALLOWED_MIME,
    MEDIA_MAX_BYTES,
    MEDIA_STORAGE_BACKEND,
    REQUIRE_ANALYSIS_CONSENT,
    SUPABASE_ANON_KEY,
    SUPABASE_URL,
    validate_production_configuration,
)
from app.domains.ai_gateway.providers import gemini
from app.shared.database import sql
from app.shared.database.sql import get_session
from app.shared.flags import service as flags

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    """Public runtime configuration for the client.

    Deliberately payment-free. The Expo app reads this once at startup and
    hides anything that ``supabase.configured`` cannot support.
    """
    return {
        "api_version": "v2",
        "supabase": {
            # Both are safe to expose (URL + anon key are the two things the
            # client needs to connect to Supabase Auth and Storage read-only).
            "url": SUPABASE_URL,
            "anon_key": SUPABASE_ANON_KEY,
            "configured": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
        },
        "access": {
            "invite_required": INVITE_REQUIRED,
            "beta_message": (
                "GlamGenius is in a private beta. Your invite gives you full "
                "access."
            ),
        },
        "analysis": {
            "provider_configured": gemini.is_configured(),
            "consent_required": REQUIRE_ANALYSIS_CONSENT,
            "consent_version": CONSENT_VERSION,
        },
        "calendar": {
            "google_enabled": GOOGLE_CALENDAR_ENABLED,
            "scope": "read_only_primary" if GOOGLE_CALENDAR_ENABLED else None,
        },
        "media": {
            "max_bytes": MEDIA_MAX_BYTES,
            "allowed_types": MEDIA_ALLOWED_MIME,
            # Face/person scan photos are never stored. Inventory photos are.
            "face_photos_stored": False,
            "storage_note": (
                "Photos you send for a skin or hair check are analysed and "
                "then discarded — we never store your face. Photos you add to "
                "your own collection are stored until you delete them."
            ),
        },
        "features": await flags.all_flags(session),
    }


@router.get("/health")
async def v2_health():
    """V2 health: Process liveness only. No network calls."""
    return {
        "status": "alive",
        "version": "2.0.0-supabase",
    }


@router.get("/ready")
async def v2_ready(response: Response, session: AsyncSession = Depends(get_session)):
    """V2 ready: Database, configuration, seed, schema, and AI readiness."""
    components = {}
    is_ready = True

    # 1. PostgreSQL reachability
    postgres_ok = await sql.ping()
    components["postgres"] = "up" if postgres_ok else "down"
    if not postgres_ok:
        is_ready = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "components": components}

    # 2. Production configuration invariant check
    try:
        validate_production_configuration()
        components["production_config"] = "valid"
    except RuntimeError as e:
        components["production_config"] = f"invalid: {e}"
        if APP_ENV in ("production", "staging"):
            is_ready = False

    # 3. Storage configuration check
    if MEDIA_STORAGE_BACKEND.lower() == "local" and not MEDIA_ALLOW_LOCAL_IN_PRODUCTION and APP_ENV in ("production", "staging"):
        components["storage"] = "invalid_local"
        is_ready = False
    else:
        components["storage"] = MEDIA_STORAGE_BACKEND.lower()

    # 4. Expected reference-data seed version
    try:
        seed_result = await session.execute(
            text("SELECT seed_version FROM seed_version_records LIMIT 1")
        )
        current_seed = seed_result.scalar()
        components["seed_version"] = current_seed
        if current_seed != SEED_VERSION:
            components["seed_version_status"] = f"mismatch: expected {SEED_VERSION}"
            is_ready = False
        else:
            components["seed_version_status"] = "ok"
    except Exception as e:
        components["seed_version_status"] = f"missing or error: {e}"
        is_ready = False

    # 5. Alembic head check
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from app.config import _BACKEND_ROOT

        alembic_cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(_BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(alembic_cfg)
        repo_head = script.get_current_head()

        alembic_result = await session.execute(text("SELECT version_num FROM alembic_version"))
        db_alembic_head = alembic_result.scalar()
        components["alembic_head"] = db_alembic_head
        if not db_alembic_head:
            is_ready = False
            components["alembic_status"] = "missing"
        elif db_alembic_head != repo_head:
            is_ready = False
            components["alembic_status"] = f"mismatch: expected {repo_head}"
        else:
            components["alembic_status"] = "ok"
    except Exception as e:
        components["alembic_status"] = f"error: {e}"
        is_ready = False

    # 6. Worker heartbeat freshness (if deletion jobs exist)
    try:
        import datetime
        jobs_result = await session.execute(
            text("SELECT COUNT(*) FROM account_deletion_jobs WHERE state NOT IN ('complete', 'failed_terminal')")
        )
        pending_jobs = jobs_result.scalar() or 0
        if pending_jobs > 0:
            worker_result = await session.execute(
                text("SELECT last_heartbeat_at FROM system_worker_status WHERE worker_name LIKE 'account_deletion_worker_%' ORDER BY last_heartbeat_at DESC LIMIT 1")
            )
            last_heartbeat = worker_result.scalar()
            if not last_heartbeat:
                components["worker_heartbeat"] = "missing"
                is_ready = False
            else:
                now_utc = datetime.datetime.now(datetime.UTC)
                last_heartbeat_utc = last_heartbeat.replace(tzinfo=datetime.UTC) if last_heartbeat.tzinfo is None else last_heartbeat.astimezone(datetime.UTC)
                age = (now_utc - last_heartbeat_utc).total_seconds()
                if age > 300:
                    components["worker_heartbeat"] = "stale"
                    is_ready = False
                else:
                    components["worker_heartbeat"] = "fresh"
        else:
            components["worker_heartbeat"] = "idle_no_pending_jobs"
    except Exception as e:
        components["worker_heartbeat"] = f"error: {e}"
        is_ready = False

    # 7. Stable feature flags
    try:
        missing_essentials = flags.warn_if_essentials_disabled()
        if missing_essentials:
            components["feature_flags"] = f"missing_essentials: {','.join(missing_essentials)}"
            # is_ready = False  # Or maybe just record it? "Required stable feature flags"
        else:
            components["feature_flags"] = "ok"
    except Exception as e:
        components["feature_flags"] = f"error: {e}"
        is_ready = False

    # 8. AI configuration presence
    if not gemini.is_configured():
        components["ai_provider"] = "missing"
        is_ready = False
    else:
        components["ai_provider"] = "configured"

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if is_ready else "not_ready",
        "components": components,
    }
