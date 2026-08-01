"""GET /api/v2/config — what the app is allowed to show.

The single source of truth for client-side capability. The audit found the app
selling ₹249/month subscriptions that the backend refuses on principle
(``SUBSCRIPTIONS_UNAVAILABLE``), because nothing ever asked the backend. Now it
asks, once, at startup.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    CONSENT_VERSION,
    MEDIA_ALLOWED_MIME,
    MEDIA_MAX_BYTES,
    REQUIRE_ANALYSIS_CONSENT,
    SUBSCRIPTIONS_AVAILABLE,
)
from app.domains.ai_gateway.providers import gemini
from app.shared.database import sql
from app.shared.database.sql import get_session
from app.shared.flags import service as flags
from settings import INVITE_ONLY

router = APIRouter()


@router.get("/config")
async def get_config(session: AsyncSession = Depends(get_session)):
    return {
        "api_version": "v2",
        "billing": {
            # False in the private beta. The app must hide every payment action
            # when this is false rather than deciding for itself.
            "subscriptions_available": SUBSCRIPTIONS_AVAILABLE,
            "invite_only": INVITE_ONLY,
            "beta_message": (
                "GlamGenius is in a private beta. Your invite gives you full "
                "access — there is nothing to pay for yet."
            ),
        },
        "analysis": {
            # False means photo analysis will fail; the app should say so up
            # front instead of letting someone take a photo for nothing.
            "provider_configured": gemini.is_configured(),
            "consent_required": REQUIRE_ANALYSIS_CONSENT,
            "consent_version": CONSENT_VERSION,
        },
        "media": {
            "max_bytes": MEDIA_MAX_BYTES,
            "allowed_types": MEDIA_ALLOWED_MIME,
            # Face photos are never stored. Inventory photos are. The app shows
            # this verbatim so the answer to "do you keep my pictures" is not
            # buried in a policy page.
            "face_photos_stored": False,
            "storage_note": (
                "Photos you send for a skin or hair check are analysed and then "
                "discarded — we never store your face. Photos you add to your "
                "own collection are stored until you delete them."
            ),
        },
        "features": await flags.all_flags(session),
    }


@router.get("/health")
async def v2_health():
    """V2 health, including PostgreSQL. The V1 /api/health is unchanged."""
    postgres_ok = await sql.ping()
    return {
        "status": "healthy" if postgres_ok else "degraded",
        "postgres": "up" if postgres_ok else "down",
        "ai_provider_configured": gemini.is_configured(),
    }
