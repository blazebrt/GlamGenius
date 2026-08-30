"""Anonymous device identity.

The camera has to open on first launch with nothing set up, so the phone
identifies itself instead of a person. That keeps every lookup attributable and
rate-limitable without anybody signing up, and without opening a public
endpoint that anyone can scrape.

The token is random, stored hashed, and grants exactly one thing: reading
product data and recording scans. It cannot reach an account, a profile, an
inventory or anything else.
"""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import ScanDevice
from app.shared.database.base import utcnow

TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register(
    session: AsyncSession, *, device_key: str, platform: str | None = None,
) -> tuple[ScanDevice, str]:
    """Register a device, or re-issue a token for one that already exists.

    Re-registering rotates the token: a phone that lost its keychain gets a
    working device back rather than a dead one, and the old token stops working.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    device = (await session.execute(
        select(ScanDevice).where(ScanDevice.device_key == device_key)
    )).scalar_one_or_none()
    if device is None:
        device = ScanDevice(device_key=device_key, token_hash=_hash(token), platform=platform)
        session.add(device)
    else:
        device.token_hash = _hash(token)
        if platform:
            device.platform = platform
    device.last_seen_at = utcnow()
    await session.flush()
    return device, token


async def resolve(session: AsyncSession, token: str | None) -> ScanDevice | None:
    """Find the device a token belongs to, or None."""
    if not token:
        return None
    device = (await session.execute(
        select(ScanDevice).where(ScanDevice.token_hash == _hash(token))
    )).scalar_one_or_none()
    if device is not None:
        device.last_seen_at = utcnow()
    return device


async def claim(session: AsyncSession, *, device: ScanDevice, account_id) -> ScanDevice:
    """Attach a device to an account, so scans made before signing up follow along."""
    device.claimed_by_account_id = account_id
    await session.flush()
    return device
