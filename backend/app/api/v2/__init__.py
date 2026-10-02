"""V2 API router.

Mounted at ``/api/v2``. Every route here authenticates through
``app.shared.security.supabase_auth`` and owns its own PostgreSQL access. There
is no V1 surface — the old ``/api`` prefix was retired in the Supabase cutover.
"""
from fastapi import APIRouter

from app.api.v2 import (
    access,
    admin,
    b2b_admin,
    commerce,
    community,
    config,
    consent,
    family,
    growth,
    integrations,
    internal_scheduler,
    inventory,
    jobs,
    maintenance,
    me,
    media,
    notification_settings,
    personal_applicability_admin,
    personal_decision_release_admin,
    privacy,
    product,
    profile,
    routines,
    scan,
    shelf,
    shopping,
    skin_care_personal_decision,
    skin_care_scan,
    supplements,
)

router = APIRouter(prefix="/api/v2")

router.include_router(config.router, tags=["v2-config"])
router.include_router(me.router, tags=["v2-me"])
router.include_router(access.router, tags=["v2-access"])
router.include_router(consent.router, tags=["v2-consent"])
router.include_router(family.router, tags=["v2-family"])
router.include_router(media.router, tags=["v2-media"])
router.include_router(jobs.router, tags=["v2-jobs"])
router.include_router(privacy.router, tags=["v2-privacy"])
router.include_router(profile.router, tags=["v2-profile"])
router.include_router(inventory.router, tags=["v2-inventory"])
router.include_router(supplements.router, tags=["v2-supplements"])
router.include_router(scan.router, tags=["v2-scan"])
router.include_router(product.router, tags=["v2-product-scan"])
router.include_router(skin_care_scan.router, tags=["v2-skin-care-scan"])
router.include_router(
    skin_care_personal_decision.router, tags=["v2-skin-care-for-you"]
)
router.include_router(community.router, tags=["v2-community"])
router.include_router(shopping.router, tags=["v2-shopping"])
router.include_router(integrations.router, tags=["v2-integrations"])
router.include_router(shelf.router, tags=["v2-shelf"])
router.include_router(maintenance.router, tags=["v2-maintenance"])
router.include_router(routines.router, tags=["v2-routines"])
# Notification settings and device registration. Their paths keep a
# ``/today/`` segment for the installed app; the retired Today product is
# still not mounted, and these routes do not depend on its feature flag.
router.include_router(notification_settings.router, tags=["v2-notifications"])
router.include_router(growth.router, tags=["v2-growth"])
# Step 16. A disclosed outbound handoff read after the decision, and its own
# telemetry. Mounted separately from growth: two contracts, one table.
router.include_router(commerce.router, tags=["v2-commerce"])
router.include_router(admin.router, tags=["v2-admin"])
# Step 17. Pilot issuance of B2B API access, admins only. The B2B API itself is
# a separate surface (``app.api.b2b``, ``/api/b2b/v1``), not part of /api/v2.
router.include_router(b2b_admin.router, tags=["v2-admin-b2b"])
# Not a customer surface: a shared-secret door for the external scheduler.
router.include_router(internal_scheduler.router)
router.include_router(personal_applicability_admin.router, tags=["v2-admin-personal-applicability"])
router.include_router(
    personal_decision_release_admin.router, tags=["v2-admin-personal-decision-releases"]
)

__all__ = ["router"]


