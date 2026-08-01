"""Style plan creation routes."""
from fastapi import APIRouter, Depends
from typing import Dict, Any
from datetime import datetime

from database import db
from models import StylePlanRequest, StylePlan
from security import get_current_user
from ai import generate_style_plan, _assert_ai_quota

router = APIRouter()

@router.post("/plans/style")
async def create_style_plan(
    request: StylePlanRequest, current_user: Dict[str, Any] = Depends(get_current_user)
):
    # Identity comes from the token. Any user_id in the body is ignored.
    user_id = current_user["id"]
    # Counted here rather than on each route, because /recommendations/advice
    # and /recommendations/generate both run through this function — so every
    # AI call is counted exactly once, whichever route was used.
    await _assert_ai_quota(current_user)
    user = dict(current_user)
    if request.diet:
        user["diet"] = request.diet
    if request.city:
        user["city"] = request.city
    if request.height_cm is not None:
        user["height_cm"] = request.height_cm
    if request.weight_kg is not None:
        user["weight_kg"] = request.weight_kg
    if request.body_type:
        user["body_type"] = request.body_type
    if request.style_vibe:
        user["style_vibe"] = request.style_vibe
    user["follow_trends"] = request.follow_trends

    # Persist body profile for the signed-in user
    persist = {
        k: user[k]
        for k in ("diet", "city", "height_cm", "weight_kg", "body_type", "style_vibe")
        if k in user and user[k] is not None
    }
    if persist:
        persist["updated_at"] = datetime.utcnow()
        await db.users.update_one({"id": user_id}, {"$set": persist})

    # Raises AnalysisUnavailableError when a trustworthy plan cannot be built.
    # Nothing below runs in that case, so no empty plan is ever saved.
    plan = await generate_style_plan(
        user,
        request.occasion,
        request.mood,
        request.budget_range or user.get("budget_range") or "mid",
        v1_user_id=user_id,
    )
    rec = StylePlan(
        user_id=user_id,
        occasion=request.occasion,
        mood=request.mood,
        plan=plan,
    )
    await db.style_plans.insert_one(rec.dict())
    return {"plan_id": rec.id, "plan": plan}
