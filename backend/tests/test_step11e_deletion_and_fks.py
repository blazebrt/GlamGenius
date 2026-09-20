"""Step 11E deletion authority for subject-owned persisted routine graphs."""
from __future__ import annotations

import uuid

import pytest
from app.domains.family.models import FamilyProfile
from app.domains.identity.models import Account
from app.domains.media.storage import factory as storage_factory
from app.domains.privacy import deletion_service
from app.domains.privacy.models import STATE_COMPLETE, AccountDeletionJob
from app.domains.routines.models import (
    Routine,
    RoutineAdherence,
    RoutineRecommendationRun,
    RoutineStep,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import auth
from tests.test_account_deletion_state_machine import _FakeStorage, _FakeSupabaseAdmin
from tests.test_domain_routines_api import _seeded_shelf
from tests.test_step11d_subject_care_authority import _member

pytestmark = pytest.mark.asyncio


async def _generate(client, token: str, subject_id: str):
    response = await client.post(
        f"/api/v2/routines/generate?subject_id={subject_id}",
        headers=auth(token),
        json={"kinds": ["morning"], "explain": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_direct_family_profile_delete_is_blocked_by_subject_routine_identity(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, _account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)

    async with get_sessionmaker()() as session:
        profile = await session.get(FamilyProfile, uuid.UUID(member))
        assert profile is not None
        await session.delete(profile)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with get_sessionmaker()() as session:
        assert await session.get(FamilyProfile, uuid.UUID(member)) is not None
        routine = (await session.execute(
            select(Routine).where(
                Routine.household_subject_id == uuid.UUID(member),
            )
        )).scalar_one()
        assert routine is not None
        run = (await session.execute(
            select(RoutineRecommendationRun).where(
                RoutineRecommendationRun.household_subject_id == uuid.UUID(member),
            )
        )).scalar_one()
        assert run is not None


async def test_each_piece_of_a_members_routine_identity_blocks_the_delete_on_its_own(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    """Two protections, proved one at a time.

    A member who has generated a routine leaves two rows naming them: the
    routine itself and the run that built it. With both present, a delete is
    refused — but that proves only that *something* refused, and either foreign
    key could be carrying the other. If one were ever relaxed to cascade, the
    member's Care history would start disappearing with them silently, and the
    combined test would keep passing on the strength of the one that was left.

    So each is asked alone: remove the runs and the routine must still refuse,
    remove the routine graph and the run must still refuse.
    """
    token, _account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    member = await _member(app_client, token)
    await _generate(app_client, token, member)
    member_uuid = uuid.UUID(member)

    async def _delete_is_refused() -> None:
        async with get_sessionmaker()() as session:
            profile = await session.get(FamilyProfile, member_uuid)
            assert profile is not None
            await session.delete(profile)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
        async with get_sessionmaker()() as session:
            assert await session.get(FamilyProfile, member_uuid) is not None

    # The routine alone.
    async with get_sessionmaker()() as session:
        await session.execute(text(
            "DELETE FROM routine_recommendation_runs WHERE household_subject_id = :id"
        ), {"id": member_uuid})
        await session.commit()
    await _delete_is_refused()

    # The run alone.
    await _generate(app_client, token, member)
    async with get_sessionmaker()() as session:
        await session.execute(text(
            "DELETE FROM routine_adherence WHERE routine_id IN "
            "(SELECT id FROM routines WHERE household_subject_id = :id)"
        ), {"id": member_uuid})
        await session.execute(text(
            "DELETE FROM routine_steps WHERE routine_id IN "
            "(SELECT id FROM routines WHERE household_subject_id = :id)"
        ), {"id": member_uuid})
        await session.execute(text(
            "DELETE FROM routines WHERE household_subject_id = :id"
        ), {"id": member_uuid})
        await session.commit()
    async with get_sessionmaker()() as session:
        remaining = await session.scalar(text(
            "SELECT count(*) FROM routine_recommendation_runs "
            "WHERE household_subject_id = :id"
        ), {"id": member_uuid})
    assert remaining, "the run that built the routine should still name the member"
    await _delete_is_refused()


async def test_real_account_deletion_erases_subject_routine_graph_and_keeps_tombstone(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    storage = _FakeStorage()
    admin = _FakeSupabaseAdmin()
    storage_factory.set_storage(storage)
    monkeypatch.setattr(deletion_service, "get_supabase_admin", lambda: admin)

    try:
        token, account_id = await registered_supabase_user()
        await _seeded_shelf(app_client, token)
        member = await _member(app_client, token)
        generated = await _generate(app_client, token, member)
        morning = generated["routines"][0]
        step_id = uuid.UUID(morning["steps"][0]["id"])
        routine_id = uuid.UUID(morning["id"])

        completed = await app_client.post(
            f"/api/v2/routines/steps/{step_id}/complete?subject_id={member}",
            headers=auth(token),
            json={"completed": True},
        )
        assert completed.status_code == 200, completed.text

        async with get_sessionmaker()() as session:
            step_ids = set((await session.execute(
                select(RoutineStep.id).where(RoutineStep.routine_id == routine_id)
            )).scalars().all())
            adherence_ids = set((await session.execute(
                select(RoutineAdherence.id).where(
                    RoutineAdherence.account_id == account_id,
                )
            )).scalars().all())
            run_ids = set((await session.execute(
                select(RoutineRecommendationRun.id).where(
                    RoutineRecommendationRun.account_id == account_id,
                )
            )).scalars().all())

        async with get_sessionmaker()() as session:
            await deletion_service.request_deletion(session, account_id)
            await session.commit()
        async with get_sessionmaker()() as session:
            assert await deletion_service.drain_all(session) >= 1
            await session.commit()

        async with get_sessionmaker()() as session:
            assert await session.get(Account, account_id) is None
            assert await session.get(FamilyProfile, uuid.UUID(member)) is None
            assert await session.get(Routine, routine_id) is None
            for step in step_ids:
                assert await session.get(RoutineStep, step) is None
            for adherence_id in adherence_ids:
                assert await session.get(RoutineAdherence, adherence_id) is None
            for run_id in run_ids:
                assert await session.get(RoutineRecommendationRun, run_id) is None
            job = (await session.execute(
                select(AccountDeletionJob).where(
                    AccountDeletionJob.account_id == account_id,
                )
            )).scalar_one()
            assert job.state == STATE_COMPLETE
            assert job.completed_at is not None
        assert admin.deleted_users == [str(account_id)]
    finally:
        storage_factory.set_storage(None)
