"""Reading and setting feature flags.

Resolution order (§11 of the Supabase hardening spec):

1. The ``feature_flags`` table (per-flag) — a runtime override written by an
   admin. Highest precedence.
2. The ``V2_FEATURES`` environment list — an explicit deploy-time override.
3. The **stable private-beta default** built into ``STABLE_BETA_DEFAULTS``
   below. This is the safety net: if the environment variable is missing
   the application must still boot with the essential private-beta
   features enabled. A missing ``V2_FEATURES`` used to turn the entire
   product off, which was the pre-hardening bug this addresses.

The stable default set enables everything a private-beta user needs to
onboard, add inventory, run scans, plan looks, and export their data.
Unfinished features (virtual try-on and any brand-new integration) stay
off by default.

A database that is unreachable must not turn features on that the code
default says are off, so any error falls back to the environment list,
which itself falls back to the stable default set.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import V2_FEATURES
from app.shared.flags.models import FeatureFlag

logger = logging.getLogger(__name__)

# Every flag the codebase knows about. Listing them makes /api/v2/config able to
# report the full set rather than only the ones someone happened to switch on.
KNOWN_FLAGS: dict[str, str] = {
    "v2_media": "Upload, fetch and delete media through the V2 API",
    "v2_privacy": "Data export and account deletion requests",
    "v2_consent": "Record and enforce photo-analysis consent",
    "v2_ai_gateway": "Route AI calls through the recorded gateway",
    "v2_profile": "Appearance digital twin and progressive onboarding",
    "v2_inventory": "Complete appearance inventory",
    "v2_inventory_batch": "Multi-item capture: one shelf photo, one tap per item",
    "v2_recommendations": "Internal look engine used by Event Ready",
    "v2_shopping_decisions": "Shopping decisions: should I buy this?",
    "v2_today": "The Today engine: one plan for the day",
    "v2_planner": "The Monday-to-Sunday weekly planner",
    "v2_routines": (
        "Safe appearance routines, shelf, perfume, supplement and food context"
    ),
    "v2_progress": (
        "Explainable progress metrics, goals, milestones and controlled memory"
    ),
    "v2_scan": "Face/hair/hands photo analysis with consent enforcement",
    "v2_quiz": "Style vibe quiz. Rejected product surface; keep this off.",
    "v2_beta_access": "Invite reservation, redemption and beta usage limiter",
    "v2_onboarding": "Progressive onboarding flow",
    "v2_virtual_tryon": "Virtual try-on. No provider is selected; keep this off.",
    "v2_packing": "Packing decision engine. Not yet implemented; keep this off.",
}


# ---------------------------------------------------------------------------
# Stable private-beta defaults (§11)
# ---------------------------------------------------------------------------
# When neither the database nor the environment says anything about a flag,
# these defaults win. Everything the app needs to actually deliver value in
# the private beta is ON; anything unfinished is OFF.
STABLE_BETA_DEFAULTS: dict[str, bool] = {
    "v2_media": True,
    "v2_privacy": True,
    "v2_consent": True,
    "v2_ai_gateway": True,
    "v2_profile": True,
    "v2_inventory": True,
    "v2_inventory_batch": True,
    "v2_recommendations": True,
    "v2_shopping_decisions": True,
    "v2_today": True,
    "v2_planner": True,
    "v2_routines": True,
    "v2_progress": True,
    "v2_scan": True,
    "v2_quiz": False,
    "v2_beta_access": True,
    "v2_onboarding": True,
    "v2_virtual_tryon": False,
    "v2_packing": False,
}


def _env_list() -> list[str]:
    """Return the current ``V2_FEATURES`` list.

    Read fresh (rather than closing over ``V2_FEATURES``) so tests that
    monkeypatch the environment for the process pick up the change.
    """
    raw = os.environ.get("V2_FEATURES")
    if raw is None:
        return list(V2_FEATURES)
    return [part.strip() for part in raw.split(",") if part.strip()]


def env_override_set() -> bool:
    """True when ``V2_FEATURES`` was explicitly provided by the deploy env.

    Distinct from "env list is empty" — an explicit empty string is a
    deliberate override that turns every flag off. Nothing set means fall
    back to the stable default.
    """
    return "V2_FEATURES" in os.environ


def resolved_default(key: str) -> bool:
    """Effective boot default for ``key`` after applying env + stable default.

    Not read from the database; ``is_enabled`` layers the DB row on top.
    """
    env_list = _env_list()
    if env_override_set():
        return key in env_list
    return STABLE_BETA_DEFAULTS.get(key, False)


def env_enabled(key: str) -> bool:
    """Kept for callers that specifically want the env-only view."""
    return key in _env_list()


# Essential beta flags. If the resolved-config set has any of these off we
# emit a startup warning so an operator knows the deploy is broken.
#
# "Essential" means the beta is broken without it, not merely that the code
# exists. Two flags are deliberately absent:
#   v2_quiz            — a rejected product surface, defaulted off above.
#   v2_recommendations — the look engine is retained, but only as internal
#                        infrastructure Event Ready calls. It stays on by
#                        default so Event Ready works; it is not standalone
#                        product infrastructure, so its absence is not a
#                        broken deploy.
# v2_scan stays: the photo baseline is part of the retained skin and hair
# manager, not a style surface.
ESSENTIAL_BETA_FLAGS: list[str] = [
    "v2_profile",
    "v2_onboarding",
    "v2_inventory",
    "v2_media",
    "v2_consent",
    "v2_scan",
    "v2_shopping_decisions",
    "v2_today",
    "v2_planner",
    "v2_routines",
    "v2_progress",
    "v2_privacy",
    "v2_beta_access",
]


def warn_if_essentials_disabled() -> list[str]:
    """Return the list of essentials that are currently disabled by config.

    Also emits a WARNING log line so ops observability catches the case
    where a redeploy dropped critical features.
    """
    missing = [k for k in ESSENTIAL_BETA_FLAGS if not resolved_default(k)]
    if missing:
        logger.warning(
            "essential_beta_flags_disabled flags=%s hint=%s",
            ",".join(missing),
            "Set V2_FEATURES to include these keys or remove the override.",
        )
    return missing


async def is_enabled(session: AsyncSession, key: str) -> bool:
    try:
        row = await session.get(FeatureFlag, key)
    except Exception as exc:  # noqa: BLE001 — a flag lookup must never 500 a request
        logger.warning(
            "feature_flag_lookup_failed key=%s type=%s", key, type(exc).__name__
        )
        return resolved_default(key)
    if row is None:
        return resolved_default(key)
    return bool(row.enabled)


async def all_flags(session: AsyncSession) -> dict[str, bool]:
    """Every known flag and its resolved state."""
    resolved = {key: resolved_default(key) for key in KNOWN_FLAGS}
    try:
        rows = (await session.execute(select(FeatureFlag))).scalars().all()
        for row in rows:
            resolved[row.key] = bool(row.enabled)
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature_flags_read_failed type=%s", type(exc).__name__)
    return resolved


async def set_flag(
    session: AsyncSession, key: str, enabled: bool, description: str = ""
) -> FeatureFlag:
    row = await session.get(FeatureFlag, key)
    if row is None:
        row = FeatureFlag(key=key, enabled=enabled, description=description)
        session.add(row)
    else:
        row.enabled = enabled
        if description:
            row.description = description
    await session.flush()
    return row
