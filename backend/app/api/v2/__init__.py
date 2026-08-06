"""V2 API router.

Mounted at ``/api/v2``. Every route here authenticates through
``app.shared.security.supabase_auth`` and owns its own PostgreSQL access. There
is no V1 surface — the old ``/api`` prefix was retired in the Supabase cutover.
"""
from fastapi import APIRouter

from app.api.v2 import (
    access,
    admin,
    config,
    consent,
    integrations,
    inventory,
    jobs,
    me,
    media,
    onboarding,
    planner,
    privacy,
    profile,
    progress,
    quiz,
    routines,
    scan,
    shelf,
    shopping,
    style,
    today,
)

router = APIRouter(prefix="/api/v2")

router.include_router(config.router, tags=["v2-config"])
router.include_router(me.router, tags=["v2-me"])
router.include_router(access.router, tags=["v2-access"])
router.include_router(consent.router, tags=["v2-consent"])
router.include_router(media.router, tags=["v2-media"])
router.include_router(jobs.router, tags=["v2-jobs"])
router.include_router(privacy.router, tags=["v2-privacy"])
router.include_router(profile.router, tags=["v2-profile"])
router.include_router(onboarding.router, tags=["v2-onboarding"])
router.include_router(inventory.router, tags=["v2-inventory"])
router.include_router(scan.router, tags=["v2-scan"])
router.include_router(quiz.router, tags=["v2-quiz"])
router.include_router(style.router, tags=["v2-style"])
router.include_router(shopping.router, tags=["v2-shopping"])
router.include_router(today.router, tags=["v2-today"])
router.include_router(planner.router, tags=["v2-planner"])
router.include_router(integrations.router, tags=["v2-integrations"])
router.include_router(shelf.router, tags=["v2-shelf"])
router.include_router(routines.router, tags=["v2-routines"])
router.include_router(progress.router, tags=["v2-progress"])
router.include_router(admin.router, tags=["v2-admin"])

__all__ = ["router"]
