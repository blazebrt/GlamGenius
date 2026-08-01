"""DELETE /api/v2/account — account deletion request.

**What this does and does not do, and why.**

It immediately and irreversibly erases every stored image the account owns,
revokes photo-analysis consent, marks the account ``deletion_requested``, and
writes an audit record.

It does **not** destroy the V1 user document. A single unconfirmed API call
should not be able to permanently erase an account's history — that needs a
confirmation flow in the app and an operator-side purge with a grace period,
neither of which exists yet. Erasing the images is the part that is genuinely
urgent and genuinely irreversible, so that part happens now.

The response says exactly which of those two things happened, so nobody has to
guess.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.audit import service as audit
from app.domains.audit.models import ACTION_ACCOUNT_DELETION_REQUESTED
from app.domains.consent import service as consent_service
from app.domains.consent.models import CONSENT_PHOTO_ANALYSIS
from app.domains.identity.models import ACCOUNT_STATUS_DELETION_REQUESTED
from app.domains.media import service as media_service
from app.shared.database.base import utcnow
from app.shared.database.sql import get_session
from app.shared.events import outbox
from app.shared.security.deps import (
    CurrentAccount,
    client_ip,
    get_current_account,
    require_flag,
)

router = APIRouter(dependencies=[Depends(require_flag("v2_privacy"))])


@router.delete("/account")
async def request_account_deletion(
    request: Request,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    account = current.account

    media_removed = await media_service.delete_all_for_account(
        session, account.id, client_ip=client_ip(request)
    )

    # Withdraw consent as part of the same request. Continuing to hold an
    # agreement to analyse photos for someone who is leaving would be wrong.
    await consent_service.record(
        session,
        account_id=account.id,
        consent_type=CONSENT_PHOTO_ANALYSIS,
        granted=False,
        source="account_deletion",
        client_ip=client_ip(request),
    )

    account.status = ACCOUNT_STATUS_DELETION_REQUESTED
    account.deletion_requested_at = utcnow()
    await session.flush()

    await audit.record(
        session,
        action=ACTION_ACCOUNT_DELETION_REQUESTED,
        account_id=account.id,
        subject_type="account",
        subject_id=str(account.id),
        context={"media_deleted": media_removed},
        client_ip=client_ip(request),
    )
    await outbox.publish(
        session,
        aggregate_type="account",
        aggregate_id=str(account.id),
        event_type="account.deletion_requested",
        payload={"v1_user_id": current.v1_user_id, "media_deleted": media_removed},
    )
    await session.commit()

    return {
        "status": account.status,
        "requested_at": account.deletion_requested_at.isoformat(),
        "completed_now": {
            "media_deleted": media_removed,
            "photo_analysis_consent_withdrawn": True,
        },
        "pending": {
            "profile_and_history": (
                "Your profile and past results are scheduled for removal and "
                "will be erased within 30 days."
            )
        },
        "message": (
            f"We have deleted {media_removed} stored photo(s) straight away and "
            "marked your account for closure. Your profile and past results will "
            "be removed within 30 days. Contact support if you change your mind "
            "before then."
        ),
    }
