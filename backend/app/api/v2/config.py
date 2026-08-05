"""GET /api/v2/config + /api/v2/health — client-side capability + ops health."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    CONSENT_VERSION,
    INVITE_REQUIRED,
    MEDIA_ALLOWED_MIME,
    MEDIA_MAX_BYTES,
    REQUIRE_ANALYSIS_CONSENT,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
)
from app.domains.ai_gateway.providers import gemini
from app.shared.database import sql
from app.shared.database.sql import get_session
from app.shared.flags import service as flags

router = APIRouter()


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
async def v2_health(response: Response):
    """V2 health: PostgreSQL reachability and AI provider status.

    No billing block — payments are not part of this architecture.
    """
    postgres_ok = await sql.ping()
    if not postgres_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if postgres_ok else "degraded",
        "postgres": "up" if postgres_ok else "down",
        "ai_provider_configured": gemini.is_configured(),
    }


@router.get("/sentry-debug")
async def trigger_error():
    """Synthetic exception to verify Sentry configuration."""
    raise RuntimeError("This is a test exception for Sentry.")
