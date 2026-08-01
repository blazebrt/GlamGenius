"""Recording and checking consent."""
from __future__ import annotations

import uuid
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CONSENT_VERSION, REQUIRE_ANALYSIS_CONSENT
from app.domains.audit import service as audit
from app.domains.audit.models import ACTION_CONSENT_GRANTED, ACTION_CONSENT_REVOKED
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS, Consent
from app.domains.identity import service as identity
from app.shared.database.base import utcnow
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ConsentRequiredError


async def latest(
    session: AsyncSession, account_id: uuid.UUID, consent_type: str
) -> Optional[Consent]:
    stmt = (
        select(Consent)
        .where(Consent.account_id == account_id)
        .where(Consent.consent_type == consent_type)
        .order_by(Consent.recorded_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def has_consent(
    session: AsyncSession, account_id: uuid.UUID, consent_type: str
) -> bool:
    """True only for a current-version grant.

    An agreement to superseded wording is not an agreement to the current
    wording, so a version bump correctly invalidates old consent.
    """
    row = await latest(session, account_id, consent_type)
    return bool(row and row.granted and row.version == CONSENT_VERSION)


async def record(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    consent_type: str,
    granted: bool,
    source: str = "app",
    client_ip: Optional[str] = None,
) -> Consent:
    row = Consent(
        account_id=account_id,
        consent_type=consent_type,
        granted=granted,
        version=CONSENT_VERSION,
        source=source,
        recorded_at=utcnow(),
    )
    session.add(row)
    await session.flush()

    await audit.record(
        session,
        action=ACTION_CONSENT_GRANTED if granted else ACTION_CONSENT_REVOKED,
        account_id=account_id,
        subject_type="consent",
        subject_id=str(row.id),
        context={"consent_type": consent_type, "version": CONSENT_VERSION},
        client_ip=client_ip,
    )
    return row


async def require_analysis_consent(
    session: AsyncSession, account_id: uuid.UUID
) -> None:
    """Raise unless the user has agreed to photo analysis.

    The flag exists as an emergency rollback switch, but ships on. The frontend
    collects the answer before sending the image and this server-side check
    prevents callers from bypassing that trust boundary.
    """
    if not REQUIRE_ANALYSIS_CONSENT:
        return
    if await has_consent(session, account_id, CONSENT_PHOTO_ANALYSIS):
        return
    raise ConsentRequiredError(
        consent_type=CONSENT_PHOTO_ANALYSIS, consent_version=CONSENT_VERSION
    )


async def require_analysis_consent_for_v1_user(v1_user_id: str) -> None:
    """Enforce V2 consent on the existing authenticated V1 scan route.

    The old route authenticates through MongoDB and does not have a SQLAlchemy
    request session. Resolve its stable user id through ``account_links`` only
    when enforcement is enabled, keeping the V1 identity source authoritative.
    """
    if not REQUIRE_ANALYSIS_CONSENT:
        return

    factory = get_sessionmaker()
    async with factory() as session:
        account = await identity.get_or_create_account(session, str(v1_user_id))
        await session.commit()
        await require_analysis_consent(session, account.id)


async def summary(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, object]:
    """What the app needs to decide whether to show a consent prompt."""
    row = await latest(session, account_id, CONSENT_PHOTO_ANALYSIS)
    return {
        "consent_version": CONSENT_VERSION,
        "required": REQUIRE_ANALYSIS_CONSENT,
        "photo_analysis": {
            "granted": bool(row and row.granted and row.version == CONSENT_VERSION),
            "recorded_at": row.recorded_at.isoformat() if row else None,
            "version": row.version if row else None,
            # True when they answered an older wording and need to be asked again.
            "needs_refresh": bool(row and row.version != CONSENT_VERSION),
        },
    }
