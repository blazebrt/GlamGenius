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

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.domains.product.models import ScanDevice
from app.shared.database.base import utcnow
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError


TOKEN_BYTES = 32


def _device_conflict(message: str, reason: str) -> AppError:
    """A plain, non-retryable 409.

    Not :class:`ConflictError`: that is the optimistic-locking error and
    requires a ``current_version`` that means nothing for a device. Raising it
    without one was a ``TypeError``, so both conflicts below used to surface
    as a 500 rather than as the refusal they are. Not retryable: sending the
    same request again cannot succeed.
    """
    return AppError(message, status_code=409, code=ErrorCode.CONFLICT, retryable=False, extra={"reason": reason})


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def register(
    session: AsyncSession, *, device_key: str, platform: str | None = None, proof_token: str | None = None,
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
        if not proof_token or device.token_hash != _hash(proof_token):
            raise _device_conflict(
                "A known device key requires its current device token before it can rotate.",
                "device_token_required",
            )
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
    """Attach a device to an account, so scans made before signing up follow along.

    One statement decides it: an UPDATE that succeeds only while the row is
    still unclaimed or already this account's. Reading the claim and then
    writing it was two steps. Two accounts claiming the same unclaimed phone
    at once could both read NULL, both write, and both be told they had
    claimed it, with the second write silently replacing the first.

    Under PostgreSQL's READ COMMITTED a concurrent UPDATE of the same row waits
    for the first to commit and then re-evaluates its WHERE clause against the
    committed row. So exactly one of two racing claims for different accounts
    matches; the other matches no row and is refused. A replay by the account
    that already holds the claim matches and succeeds, as before.

    What a claim is, and what it is not:

    * The **installation** is ``device_key`` and its token: which phone.
    * The **claim** is a historical attachment. Scans made signed out *before*
      anyone claimed the phone follow the claimant in, once
      (``service.attach_scans_to_account``). It is never transferred: a second
      account is refused rather than given the phone, so nobody's earlier
      history changes hands.
    * **Current authority** for anything new is the bearer token on that
      request, and only that. A claim does not make later device-only writes
      the claimant's (``service.record_scan``).
    """
    result = await session.execute(
        update(ScanDevice)
        .where(
            ScanDevice.id == device.id,
            or_(ScanDevice.claimed_by_account_id.is_(None), ScanDevice.claimed_by_account_id == account_id),
        )
        .values(claimed_by_account_id=account_id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise _device_conflict("This device is already claimed by another account.", "device_claimed_by_another_account")
    # The statement wrote the row; the object loaded from the token should say so
    # too, without the session issuing a second UPDATE for it.
    set_committed_value(device, "claimed_by_account_id", account_id)
    return device
