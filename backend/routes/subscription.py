"""App Plus subscription routes (mock billing)."""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from datetime import datetime, timedelta
import uuid

from database import db
from models import SubscriptionOrderRequest
from security import get_current_user, _require_same_user, _month_key
from settings import FREE_SCANS_PER_MONTH, PLUS_PRICE_INR, PLUS_YEARLY_INR

router = APIRouter()

@router.post("/subscription/create-order")
async def create_subscription_order(
    request: SubscriptionOrderRequest, user: Dict[str, Any] = Depends(get_current_user)
):
    # Identity comes from the token. Any user_id in the body is ignored.
    user_id = user["id"]

    if request.plan == "plus_yearly":
        amount = PLUS_YEARLY_INR
        days = 365
        label = "GlamGenius Plus (Yearly)"
    else:
        amount = PLUS_PRICE_INR
        days = 30
        label = "GlamGenius Plus (Monthly)"

    order = {
        "id": f"sub_{uuid.uuid4().hex[:16]}",
        "user_id": user_id,
        "plan": request.plan,
        "amount_inr": amount,
        "currency": "INR",
        "label": label,
        "days": days,
        "status": "created",
        "created_at": datetime.utcnow(),
    }
    await db.subscription_orders.insert_one(order)
    return {
        "order_id": order["id"],
        "amount_inr": amount,
        "currency": "INR",
        "label": label,
        "plan": request.plan,
        "mock": True,
        "message": "Demo billing — replace with Razorpay keys for production.",
    }


@router.post("/subscription/confirm")
async def confirm_subscription(
    order_id: str,
    payment_method: str = "upi",
    user: Dict[str, Any] = Depends(get_current_user),
):
    # Private test is invite-only — no request may grant a paid plan this way.
    raise HTTPException(
        status_code=403,
        detail={
            "code": "SUBSCRIPTIONS_UNAVAILABLE",
            "message": (
                "Subscriptions are not available yet. "
                "GlamGenius is invite-only for a private test group — "
                "use your invite code to sign up for full access."
            ),
        },
    )
    # Kept below for a later paid launch; unreachable while invite-only.
    order = await db.subscription_orders.find_one({"id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # An order can only be confirmed by the user it belongs to.
    _require_same_user(user, order.get("user_id") or "")

    expires = datetime.utcnow() + timedelta(days=int(order.get("days", 30)))
    await db.subscription_orders.update_one(
        {"id": order_id},
        {
            "$set": {
                "status": "paid",
                "payment_method": payment_method,
                "paid_at": datetime.utcnow(),
            }
        },
    )
    await db.users.update_one(
        {"id": order["user_id"]},
        {
            "$set": {
                "plan": "plus",
                "plan_expires_at": expires,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    return {
        "success": True,
        "plan": "plus",
        "expires_at": expires.isoformat(),
        "message": "Plus activated. Unlimited skin & hair checks unlocked.",
    }


@router.get("/subscription/status")
async def subscription_status(user: Dict[str, Any] = Depends(get_current_user)):
    plan = user.get("plan", "free")
    expires = user.get("plan_expires_at")
    active = plan == "plus" and (not expires or expires > datetime.utcnow())
    used = int(user.get("scans_used_this_month") or 0)
    if user.get("scan_month_key") != _month_key():
        used = 0
    from settings import INVITE_SCANS_PER_MONTH
    limit = INVITE_SCANS_PER_MONTH if active else FREE_SCANS_PER_MONTH
    return {
        "plan": "plus" if active else "free",
        "expires_at": expires,
        "free_scans_per_month": limit,
        "invite_scans_per_month": INVITE_SCANS_PER_MONTH,
        "scans_used_this_month": used,
        "scans_remaining": max(0, limit - used),
        "plus_price_inr": PLUS_PRICE_INR,
        "plus_yearly_inr": PLUS_YEARLY_INR,
        "subscriptions_available": False,
        "invite_only": True,
    }
