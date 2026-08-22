"""V3-03.10 explicit Care simplification contract and integration coverage."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.profile.models import AppearanceProfile, ProfileAttribute, ProfileChangeEvent
from app.domains.routines import service as routines_service
from app.domains.routines.models import Routine, RoutineAdherence, RoutineRecommendationRun, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_v3_03_3_integration import (
    GENERATION_DATE,
    _allergy,
    _effort,
)
from tests.test_v3_03_3_integration import (
    _generate as _db_generate,
)
from tests.test_v3_03_3_integration import (
    _product as _db_product,
)
from tests.test_v3_03_3_integration import (
    _seed as _db_seed,
)


def test_simplification_ladder_is_closed_and_one_step_only():
    from app.domains.care.routine_plan import CareRoutineEffort
    from app.domains.care.simplification import (
        CareSimplificationStatus,
        decide_care_simplification,
    )

    detailed = decide_care_simplification(CareRoutineEffort.DETAILED)
    balanced = decide_care_simplification(CareRoutineEffort.BALANCED)
    minimal = decide_care_simplification(CareRoutineEffort.MINIMAL)
    assert (detailed.target_effort, detailed.status.value, detailed.reason) == (
        CareRoutineEffort.BALANCED, CareSimplificationStatus.AVAILABLE.value,
        "explicit_user_simplification_request",
    )
    assert (balanced.target_effort, balanced.status.value) == (
        CareRoutineEffort.MINIMAL, CareSimplificationStatus.AVAILABLE.value,
    )
    assert (minimal.target_effort, minimal.status.value, minimal.reason) == (
        None, CareSimplificationStatus.ALREADY_MINIMAL.value, "already_minimal",
    )


async def _latest_run(account_id: uuid.UUID) -> RoutineRecommendationRun:
    factory = get_sessionmaker()
    async with factory() as session:
        return (await session.execute(
            select(RoutineRecommendationRun)
            .where(RoutineRecommendationRun.account_id == account_id)
            .order_by(RoutineRecommendationRun.created_at.desc())
            .limit(1)
        )).scalar_one()


async def _set_usage(item_id: str, count: int = 1) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        item.usage_count = count
        await session.commit()


def _patch_today(monkeypatch) -> None:
    monkeypatch.setattr(clock, "local_today", lambda *_args, **_kwargs: GENERATION_DATE)


@pytest.mark.asyncio
async def test_detailed_to_balanced_applies_profile_history_and_audit_trigger(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    cleanser = await _db_product(app_client, token, name="Core Cleanser", product_type="cleanser")
    toner = await _db_product(app_client, token, name="Established Toner", product_type="toner")
    await _set_usage(toner)
    await _effort(app_client, token, "detailed")
    await _db_generate(app_client, token, kinds=["morning"])
    before = await _latest_run(account_id)
    before_snapshot = before.inputs["care_snapshot"]
    _patch_today(monkeypatch)

    response = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["previous_effort"] == "detailed"
    assert body["new_effort"] == "balanced"
    assert body["removed_optional_slots"] == []
    assert "visible steps stay the same" in body["message"]

    after = await _latest_run(account_id)
    after_snapshot = after.inputs["care_snapshot"]
    assert after.inputs["care_adjustment"] == {
        "version": "v3-03.10",
        "kind": "explicit_simplification",
        "from_effort": "detailed",
        "to_effort": "balanced",
        "profile_change_reason": "care_simplification_v3_03_10",
    }
    assert after_snapshot["snapshot_version"] == "v3-03.18"
    assert before_snapshot["decisions"]["decision_fingerprint"] == after_snapshot["decisions"]["decision_fingerprint"]
    assert before_snapshot["routine_plan"]["routine_plan_fingerprint"] != after_snapshot["routine_plan"]["routine_plan_fingerprint"]
    assert cleanser in str(after_snapshot)
    assert toner in str(after_snapshot)

    factory = get_sessionmaker()
    async with factory() as session:
        profile = (await session.execute(
            select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
        )).scalar_one()
        effort = (await session.execute(
            select(ProfileAttribute).where(ProfileAttribute.profile_id == profile.id, ProfileAttribute.key == "care_routine_effort")
        )).scalar_one()
        event = (await session.execute(
            select(ProfileChangeEvent).where(
                ProfileChangeEvent.profile_id == profile.id,
                ProfileChangeEvent.attribute_key == "care_routine_effort",
            ).order_by(ProfileChangeEvent.created_at.desc())
        )).scalars().first()
    assert effort.value == "balanced"
    assert effort.source == "user_declared"
    assert effort.verification_state == "confirmed"
    assert event.attribute_key == "care_routine_effort"
    assert event.old_value == "detailed" and event.new_value == "balanced"
    assert event.source == "user_declared"
    assert event.reason == "care_simplification_v3_03_10"
    today = await app_client.get(
        f"/api/v2/routines/today?on={GENERATION_DATE.isoformat()}", headers=auth(token),
    )
    assert today.status_code == 200, today.text
    assert today.json()["refresh_required"] is False


@pytest.mark.asyncio
async def test_balanced_to_minimal_removes_optional_step_but_preserves_adherence_and_restores(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _db_product(app_client, token, name="Core Cleanser", product_type="cleanser")
    toner = await _db_product(app_client, token, name="Established Toner", product_type="toner")
    await _set_usage(toner)
    await _effort(app_client, token, "balanced")
    await _db_generate(app_client, token, kinds=["morning"])
    factory = get_sessionmaker()
    async with factory() as session:
        step = (await session.execute(
            select(RoutineStep).join(Routine).where(
                Routine.account_id == account_id, Routine.kind == "morning", RoutineStep.slot == "toner",
            )
        )).scalar_one()
        step_id = step.id
    completed = await app_client.post(
        f"/api/v2/routines/steps/{step_id}/complete", headers=auth(token),
        json={"completed": True, "done_on": GENERATION_DATE.isoformat()},
    )
    assert completed.status_code == 200, completed.text
    _patch_today(monkeypatch)
    response = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_effort"] == "minimal"
    assert any(row["slot"] == "toner" and row["inventory_item_id"] == toner for row in body["removed_optional_slots"])

    async with factory() as session:
        current = (await session.execute(
            select(RoutineStep).join(Routine).where(
                Routine.account_id == account_id, Routine.kind == "morning", RoutineStep.slot == "toner",
            )
        )).scalar_one_or_none()
        history = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id, RoutineAdherence.slot == "toner",
                RoutineAdherence.done_on == GENERATION_DATE,
            )
        )).scalar_one()
    assert current is None
    assert history.completed is True
    assert history.step_id is None

    restored = await _effort(app_client, token, "balanced")
    assert restored is None
    await _db_generate(app_client, token, kinds=["morning"])
    async with factory() as session:
        restored_step = (await session.execute(
            select(RoutineStep).join(Routine).where(
                Routine.account_id == account_id, Routine.kind == "morning", RoutineStep.slot == "toner",
            )
        )).scalar_one()
        rows = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id, RoutineAdherence.slot == "toner")
        )).scalars().all()
    assert restored_step.id != step_id
    assert len(rows) == 1 and rows[0].completed is True


@pytest.mark.asyncio
async def test_missing_and_not_sure_resolve_balanced_then_store_minimal(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    _patch_today(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    missing = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert missing.status_code == 200, missing.text
    assert missing.json()["previous_effort"] == "balanced"
    assert missing.json()["new_effort"] == "minimal"
    await _effort(app_client, token, "not_sure")
    not_sure = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert not_sure.status_code == 200, not_sure.text
    assert not_sure.json()["previous_effort"] == "balanced"
    factory = get_sessionmaker()
    async with factory() as session:
        profile = (await session.execute(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))).scalar_one()
        events = (await session.execute(
            select(ProfileChangeEvent).where(ProfileChangeEvent.profile_id == profile.id).order_by(ProfileChangeEvent.created_at)
        )).scalars().all()
    assert events[0].old_value is None and events[0].new_value == "minimal"
    assert events[1].old_value == "minimal" and events[1].new_value == "not_sure"
    assert events[2].old_value == "not_sure" and events[2].new_value == "minimal"


@pytest.mark.asyncio
async def test_minimal_is_idempotent_and_improve_is_read_only(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    _patch_today(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _effort(app_client, token, "minimal")
    await _db_generate(app_client, token, kinds=["morning"])
    factory = get_sessionmaker()
    async with factory() as session:
        profile = (await session.execute(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))).scalar_one()
        before = {
            "version": profile.version,
            "events": await session.scalar(select(func.count(ProfileChangeEvent.id)).where(ProfileChangeEvent.profile_id == profile.id)),
            "runs": await session.scalar(select(func.count(RoutineRecommendationRun.id)).where(RoutineRecommendationRun.account_id == account_id)),
        }
    overview = await app_client.get("/api/v2/routines/improve", headers=auth(token))
    assert overview.status_code == 200, overview.text
    assert overview.json()["routine_effort"] == {
        "resolved": "minimal", "source": "user_declared", "can_simplify": False, "next_simpler": None,
    }
    assert overview.json()["care_product_controls"] == []
    no_op = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["status"] == "already_minimal"
    async with factory() as session:
        profile = (await session.execute(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))).scalar_one()
        after = {
            "version": profile.version,
            "events": await session.scalar(select(func.count(ProfileChangeEvent.id)).where(ProfileChangeEvent.profile_id == profile.id)),
            "runs": await session.scalar(select(func.count(RoutineRecommendationRun.id)).where(RoutineRecommendationRun.account_id == account_id)),
        }
    assert after == before


@pytest.mark.asyncio
async def test_simplification_preserves_safety_and_isolated_export(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    _patch_today(monkeypatch)
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _db_seed(app_client)
    blocked = await _db_product(app_client, token_a, name="Blocked", active_ingredients=["fragrance"])
    expired = await _db_product(app_client, token_a, name="Expired", expiry=date(2026, 8, 1))
    await _allergy(app_client, token_a)
    await _effort(app_client, token_a, "balanced")
    await _db_generate(app_client, token_a, kinds=["morning"])
    await _effort(app_client, token_b, "balanced")
    await _db_generate(app_client, token_b, kinds=["morning"])
    before = await _latest_run(account_a)
    result = await app_client.post("/api/v2/routines/simplify", headers=auth(token_a))
    assert result.status_code == 200, result.text
    after = await _latest_run(account_a)
    before_rows = {row["item_id"]: row for row in before.inputs["care_snapshot"]["decisions"]["product_decisions"]}
    after_rows = {row["item_id"]: row for row in after.inputs["care_snapshot"]["decisions"]["product_decisions"]}
    assert before_rows[blocked]["blocking_reasons"] == after_rows[blocked]["blocking_reasons"]
    assert before_rows[expired]["blocking_reasons"] == after_rows[expired]["blocking_reasons"]
    assert after.inputs["care_adjustment"]["to_effort"] == "minimal"
    exported = (await app_client.get("/api/v2/privacy/export", headers=auth(token_a))).json()
    runs = exported["domains"]["routines"]["recommendation_runs"]
    assert any(row["inputs"].get("care_adjustment") for row in runs)
    assert str(account_b) not in str(exported)


@pytest.mark.asyncio
async def test_simplification_trigger_is_separate_from_snapshot_identity(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    _patch_today(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _db_product(app_client, token, name="Core Cleanser", product_type="cleanser")
    await _effort(app_client, token, "balanced")
    simplified = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert simplified.status_code == 200, simplified.text
    adjustment_run = await _latest_run(account_id)
    adjustment_snapshot = adjustment_run.inputs["care_snapshot"]
    assert "care_adjustment" in adjustment_run.inputs
    direct = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"kinds": [], "as_of": GENERATION_DATE.isoformat(), "explain": False},
    )
    assert direct.status_code == 200, direct.text
    direct_run = await _latest_run(account_id)
    assert "care_adjustment" not in direct_run.inputs
    assert direct_run.inputs["care_snapshot"] == adjustment_snapshot


@pytest.mark.asyncio
async def test_simplification_rollback_leaves_profile_history_and_runs_unchanged(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    _patch_today(monkeypatch)
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _effort(app_client, token, "balanced")
    factory = get_sessionmaker()
    async with factory() as session:
        profile = await session.scalar(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))
        baseline_version = profile.version
        baseline_events = await session.scalar(
            select(func.count(ProfileChangeEvent.id)).where(ProfileChangeEvent.profile_id == profile.id)
        )
    original = routines_service.generate_routines

    async def fail(*_args, **_kwargs):
        raise RuntimeError("regeneration failed")

    monkeypatch.setattr(routines_service, "generate_routines", fail)
    async with factory() as session:
        with pytest.raises(RuntimeError):
            await routines_service.simplify_care_routine(
                session, account_id=account_id, account_id_str=str(account_id),
            )
        await session.rollback()
        profile = await session.scalar(select(AppearanceProfile).where(AppearanceProfile.account_id == account_id))
        assert profile is not None
        assert profile.version == baseline_version
        assert await session.scalar(select(ProfileAttribute.value).where(ProfileAttribute.profile_id == profile.id, ProfileAttribute.key == "care_routine_effort")) == "balanced"
        assert await session.scalar(select(func.count(ProfileChangeEvent.id)).where(ProfileChangeEvent.profile_id == profile.id)) == baseline_events
        assert await session.scalar(select(func.count(RoutineRecommendationRun.id)).where(RoutineRecommendationRun.account_id == account_id)) == 0
    monkeypatch.setattr(routines_service, "generate_routines", original)
