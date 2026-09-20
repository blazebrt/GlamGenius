"""Step 11E: persisted Care execution belongs to one household subject.

The account still owns the physical shelf. These tests prove that persisted
routine identity, steps through that routine, adherence, wash cadence and
compiler provenance no longer become household-global merely because the
products are shared.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.family.decision_subject import canonicalize_decision_subject
from app.domains.family.subject import (
    AGE_BAND_NOT_STATED,
    SUBJECT_HOUSEHOLD_MEMBER,
    ResolvedSubject,
)
from app.domains.routines import adherence
from app.domains.routines.models import (
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11d_subject_care_authority import _member, _self_subject_id

pytestmark = pytest.mark.asyncio


async def _generate(client, token: str, subject_id: str | None = None):
    url = "/api/v2/routines/generate"
    if subject_id is not None:
        url += f"?subject_id={subject_id}"
    response = await client.post(
        url,
        headers=auth(token),
        json={"kinds": ["morning", "evening", "wash_day"], "explain": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _morning_by_subject(account_id: uuid.UUID):
    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(Routine)
            .where(Routine.account_id == account_id, Routine.kind == "morning")
            .order_by(Routine.created_at, Routine.id)
        )).scalars().all()
        return rows


async def _step_for_subject(
    account_id: uuid.UUID, subject_id: uuid.UUID, *, slot: str = "cleanser",
) -> tuple[Routine, RoutineStep]:
    async with get_sessionmaker()() as session:
        routine = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.household_subject_id == subject_id,
                Routine.kind == "morning",
            )
        )).scalar_one()
        step = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == routine.id,
                RoutineStep.slot == slot,
            )
        )).scalar_one()
        return routine, step


async def test_legacy_self_routine_adopts_in_place_and_keeps_adherence(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)

    first = await _generate(app_client, token)
    legacy_morning = next(row for row in first["routines"] if row["kind"] == "morning")
    legacy_routine_id = uuid.UUID(legacy_morning["id"])
    cleanser = next(
        row for row in legacy_morning["steps"] if row["slot"] == "cleanser"
    )
    done_on = date(2026, 9, 20)
    completed = await app_client.post(
        f"/api/v2/routines/steps/{cleanser['id']}/complete",
        headers=auth(token),
        json={"done_on": done_on.isoformat(), "completed": True},
    )
    assert completed.status_code == 200, completed.text

    await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    second = await _generate(app_client, token)
    assert second["subject"] == {
        "household_subject_id": str(self_id),
        "is_account_holder": True,
    }

    async with get_sessionmaker()() as session:
        mornings = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "morning",
            )
        )).scalars().all()
        assert len(mornings) == 1
        assert mornings[0].id == legacy_routine_id
        assert mornings[0].household_subject_id == self_id
        history = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.routine_id == legacy_routine_id,
                RoutineAdherence.done_on == done_on,
            )
        )).scalar_one()
        assert history.completed is True


async def test_self_and_two_members_can_hold_same_kind_without_cloning_products(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    self_id = await _self_subject_id(account_id)

    self_result = await _generate(app_client, token)
    a_result = await _generate(app_client, token, member_a)
    b_result = await _generate(app_client, token, member_b)

    assert self_result["subject"]["household_subject_id"] == str(self_id)
    assert a_result["subject"]["household_subject_id"] == member_a
    assert b_result["subject"]["household_subject_id"] == member_b

    rows = await _morning_by_subject(account_id)
    assert {row.household_subject_id for row in rows} == {
        self_id, uuid.UUID(member_a), uuid.UUID(member_b),
    }
    assert len(rows) == 3

    async with get_sessionmaker()() as session:
        cleanser_item_ids = []
        for routine in rows:
            step = (await session.execute(
                select(RoutineStep).where(
                    RoutineStep.routine_id == routine.id,
                    RoutineStep.slot == "cleanser",
                )
            )).scalar_one()
            cleanser_item_ids.append(step.inventory_item_id)
    assert len(set(cleanser_item_ids)) == 1, (
        "subjects may share the physical product without cloning inventory"
    )


async def test_completion_and_consistency_are_isolated_by_selected_subject(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    await _generate(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)

    routine_a, step_a = await _step_for_subject(
        account_id, uuid.UUID(member_a),
    )
    _routine_b, _step_b = await _step_for_subject(
        account_id, uuid.UUID(member_b),
    )

    done = await app_client.post(
        f"/api/v2/routines/steps/{step_a.id}/complete?subject_id={member_a}",
        headers=auth(token),
        json={"completed": True},
    )
    assert done.status_code == 200, done.text
    assert done.json()["subject"]["household_subject_id"] == member_a

    wrong_subject = await app_client.post(
        f"/api/v2/routines/steps/{step_a.id}/complete?subject_id={member_b}",
        headers=auth(token),
        json={"completed": True},
    )
    assert wrong_subject.status_code == 404

    async with get_sessionmaker()() as session:
        rows = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].routine_id == routine_a.id

    a_consistency = await app_client.get(
        f"/api/v2/routines/consistency?days=1&subject_id={member_a}",
        headers=auth(token),
    )
    b_consistency = await app_client.get(
        f"/api/v2/routines/consistency?days=1&subject_id={member_b}",
        headers=auth(token),
    )
    assert a_consistency.status_code == b_consistency.status_code == 200
    assert a_consistency.json()["steps_completed"] == 1
    assert b_consistency.json()["steps_completed"] == 0


async def test_wash_anchor_uses_only_the_selected_subject_history(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member_a = await _member(app_client, token)
    member_b = await _member(app_client, token)
    await _generate(app_client, token, member_a)
    await _generate(app_client, token, member_b)

    async with get_sessionmaker()() as session:
        routines = (await session.execute(
            select(Routine).where(
                Routine.account_id == account_id,
                Routine.kind == "wash_day",
            )
        )).scalars().all()
        by_subject = {row.household_subject_id: row for row in routines}
        wash_a = by_subject[uuid.UUID(member_a)]
        shampoo_a = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == wash_a.id,
                RoutineStep.slot == "shampoo",
            )
        )).scalar_one()

    anchor = date(2026, 9, 19)
    response = await app_client.post(
        f"/api/v2/routines/steps/{shampoo_a.id}/complete?subject_id={member_a}",
        headers=auth(token),
        json={"done_on": anchor.isoformat(), "completed": True},
    )
    assert response.status_code == 200, response.text

    async with get_sessionmaker()() as session:
        claim_a = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER,
            account_id=account_id,
            subject_id=uuid.UUID(member_a),
            relation="adult",
            age_band=AGE_BAND_NOT_STATED,
        )
        claim_b = ResolvedSubject(
            kind=SUBJECT_HOUSEHOLD_MEMBER,
            account_id=account_id,
            subject_id=uuid.UUID(member_b),
            relation="adult",
            age_band=AGE_BAND_NOT_STATED,
        )
        checked_a = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=claim_a,
        )
        checked_b = await canonicalize_decision_subject(
            session, principal_account_id=account_id, decision_subject=claim_b,
        )
        assert await adherence.last_completed_wash_on(
            session,
            account_id=account_id,
            through=anchor,
            decision_subject=checked_a,
        ) == anchor
        assert await adherence.last_completed_wash_on(
            session,
            account_id=account_id,
            through=anchor,
            decision_subject=checked_b,
        ) is None


async def test_new_recommendation_runs_record_subject_without_rewriting_legacy(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _generate(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)

    async with get_sessionmaker()() as session:
        runs = (await session.execute(
            select(RoutineRecommendationRun)
            .where(RoutineRecommendationRun.account_id == account_id)
            .order_by(RoutineRecommendationRun.created_at)
        )).scalars().all()
    assert runs[0].household_subject_id is None
    assert runs[-1].household_subject_id == uuid.UUID(member)
