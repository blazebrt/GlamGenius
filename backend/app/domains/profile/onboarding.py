"""Resumable, minimal progressive onboarding state machine."""
from __future__ import annotations

import uuid
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile import service
from app.domains.profile.models import AppearanceProfile, OnboardingSession
from app.shared.database.base import utcnow
from app.shared.errors.exceptions import ValidationFailedError

STEPS = ["goal", "style_preferences", "fit_preferences", "lifestyle", "appearance_photo", "colour_palette", "confirm_attributes", "preview"]
STEP_KEYS = {
    "goal": {"current_goal", "appearance_goals"},
    "style_preferences": {"preferred_style", "favourite_colours", "disliked_colours", "formality_preference", "style_experimentation", "indian_categories", "western_categories", "fusion_categories"},
    "fit_preferences": {"height_cm", "usual_top_size", "usual_bottom_size", "preferred_coverage", "sleeve_preferences", "neckline_preferences", "footwear_comfort", "silhouettes_liked", "silhouettes_avoided"},
    "lifestyle": {"city", "climate", "work_context", "routine", "activity_level", "travel_frequency", "budget"},
    "appearance_photo": set(), "colour_palette": set(), "confirm_attributes": set(), "preview": set(),
}


async def current(session: AsyncSession, profile: AppearanceProfile) -> OnboardingSession:
    row = (await session.execute(select(OnboardingSession).where(OnboardingSession.profile_id == profile.id).order_by(OnboardingSession.created_at.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        row = OnboardingSession(profile_id=profile.id)
        session.add(row)
        await session.flush()
    return row


def _next_step(done: list[str], skipped: list[str]) -> str:
    handled = set(done) | set(skipped)
    return next((step for step in STEPS if step not in handled), "preview")


async def record_step(session: AsyncSession, profile: AppearanceProfile, row: OnboardingSession, *, step: str, data: Dict[str, Any], skipped: bool) -> None:
    if row.status == "completed":
        raise ValidationFailedError("Onboarding is already complete.")
    if step == "goal" and skipped:
        raise ValidationFailedError("Choose what you are preparing for to continue.", field="current_goal")
    allowed = STEP_KEYS[step]
    unknown = set(data) - allowed
    if unknown:
        raise ValidationFailedError(f"Unexpected fields for {step}: {', '.join(sorted(unknown))}")
    if step == "goal" and not data.get("current_goal"):
        raise ValidationFailedError("Choose what you are preparing for.", field="current_goal")
    completed = list(row.completed_steps or [])
    skipped_steps = list(row.skipped_steps or [])
    answers = dict(row.answers or {})
    if skipped:
        if step not in skipped_steps:
            skipped_steps.append(step)
        answers.pop(step, None)
    else:
        updates = [{"key": key, "value": value} for key, value in data.items()]
        if updates:
            await service.apply_attributes(session, profile, updates, reason=f"onboarding_{step}")
        if step not in completed:
            completed.append(step)
        if step in skipped_steps:
            skipped_steps.remove(step)
        answers[step] = data
    row.completed_steps = completed
    row.skipped_steps = skipped_steps
    row.answers = answers
    row.current_step = _next_step(completed, skipped_steps)


def preview(row: OnboardingSession) -> Dict[str, Any]:
    answers = row.answers or {}
    goal = (answers.get("goal") or {}).get("current_goal", "Everyday confidence")
    style = (answers.get("style_preferences") or {}).get("preferred_style", "versatile")
    colours = (answers.get("style_preferences") or {}).get("favourite_colours", [])
    colour_line = f" Start with {', '.join(colours[:2])}." if colours else " Add favourite colours whenever you are ready."
    return {
        "headline": f"Your first {style.lower()} direction for {goal.lower()}",
        "recommendation": f"Build one comfortable repeatable look first, then vary one detail at a time.{colour_line}",
        "next_action": "Open My Appearance to review and improve what GlamGenius knows.",
    }


async def complete(session: AsyncSession, profile: AppearanceProfile, row: OnboardingSession) -> Dict[str, Any]:
    if "goal" not in (row.completed_steps or []):
        raise ValidationFailedError("Complete the goal step before finishing onboarding.")
    row.status = "completed"
    row.current_step = "complete"
    row.completed_at = utcnow()
    row.recommendation_preview = preview(row)
    return row.recommendation_preview


def serialize(row: OnboardingSession) -> Dict[str, Any]:
    return {
        "id": str(row.id), "status": row.status, "current_step": row.current_step,
        "steps": STEPS, "completed_steps": row.completed_steps or [],
        "skipped_steps": row.skipped_steps or [], "answers": row.answers or {},
        "recommendation_preview": row.recommendation_preview,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "minimum_complete": "goal" in (row.completed_steps or []), "weight_required": False,
    }
