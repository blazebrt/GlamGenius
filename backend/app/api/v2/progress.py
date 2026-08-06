"""Progress, goals, memory and milestone routes.

Every route reads the caller's own data. There is no path parameter or body
field that names another account — ownership comes from the token, so there is
nothing to tamper with.

``/memory/export`` is the one worth calling out: it returns everything held
about a user including what they deleted and why, because an export that
quietly omits deletions is not an honest export.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.progress import memory as memory_domain
from app.domains.progress import milestones as milestone_rules
from app.domains.progress import registry, service
from app.domains.progress.schemas import (
    FeedbackInput,
    GoalCreate,
    GoalPatch,
    MemoryCategoryPatch,
    MemoryPatch,
    ProgressPhotoInput,
    SelfReport,
)
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_progress"))])


# --- Progress ---------------------------------------------------------------------


@router.get("/progress")
async def get_progress(
    period: str = Query("week", pattern="^(week|month)$"),
    as_of: Optional[date] = Query(None),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Every metric for the period, each with how it was worked out."""
    result = await service.overview(session, current.account_id, period=period, today=as_of)
    await session.commit()
    return result


@router.get("/progress/metrics")
async def list_metric_definitions(
    current: CurrentAccount = Depends(get_current_account),
):
    """Every metric this product can show, and its documented formula."""
    return {
        "metrics": registry.all_definitions(),
        "registry_version": registry.REGISTRY_VERSION,
        "no_overall_score": True,
        "note": (
            "Each metric measures one thing and reads only its own inputs. None of them is "
            "combined into a single headline number, and by design none of them can be."
        ),
    }


@router.get("/progress/metrics/{key}")
async def get_metric(
    key: str,
    as_of: Optional[date] = Query(None),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """One metric: its formula, its value now, and its history."""
    return await service.metric_detail(session, current.account_id, key, today=as_of)


@router.post("/progress/self-report")
async def record_self_report(
    body: SelfReport,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """How you felt, in your own words. Nothing else feeds this metric."""
    result = await service.record_self_report(session, current.account_id, body)
    await session.commit()
    return result


# --- Photos and comparisons -----------------------------------------------------------


@router.post("/progress/photos")
async def add_progress_photo(
    body: ProgressPhotoInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Keep a photo for comparison, with the conditions it was taken in."""
    result = await service.add_photo(session, current.account_id, body)
    await session.commit()
    return result


@router.get("/progress/comparisons")
async def get_comparisons(
    body_area: str = Query("face", max_length=32),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """A side-by-side, but only if the conditions genuinely allow one."""
    from app.domains.progress.comparison import BODY_AREAS

    if body_area not in BODY_AREAS:
        raise ValidationFailedError(
            f"'{body_area}' is not an area we compare.", field="body_area",
        )
    result = await service.comparisons(session, current.account_id, body_area=body_area)
    await session.commit()
    return result


# --- Goals -------------------------------------------------------------------------------


@router.get("/goals")
async def list_goals(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    return {"goals": await service.list_goals(session, current.account_id)}


@router.post("/goals")
async def create_goal(
    body: GoalCreate,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Set a goal, with where you are starting from recorded."""
    result = await service.create_goal(session, current.account_id, body)
    await session.commit()
    return result


@router.patch("/goals/{goal_id}")
async def patch_goal(
    goal_id: uuid.UUID,
    body: GoalPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await service.patch_goal(session, current.account_id, goal_id, body)
    await session.commit()
    return result


# --- Memory -------------------------------------------------------------------------------


@router.get("/memory")
async def list_memory(
    category: Optional[str] = Query(None, max_length=32),
    include_deleted: bool = Query(False),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What GlamGenius remembers, why, and where each fact came from."""
    if category and category not in memory_domain.CATEGORIES:
        raise ValidationFailedError(f"'{category}' is not a memory category.", field="category")
    return await service.list_memory(
        session, current.account_id, category=category, include_deleted=include_deleted,
    )


@router.patch("/memory/{fact_id}")
async def patch_memory(
    fact_id: uuid.UUID,
    body: MemoryPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Correct or confirm something we remember. Your version wins."""
    result = await service.patch_memory(session, current.account_id, fact_id, body)
    await session.commit()
    return result


@router.delete("/memory/{fact_id}")
async def delete_memory(
    fact_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Forget something. It stops affecting suggestions straight away."""
    result = await service.delete_memory(session, current.account_id, fact_id)
    await session.commit()
    return result


@router.get("/memory/export")
async def export_memory(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Everything we hold, including what you deleted and when."""
    return await service.export_memory(session, current.account_id)


@router.patch("/memory/categories/{category}")
async def set_memory_category(
    category: str,
    body: MemoryCategoryPatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Switch a whole category of memory on or off. Nothing is destroyed."""
    if category != body.category:
        raise ValidationFailedError(
            "The category in the address and in the body must match.", field="category",
        )
    result = await service.set_memory_category(session, current.account_id, category, body.enabled)
    await session.commit()
    return result


@router.post("/memory/feedback")
async def record_feedback(
    body: FeedbackInput,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """Tell us what you thought. We say plainly what we learned from it."""
    result = await service.record_feedback(session, current.account_id, body)
    await session.commit()
    return result


# --- Milestones ------------------------------------------------------------------------------


@router.get("/milestones")
async def list_milestones(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    """What you have reached, and what is on the list."""
    return {
        "earned": await service.recent_milestones(session, current.account_id, limit=50),
        "streaks": await service.streaks_for(session, current.account_id),
        "all_rules": milestone_rules.all_rules(),
        "rewarded_behaviours": list(milestone_rules.REWARDABLE_BEHAVIOURS),
        "never_rewarded": list(milestone_rules.NEVER_REWARDABLE),
        "note": (
            "These mark things worth doing — using what you own, keeping to a routine, planning "
            "ahead. Opening the app is not one of them and never will be."
        ),
    }


@router.post("/milestones/{milestone_id}/acknowledge")
async def acknowledge_milestone(
    milestone_id: uuid.UUID,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    result = await service.acknowledge_milestone(session, current.account_id, milestone_id)
    await session.commit()
    return result
