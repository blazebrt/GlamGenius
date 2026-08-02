"""V2 API router.

Mounted at ``/api/v2`` on the existing FastAPI app. V1 at ``/api`` is untouched
and keeps working exactly as before.
"""
from fastapi import APIRouter

from app.api.v2 import (
    account, config, consent, integrations, inventory, jobs, me, media, onboarding,
    planner, privacy, profile, progress, routines, shelf, shopping, style, today,
)

router = APIRouter(prefix="/api/v2")

router.include_router(config.router, tags=["v2-config"])
router.include_router(me.router, tags=["v2-me"])
router.include_router(consent.router, tags=["v2-consent"])
router.include_router(media.router, tags=["v2-media"])
router.include_router(jobs.router, tags=["v2-jobs"])
router.include_router(privacy.router, tags=["v2-privacy"])
router.include_router(account.router, tags=["v2-account"])
router.include_router(profile.router, tags=["v2-profile"])
router.include_router(onboarding.router, tags=["v2-onboarding"])
router.include_router(inventory.router, tags=["v2-inventory"])
router.include_router(style.router, tags=["v2-style"])
router.include_router(shopping.router, tags=["v2-shopping"])
router.include_router(today.router, tags=["v2-today"])
router.include_router(planner.router, tags=["v2-planner"])
router.include_router(integrations.router, tags=["v2-integrations"])
router.include_router(shelf.router, tags=["v2-shelf"])
router.include_router(routines.router, tags=["v2-routines"])
router.include_router(progress.router, tags=["v2-progress"])

__all__ = ["router"]
