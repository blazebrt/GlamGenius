"""Style quiz routes."""
from fastapi import APIRouter, Depends
from typing import Dict, Any
from datetime import datetime

from database import db
from models import QuizSubmission, StylePlan
from catalog import QUIZ_QUESTIONS
from security import get_current_user
from ai import generate_style_plan, _assert_ai_quota

router = APIRouter()

@router.get("/quiz/questions")
async def get_quiz_questions():
    return QUIZ_QUESTIONS


@router.post("/quiz/submit")
async def submit_quiz(
    submission: QuizSubmission, user: Dict[str, Any] = Depends(get_current_user)
):
    # Identity comes from the token. Any user_id in the body is ignored.
    user_id = user["id"]
    # This route builds a plan with Gemini too, so it shares the same limit.
    await _assert_ai_quota(user)
    answer_map = {a.question_id: a.answer for a in submission.answers}
    update_data: Dict[str, Any] = {"updated_at": datetime.utcnow()}

    hair_map = {
        "Frizz & dryness": "frizz",
        "Thin-looking / low volume": "thinning",
        "Breakage & damage": "damage",
        "Oily roots": "oily_roots",
    }
    if answer_map.get("q1") in hair_map:
        update_data["hair_concerns"] = [hair_map[answer_map["q1"]]]

    skin_map = {
        "Oily with shine": "oily",
        "Dry or tight": "dry",
        "Combination": "combination",
        "Balanced": "normal",
        "Easily uncomfortable": "sensitive",
    }
    if answer_map.get("q2") in skin_map:
        update_data["skin_type"] = skin_map[answer_map["q2"]]

    concern_map = {
        "Fewer pimples": "pimples",
        "More even tone": "uneven_tone",
        "Less dullness": "dullness",
        "Better hydration": "dryness",
        "Smoother texture": "texture",
    }
    if answer_map.get("q3") in concern_map:
        update_data["skin_concerns"] = [concern_map[answer_map["q3"]]]

    diet_map = {
        "Vegetarian": "veg",
        "Eggetarian": "egg",
        "Non-vegetarian": "non-veg",
    }
    if answer_map.get("q4") in diet_map:
        update_data["diet"] = diet_map[answer_map["q4"]]

    vibe_map = {
        "Natural & easy": "natural",
        "Polished & neat": "polished",
        "Festive & bold": "festive",
        "Classic Indian wear": "classic",
        "Trend-led / experimental": "trendy",
    }
    if answer_map.get("q5") in vibe_map:
        update_data["style_vibe"] = vibe_map[answer_map["q5"]]
        update_data["preferences"] = {"style_preference": answer_map["q5"]}

    body_map = {
        "Slim / petite": "slim",
        "Average": "average",
        "Athletic": "athletic",
        "Curvy": "curvy",
        "Plus / fuller": "plus",
        "Prefer not to say": "prefer_not",
    }
    if answer_map.get("q6") in body_map:
        update_data["body_type"] = body_map[answer_map["q6"]]

    await db.users.update_one({"id": user_id}, {"$set": update_data})

    user = await db.users.find_one({"id": user_id}) or {}
    plan = await generate_style_plan(
        user,
        submission.occasion or "everyday",
        None,
        submission.budget or user.get("budget_range") or "mid",
        v1_user_id=user_id,
    )
    rec = StylePlan(user_id=user_id, occasion=submission.occasion, plan=plan)
    await db.style_plans.insert_one(rec.dict())
    return {"plan_id": rec.id, "plan": plan, "profile_updated": True}
