"""Regression coverage for the appearance-profile domain.

Covers:
    * ``get_or_create_profile`` is idempotent.
    * Attribute observations and appearance goals persist and can be listed.
    * Attribute application overwrites the same key rather than duplicating.
    * Onboarding sessions can progress from ``in_progress`` to ``complete``.
    * ``sync_projections`` writes into the fit / style / lifestyle projection rows.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.domains.identity import service as identity
from app.domains.profile import service as profile_service
from app.domains.profile.models import (
    AppearanceGoal,
    AttributeObservation,
    FitPreference,
    OnboardingSession,
    ProfileAttribute,
)
from app.shared.database.sql import get_sessionmaker


pytestmark = pytest.mark.asyncio


async def _account():
    factory = get_sessionmaker()
    account_id = uuid.uuid4()
    async with factory() as session:
        await identity.register_account(session, account_id)
        await session.commit()
    return account_id


async def test_get_or_create_profile_is_idempotent(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        first = await profile_service.get_or_create_profile(session, account_id)
        await session.commit()
        first_id = first.id
    async with factory() as session:
        second = await profile_service.get_or_create_profile(session, account_id)
    assert first_id == second.id


async def test_attribute_apply_overwrites_same_key(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        await profile_service.apply_attributes(
            session,
            profile,
            [{"key": "skin_tone", "value": "medium"}],
            source="onboarding",
        )
        await session.commit()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        await profile_service.apply_attributes(
            session,
            profile,
            [{"key": "skin_tone", "value": "deep"}],
            source="user_declared",
        )
        await session.commit()

    async with factory() as session:
        rows = (await session.execute(
            select(ProfileAttribute).where(
                ProfileAttribute.profile_id == profile.id,
                ProfileAttribute.key == "skin_tone",
            )
        )).scalars().all()
    # Same key kept as a single row with the latest value.
    assert len(rows) == 1 and rows[0].value == "deep"


async def test_onboarding_session_lifecycle(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        session.add(OnboardingSession(
            profile_id=profile.id, status="in_progress",
            answers={"step_1": "done"},
        ))
        await session.commit()

    async with factory() as session:
        ob = (await session.execute(
            select(OnboardingSession).where(
                OnboardingSession.profile_id == profile.id
            )
        )).scalar_one()
        ob.status = "complete"
        ob.answers = {**(ob.answers or {}), "step_2": "done"}
        await session.commit()

    async with factory() as session:
        ob = (await session.execute(
            select(OnboardingSession).where(
                OnboardingSession.profile_id == profile.id
            )
        )).scalar_one()
    assert ob.status == "complete"
    assert ob.answers == {"step_1": "done", "step_2": "done"}


async def test_attribute_observation_is_written(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        session.add(AttributeObservation(
            profile_id=profile.id,
            key="skin_tone",
            proposed_value="warm_medium",
            source="scan",
            confidence=0.72,
            why="Derived from the deterministic face scan.",
        ))
        await session.commit()

    async with factory() as session:
        obs = (await session.execute(
            select(AttributeObservation).where(
                AttributeObservation.profile_id == profile.id
            )
        )).scalars().all()
    assert len(obs) == 1 and obs[0].proposed_value == "warm_medium"


async def test_appearance_goals_scoped_to_profile(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        session.add(AppearanceGoal(
            profile_id=profile.id,
            goal="Feel less shiny by evening",
            priority=1,
        ))
        session.add(AppearanceGoal(
            profile_id=profile.id,
            goal="Rediscover my colour palette",
            priority=2,
        ))
        await session.commit()

    async with factory() as session:
        count = (await session.execute(
            select(func.count(AppearanceGoal.id)).where(
                AppearanceGoal.profile_id == profile.id
            )
        )).scalar_one()
    assert count == 2


async def test_projection_row_created_via_sync(db_clean):
    factory = get_sessionmaker()
    account_id = await _account()

    async with factory() as session:
        profile = await profile_service.get_or_create_profile(session, account_id)
        await profile_service.sync_projections(
            session, profile.id,
            {"usual_top_size": "M"},
        )
        await session.commit()

    async with factory() as session:
        row = (await session.execute(
            select(FitPreference).where(
                FitPreference.profile_id == profile.id
            )
        )).scalar_one_or_none()
    # Projection created — the exact shape depends on the projection but a
    # row must exist for downstream planner reads.
    assert row is not None
    assert row.usual_top_size == "M"
