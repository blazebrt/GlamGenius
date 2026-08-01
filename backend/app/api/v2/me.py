"""GET /api/v2/me — the caller, as V2 sees them."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent import service as consent_service
from app.domains.entitlements import service as entitlements
from app.domains.media import service as media_service
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account
from security import _month_key, _user_view

router = APIRouter()


@router.get("/me")
async def get_me(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Profile, consent state and V2 account status in one call.

    The profile block is the V1 view unchanged, so the app has one shape for a
    user rather than two that can drift apart.
    """
    month = _month_key()
    return {
        "profile": _user_view(current.user),
        "account": {
            "id": str(current.account_id),
            "status": current.account.status,
            "deletion_requested_at": (
                current.account.deletion_requested_at.isoformat()
                if current.account.deletion_requested_at
                else None
            ),
        },
        "consent": await consent_service.summary(session, current.account_id),
        "usage": {
            "period": month,
            "recorded": await entitlements.totals_for_period(
                session, current.account_id, month
            ),
        },
        "media": {
            "active_count": len(
                await media_service.list_for_account(session, current.account_id)
            )
        },
    }
