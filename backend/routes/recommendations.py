"""Recommendation aliases and history routes."""
from fastapi import APIRouter, Depends
from typing import Dict, Any

from database import db
from models import StylePlanRequest
from security import get_current_user
from routes.plans import create_style_plan

router = APIRouter()

@router.post("/recommendations/advice")
async def get_advice(
    request: StylePlanRequest, current_user: Dict[str, Any] = Depends(get_current_user)
):
    return await create_style_plan(request, current_user)


@router.post("/recommendations/generate")
async def generate_recommendations(
    request: StylePlanRequest, current_user: Dict[str, Any] = Depends(get_current_user)
):
    return await create_style_plan(request, current_user)


@router.get("/recommendations/history")
async def get_recommendation_history(user: Dict[str, Any] = Depends(get_current_user)):
    recs = await db.style_plans.find({"user_id": user["id"]}).sort("created_at", -1).to_list(20)
    return [
        {
            "id": r["id"],
            "occasion": r.get("occasion"),
            "mood": r.get("mood"),
            "plan": r.get("plan"),
            "created_at": r.get("created_at"),
        }
        for r in recs
    ]
