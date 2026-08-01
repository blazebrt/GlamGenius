"""V2 API router.

Mounted at ``/api/v2`` on the existing FastAPI app. V1 at ``/api`` is untouched
and keeps working exactly as before.
"""
from fastapi import APIRouter

from app.api.v2 import account, config, consent, jobs, me, media, privacy

router = APIRouter(prefix="/api/v2")

router.include_router(config.router, tags=["v2-config"])
router.include_router(me.router, tags=["v2-me"])
router.include_router(consent.router, tags=["v2-consent"])
router.include_router(media.router, tags=["v2-media"])
router.include_router(jobs.router, tags=["v2-jobs"])
router.include_router(privacy.router, tags=["v2-privacy"])
router.include_router(account.router, tags=["v2-account"])

__all__ = ["router"]
