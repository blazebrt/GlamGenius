"""V3-03.7 database-backed routine identity and adherence regressions."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.bootstrap import run as run_seed
from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.routines import compiler, service
from app.domains.routines.models import RoutineAdherence, RoutineStep
from app.domains.routines.schemas import RoutineStepComplete
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_domain_routines_api import _generate, _seeded_shelf

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _force_morning(monkeypatch):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")


def _compiled(*steps: tuple[str, str | None, bool, bool], kind: str = "morning") -> compiler.CompiledRoutine:
    return compiler.CompiledRoutine(
        kind=kind, label=f"{kind.title()} routine", frequency="daily",
        steps=[
            compiler.RoutineStep(
                slot=slot, label=slot.title(), order=position, required=required,
                why=f"Why {slot}", frequency="daily", item_id=item_id, is_gap=is_gap,
            )
            for position, (slot, item_id, required, is_gap) in enumerate(steps, start=1)
        ],
    )


def _compiled_from_response(
    routine: dict, *, without: set[str] | frozenset[str] = frozenset(),
) -> compiler.CompiledRoutine:
    return compiler.CompiledRoutine(
        kind=routine["kind"], label=routine["label"], frequency=routine["frequency"],
        steps=[
            compiler.RoutineStep(
                slot=step["slot"], label=step["label"], order=step["order"],
                required=step["required"], why=step["why"], frequency=step["frequency"],
                item_id=step["inventory_item_id"], product_name=step["product_name"],
                safety_note=step["safety_note"], alternative=step["alternative"],
                climate_note=step["climate_note"], is_gap=step["is_gap"],
            )
            for step in routine["steps"] if step["slot"] not in without
        ],
    )


async def _persist(session, account_id, built):
    await service._replace_routines(
        session, account_id, [built], climate=None, explanation_source="deterministic",
    )
    await session.commit()


async def _routine_step(session, account_id, slot="cleanser"):
    routine = (await session.execute(
        select(service.Routine).where(
            service.Routine.account_id == account_id,
            service.Routine.kind == "morning",
        )
    )).scalar_one()
    step = (await session.execute(
        select(RoutineStep).where(RoutineStep.routine_id == routine.id, RoutineStep.slot == slot)
    )).scalar_one()
    return routine, step


async def _products(account_id):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        first = InventoryItem(
            account_id=account_id, category="beauty", subcategory="cleanser",
            display_name="Cleanser A",
        )
        second = InventoryItem(
            account_id=account_id, category="beauty", subcategory="cleanser",
            display_name="Cleanser B",
        )
        session.add_all([first, second])
        await session.commit()
        return first.id, second.id


async def test_same_plan_regeneration_preserves_step_and_adherence_identity(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, _ = await _products(account_id)
    factory = get_sessionmaker()
    done_on = date(2026, 8, 10)

    async with factory() as session:
        built = _compiled(("cleanser", str(item_a), True, False))
        await _persist(session, account_id, built)
        routine, step = await _routine_step(session, account_id)
        await service.complete_step(
            session, account_id=account_id, step_id=step.id,
            body=RoutineStepComplete(done_on=done_on, note="steady"),
        )
        await session.commit()
        adherence = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.routine_id == routine.id,
            )
        )).scalar_one()
        step_id, adherence_id = step.id, adherence.id

        await _persist(session, account_id, built)
        _, current = await _routine_step(session, account_id)
        rows = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id)
        )).scalars().all()

    assert current.id == step_id
    assert len(rows) == 1
    assert rows[0].id == adherence_id
    assert rows[0].slot == "cleanser"
    assert rows[0].completed is True


async def test_product_winner_change_updates_current_material_not_history(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, item_b = await _products(account_id)
    factory = get_sessionmaker()
    done_on = date(2026, 8, 10)

    async with factory() as session:
        await _persist(session, account_id, _compiled(("cleanser", str(item_a), True, False)))
        routine, step = await _routine_step(session, account_id)
        await service.complete_step(
            session, account_id=account_id, step_id=step.id,
            body=RoutineStepComplete(done_on=done_on),
        )
        await session.commit()
        adherence = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id)
        )).scalar_one()
        await _persist(session, account_id, _compiled(("cleanser", str(item_b), True, False)))
        _, current = await _routine_step(session, account_id)
        after = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id)
        )).scalar_one()

    assert current.id == step.id
    assert current.inventory_item_id == item_b
    assert after.id == adherence.id
    assert after.step_id == current.id
    assert after.slot == "cleanser"


async def test_filled_gap_filled_keeps_slot_uuid_and_adherence(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, item_b = await _products(account_id)
    factory = get_sessionmaker()
    done_on = date(2026, 8, 10)

    async with factory() as session:
        await _persist(session, account_id, _compiled(("cleanser", str(item_a), True, False)))
        _, step = await _routine_step(session, account_id)
        original_id = step.id
        await service.complete_step(
            session, account_id=account_id, step_id=step.id,
            body=RoutineStepComplete(done_on=done_on),
        )
        await session.commit()

        await _persist(session, account_id, _compiled(("cleanser", None, True, True)))
        _, gap = await _routine_step(session, account_id)
        assert gap.id == original_id
        assert gap.is_gap is True

        await _persist(session, account_id, _compiled(("cleanser", str(item_b), True, False)))
        _, filled = await _routine_step(session, account_id)
        adherence = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id)
        )).scalar_one()

    assert filled.id == original_id
    assert filled.inventory_item_id == item_b
    assert adherence.step_id == original_id
    assert adherence.completed is True


async def test_optional_slot_removal_and_restore_preserves_history_and_same_day_upsert(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, item_b = await _products(account_id)
    factory = get_sessionmaker()
    historical = date(2026, 8, 10)
    future = date(2026, 8, 11)

    async with factory() as session:
        detailed = _compiled(
            ("cleanser", str(item_a), True, False),
            ("toner", str(item_b), False, False),
        )
        await _persist(session, account_id, detailed)
        routine, toner = await _routine_step(session, account_id, slot="toner")
        old_toner_id = toner.id
        await service.complete_step(
            session, account_id=account_id, step_id=toner.id,
            body=RoutineStepComplete(done_on=historical),
        )
        await session.commit()

        await _persist(session, account_id, _compiled(("cleanser", str(item_a), True, False)))
        detached = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.routine_id == routine.id,
                RoutineAdherence.slot == "toner",
            )
        )).scalar_one()
        assert detached.step_id is None

        restored = _compiled(
            ("cleanser", str(item_a), True, False),
            ("toner", str(item_b), False, False),
        )
        await _persist(session, account_id, restored)
        _, current_toner = await _routine_step(session, account_id, slot="toner")
        assert current_toner.id != old_toner_id

        # Reattaching the same historical date updates the logical row.
        await service.complete_step(
            session, account_id=account_id, step_id=current_toner.id,
            body=RoutineStepComplete(done_on=historical, note="reattached"),
        )
        await session.commit()
        same_day = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.routine_id == routine.id,
                RoutineAdherence.slot == "toner",
                RoutineAdherence.done_on == historical,
            )
        )).scalars().all()
        assert len(same_day) == 1
        assert same_day[0].step_id == current_toner.id

        await service.complete_step(
            session, account_id=account_id, step_id=current_toner.id,
            body=RoutineStepComplete(done_on=future),
        )
        await session.commit()
        total = (await session.execute(
            select(func.count(RoutineAdherence.id)).where(
                RoutineAdherence.routine_id == routine.id,
                RoutineAdherence.slot == "toner",
            )
        )).scalar_one()

    assert total == 2


async def test_completed_today_uses_routine_and_slot_not_step_uuid(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    # This route-level regression exercises the same logical key after the
    # current rendering UUID was deleted and recreated.
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    toner_response = await app_client.post(
        "/api/v2/inventory/items", headers=auth(token), json={
            "category": "beauty", "display_name": "Established Toner", "subcategory": "toner",
            "details": {"product_type": "toner", "purpose": "hydration", "routine_position": "tone"},
        },
    )
    assert toner_response.status_code in (200, 201), toner_response.text
    toner_id = toner_response.json()["id"]
    factory = get_sessionmaker()
    async with factory() as session:
        toner = await session.get(InventoryItem, toner_id)
        assert toner is not None
        toner.usage_count = 4
        await session.commit()

    first = (await _generate(app_client, token, kinds=["morning"])).json()
    morning = next(row for row in first["routines"] if row["kind"] == "morning")
    toner_step = next(step for step in morning["steps"] if step["slot"] == "toner")
    original_step_id = toner_step["id"]
    done_on = date(2026, 8, 10).isoformat()
    response = await app_client.post(
        f"/api/v2/routines/steps/{original_step_id}/complete",
        headers=auth(token), json={"done_on": done_on, "completed": True},
    )
    assert response.status_code == 200, response.text

    removed = _compiled_from_response(morning, without={"toner"})
    restored = _compiled_from_response(morning)
    routine_id = uuid.UUID(morning["id"])
    async with factory() as session:
        await service._replace_routines(
            session, account_id, [removed],
            climate=None, explanation_source="deterministic",
        )
        await session.commit()

    async with factory() as session:
        detached = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.slot == "toner",
                RoutineAdherence.done_on == date.fromisoformat(done_on),
            )
        )).scalar_one()
        assert detached.step_id is None

        await service._replace_routines(
            session, account_id, [restored],
            climate=None, explanation_source="deterministic",
        )
        await session.commit()

    async with factory() as session:
        restored_row = (await session.execute(
            select(RoutineStep).where(
                RoutineStep.routine_id == routine_id,
                RoutineStep.slot == "toner",
            )
        )).scalar_one()
        assert restored_row.id != original_step_id

    today = (await app_client.get(
        "/api/v2/routines/today?on=2026-08-10", headers=auth(token),
    )).json()
    returned_morning = next(row for row in today["routines"] if row["kind"] == "morning")
    returned_toner = next(step for step in returned_morning["steps"] if step["slot"] == "toner")
    assert returned_toner["completed_today"] is True


async def test_completion_does_not_change_inventory_usage(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, _ = await _products(account_id)
    factory = get_sessionmaker()
    async with factory() as session:
        await _persist(session, account_id, _compiled(("cleanser", str(item_a), True, False)))
        _, step = await _routine_step(session, account_id)
        await service.complete_step(
            session, account_id=account_id, step_id=step.id,
            body=RoutineStepComplete(done_on=date(2026, 8, 10)),
        )
        await session.commit()
        item = await session.get(InventoryItem, item_a)

    assert item is not None
    assert item.usage_count == 0
    assert item.last_used_at is None


async def test_routines_today_keeps_same_slot_scoped_to_routine(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "afternoon")
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    generated = (await _generate(app_client, token, kinds=["morning", "evening"])).json()
    morning = next(row for row in generated["routines"] if row["kind"] == "morning")
    evening = next(row for row in generated["routines"] if row["kind"] == "evening")
    morning_cleanser = next(step for step in morning["steps"] if step["slot"] == "cleanser")
    evening_cleanser = next(step for step in evening["steps"] if step["slot"] == "cleanser")
    assert morning["id"] != evening["id"]

    done_on = date(2026, 8, 10).isoformat()
    response = await app_client.post(
        f"/api/v2/routines/steps/{morning_cleanser['id']}/complete",
        headers=auth(token), json={"done_on": done_on, "completed": True},
    )
    assert response.status_code == 200, response.text

    today = (await app_client.get(
        "/api/v2/routines/today?on=2026-08-10", headers=auth(token),
    )).json()
    returned_morning = next(row for row in today["routines"] if row["kind"] == "morning")
    returned_evening = next(row for row in today["routines"] if row["kind"] == "evening")
    assert next(step for step in returned_morning["steps"] if step["slot"] == "cleanser")["completed_today"] is True
    assert next(step for step in returned_evening["steps"] if step["slot"] == "cleanser")["completed_today"] is False
    assert morning_cleanser["id"] != evening_cleanser["id"]


async def test_same_slot_in_morning_and_evening_isolated(
    db_clean, registered_supabase_user,
):
    _, account_id = await registered_supabase_user()
    item_a, _ = await _products(account_id)
    factory = get_sessionmaker()
    done_on = date(2026, 8, 10)

    async with factory() as session:
        await service._replace_routines(
            session, account_id,
            [
                _compiled(("cleanser", str(item_a), True, False), kind="morning"),
                _compiled(("cleanser", str(item_a), True, False), kind="evening"),
            ],
            climate=None, explanation_source="deterministic",
        )
        await session.commit()
        routines = (await session.execute(
            select(service.Routine).where(service.Routine.account_id == account_id)
        )).scalars().all()
        morning = next(row for row in routines if row.kind == "morning")
        evening = next(row for row in routines if row.kind == "evening")
        morning_step = (await session.execute(
            select(RoutineStep).where(RoutineStep.routine_id == morning.id, RoutineStep.slot == "cleanser")
        )).scalar_one()
        await service.complete_step(
            session, account_id=account_id, step_id=morning_step.id,
            body=RoutineStepComplete(done_on=done_on),
        )
        await session.commit()
        rows = (await session.execute(
            select(RoutineAdherence).where(
                RoutineAdherence.account_id == account_id,
                RoutineAdherence.done_on == done_on,
            )
        )).scalars().all()

    assert len(rows) == 1
    assert rows[0].routine_id == morning.id
    assert rows[0].routine_id != evening.id
    assert rows[0].slot == "cleanser"
