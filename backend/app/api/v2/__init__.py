"""V2 API router.

Mounted at ``/api/v2`` on the existing FastAPI app. V1 at ``/api`` is untouched
and keeps working exactly as before.
"""
from fastapi import APIRouter

from app.api.v2 import account, config, consent, inventory, jobs, me, media, onboarding, privacy, profile

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

__all__ = ["router"]
