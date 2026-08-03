"""GET /api/v2/me — the caller, as V2 sees them."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.consent import service as consent_service
from app.domains.media import service as media_service
from app.shared.database.sql import get_session
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()


def _month_key() -> str:
    d = datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


@router.get("/me")
async def get_me(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """The signed-in caller's V2 state.

    Neutral, non-billing shape. No plan, no scans-remaining-free, no
    paid-membership fields. The beta usage summary lives at
    ``/api/v2/access/usage``.
    """
    return {
        "account": {
            "id": str(current.account_id),
            "email": current.supabase_user.email,
            "status": current.account.status,
            "is_admin": current.is_admin,
            "deletion_requested_at": (
                current.account.deletion_requested_at.isoformat()
                if current.account.deletion_requested_at
                else None
            ),
        },
        "consent": await consent_service.summary(session, current.account_id),
        "media": {
            "active_count": len(
                await media_service.list_for_account(session, current.account_id)
            )
        },
        "period": _month_key(),
    }
