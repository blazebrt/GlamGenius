"""Reading and setting feature flags.

Resolution order:

1. The ``feature_flags`` table, if the flag has a row — runtime control, no deploy.
2. The ``V2_FEATURES`` environment list — the boot default.
3. Off.

A database that is unreachable must not turn features on, so any error falls
back to the environment list rather than raising.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import V2_FEATURES
from app.shared.flags.models import FeatureFlag

logger = logging.getLogger(__name__)

# Every flag the codebase knows about. Listing them makes /api/v2/config able to
# report the full set rather than only the ones someone happened to switch on.
KNOWN_FLAGS: Dict[str, str] = {
    "v2_media": "Upload, fetch and delete media through the V2 API",
    "v2_privacy": "Data export and account deletion requests",
    "v2_consent": "Record and enforce photo-analysis consent",
    "v2_ai_gateway": "Route AI calls through the recorded gateway",
    "v2_profile": "Appearance digital twin and progressive onboarding",
    "v2_inventory": "Complete appearance inventory",
    "v2_inventory_batch": "Experimental multi-item inventory capture",
}


def env_enabled(key: str) -> bool:
    return key in V2_FEATURES


async def is_enabled(session: AsyncSession, key: str) -> bool:
    try:
        row = await session.get(FeatureFlag, key)
    except Exception as exc:  # noqa: BLE001 — a flag lookup must never 500 a request
        logger.warning(
            "feature_flag_lookup_failed key=%s type=%s", key, type(exc).__name__
        )
        return env_enabled(key)
    if row is None:
        return env_enabled(key)
    return bool(row.enabled)


async def all_flags(session: AsyncSession) -> Dict[str, bool]:
    """Every known flag and its resolved state."""
    resolved = {key: env_enabled(key) for key in KNOWN_FLAGS}
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
        row = FeatureFlag(
            key=key,
            enabled=enabled,
            description=description or KNOWN_FLAGS.get(key, ""),
        )
        session.add(row)
    else:
        row.enabled = enabled
        if description:
            row.description = description
    await session.flush()
    return row


def enabled_keys() -> List[str]:
    return sorted(V2_FEATURES)
