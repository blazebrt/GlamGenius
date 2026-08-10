"""Pure and database-backed coverage for user-grounded Hair wash cadence."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.domains.care.cadence import (
    CARE_CADENCE_VERSION,
    HairWashCadenceReason,
    HairWashCadenceStatus,
    decide_hair_wash_cadence,
    hair_wash_cadence_fingerprint,
)
from app.domains.inventory.models import InventoryItem
from app.domains.planning import clock
from app.domains.planning.models import (
    DailyPlan,
    DailyPlanAction,
    DailyPlanInput,
    OutfitSchedule,
    PlanRecalculationEvent,
)
from app.domains.routines import adherence
from app.domains.routines.models import Routine, RoutineAdherence
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_domain_routines_api import _generate, _seeded_shelf

PLAN = date(2026, 8, 15)
TODAY = date(2026, 8, 17)


async def _profile(client, token: str, key: str, value) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": key, "value": value}]},
    )
    assert response.status_code == 200, response.text


async def _hair_product(client, token: str, *, name: str, product_type: str = "shampoo", expiry: date | None = None) -> str:
    details: dict[str, object] = {"product_type": product_type}
    if expiry is not None:
        details["expiry_date"] = expiry.isoformat()
    response = await client.post(
        "/api/v2/inventory/items", headers=auth(token), json={
            "category": "hair", "display_name": name,
            "subcategory": product_type, "details": details,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _beauty_product(client, token: str, *, name: str, product_type: str) -> str:
    response = await client.post(
        "/api/v2/inventory/items", headers=auth(token), json={
            "category": "beauty", "display_name": name,
            "subcategory": product_type, "details": {"product_type": product_type},
        },
    )
    assert response.status_code in (200, 201), response.text
    item_id = response.json()["id"]
    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(item_id))
        assert item is not None
        item.usage_count = 1
        await session.commit()
    return item_id


async def _stock_wardrobe(client, token: str) -> None:
    for category, name, subcategory, details in (
        ("wardrobe", "Charcoal Blazer", "blazer", {"colour": "charcoal", "fabric": "wool", "formality": "smart_casual", "season": ["all"]}),
        ("wardrobe", "White Cotton Shirt", "shirt", {"colour": "white", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]}),
        ("wardrobe", "Navy Chinos", "trousers", {"colour": "navy", "fabric": "cotton", "formality": "smart_casual", "season": ["all"]}),
        ("shoes", "Brown Leather Derbies", "derby", {"colour": "brown", "shoe_type": "derby", "occasion": ["work"]}),
    ):
        response = await client.post(
            "/api/v2/inventory/items", headers=auth(token), json={
                "category": category, "display_name": name,
                "subcategory": subcategory, "details": details,
            },
        )
        assert response.status_code in (200, 201), response.text


async def _plan_snapshot(account_id: uuid.UUID, plan_date: date = TODAY) -> dict:
    factory = get_sessionmaker()
    async with factory() as session:
        plan = (await session.execute(
            select(DailyPlan).where(
                DailyPlan.account_id == account_id, DailyPlan.plan_date == plan_date,
            )
        )).scalar_one()
        actions = (await session.execute(
            select(DailyPlanAction).where(DailyPlanAction.plan_id == plan.id)
        )).scalars().all()
        inputs = (await session.execute(
            select(DailyPlanInput).where(
                DailyPlanInput.plan_id == plan.id, DailyPlanInput.input_type == "care",
            )
        )).scalars().all()
        schedule = (await session.execute(
            select(OutfitSchedule).where(
                OutfitSchedule.account_id == account_id, OutfitSchedule.plan_date == plan_date,
            )
        )).scalar_one_or_none()
        events = await session.scalar(
            select(func.count()).select_from(PlanRecalculationEvent).where(
                PlanRecalculationEvent.account_id == account_id,
                PlanRecalculationEvent.plan_date == plan_date,
            )
        )
        return {
            "plan_id": plan.id,
            "version": plan.version,
            "cache_key": plan.cache_key,
            "look_id": plan.look_id,
            "schedule": (schedule.look_id, schedule.item_ids) if schedule else None,
            "care_values": {row.input_key: row.value for row in inputs},
            "care_input_ids": {row.input_key: row.id for row in inputs},
            "actions": {(row.module, row.action_type, row.title): row.id for row in actions},
            "events": events,
        }


async def _persist_adherence(
    account_id: uuid.UUID, *, status: str = "active",
    rows: list[tuple[str, date, bool]],
) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        routine = Routine(
            account_id=account_id, kind="wash_day", label="Wash Day",
            frequency="daily", status=status,
        )
        session.add(routine)
        await session.flush()
        for slot, done_on, completed in rows:
            session.add(RoutineAdherence(
                account_id=account_id, routine_id=routine.id, slot=slot,
                step_id=None, done_on=done_on, completed=completed,
            ))
        await session.commit()
        return routine.id


async def _ensure_weekly_routine(account_id: uuid.UUID) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        session.add(Routine(
            account_id=account_id, kind="weekly", label="Weekly extras",
            frequency="weekly", status="active",
        ))
        await session.commit()


@pytest.mark.parametrize(
    ("frequency", "status", "reason", "interval"),
    [
        ("daily", HairWashCadenceStatus.DUE, HairWashCadenceReason.DAILY_DECLARATION, 1),
        ("less_than_weekly", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_IMPRECISE, None),
        ("variable", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_VARIABLE, None),
        ("not_sure", HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_NOT_SURE, None),
        (None, HairWashCadenceStatus.UNSCHEDULED, HairWashCadenceReason.FREQUENCY_MISSING, None),
    ],
)
def test_unambiguous_and_ambiguous_frequency_contract(frequency, status, reason, interval):
    decision = decide_hair_wash_cadence(frequency, plan_date=PLAN, last_wash_on=None)
    assert decision.cadence_version == CARE_CADENCE_VERSION
    assert decision.status is status
    assert decision.reason is reason
    assert decision.interval_days == interval


def test_daily_history_is_due_yesterday_but_not_same_day():
    assert decide_hair_wash_cadence(
        "daily", plan_date=PLAN, last_wash_on=date(2026, 8, 14),
    ).status is HairWashCadenceStatus.DUE
    same_day = decide_hair_wash_cadence("daily", plan_date=PLAN, last_wash_on=PLAN)
    assert same_day.status is HairWashCadenceStatus.NOT_DUE
    assert same_day.next_due_on == date(2026, 8, 16)


def test_future_history_is_not_used_as_an_anchor():
    decision = decide_hair_wash_cadence(
        "weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 20),
    )
    assert decision.status is HairWashCadenceStatus.NEEDS_ANCHOR
    assert decision.last_wash_on is None


@pytest.mark.parametrize(
    ("frequency", "last_wash", "plan_date", "status", "next_due"),
    [
        ("several_times_week", date(2026, 8, 10), date(2026, 8, 11), HairWashCadenceStatus.NOT_DUE, date(2026, 8, 12)),
        ("several_times_week", date(2026, 8, 10), date(2026, 8, 12), HairWashCadenceStatus.DUE, date(2026, 8, 12)),
        ("weekly", date(2026, 8, 3), date(2026, 8, 9), HairWashCadenceStatus.NOT_DUE, date(2026, 8, 10)),
        ("weekly", date(2026, 8, 3), date(2026, 8, 11), HairWashCadenceStatus.DUE, date(2026, 8, 10)),
    ],
)
def test_interval_boundaries(frequency, last_wash, plan_date, status, next_due):
    decision = decide_hair_wash_cadence(frequency, plan_date=plan_date, last_wash_on=last_wash)
    assert decision.status is status
    assert decision.next_due_on == next_due


@pytest.mark.parametrize("frequency", ["several_times_week", "weekly"])
def test_history_is_required_as_anchor_for_non_daily(frequency):
    decision = decide_hair_wash_cadence(frequency, plan_date=PLAN, last_wash_on=None)
    assert decision.status is HairWashCadenceStatus.NEEDS_ANCHOR
    assert decision.reason is HairWashCadenceReason.NO_WASH_HISTORY


def test_fingerprint_is_deterministic_and_semantic():
    decision = decide_hair_wash_cadence("weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 8))
    assert hair_wash_cadence_fingerprint(decision) == hair_wash_cadence_fingerprint(decision)
    changed = decide_hair_wash_cadence("weekly", plan_date=PLAN, last_wash_on=date(2026, 8, 9))
    assert hair_wash_cadence_fingerprint(decision) != hair_wash_cadence_fingerprint(changed)


@pytest.mark.asyncio
async def test_daily_route_surfaces_wash_then_hides_it_after_completion(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    profile = await app_client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "care_hair_wash_frequency", "value": "daily"}]},
    )
    assert profile.status_code == 200, profile.text
    generated = (await _generate(app_client, token)).json()
    wash = next(row for row in generated["routines"] if row["kind"] == "wash_day")
    shampoo = next(step for step in wash["steps"] if step["slot"] == "shampoo")
    plan_date = "2026-08-17"

    before = (await app_client.get(
        f"/api/v2/routines/today?on={plan_date}", headers=auth(token),
    )).json()
    assert before["hair_wash_cadence"]["status"] == "due"
    assert "wash_day" in {row["kind"] for row in before["routines"]}

    completed = await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": plan_date, "completed": True},
    )
    assert completed.status_code == 200, completed.text
    after = (await app_client.get(
        f"/api/v2/routines/today?on={plan_date}", headers=auth(token),
    )).json()
    assert after["hair_wash_cadence"]["status"] == "not_due"
    assert "wash_day" not in {row["kind"] for row in after["routines"]}
    assert after["hair_wash_cadence"]["last_wash_on"] == plan_date


@pytest.mark.asyncio
async def test_database_history_adapter_is_core_slot_scoped_and_account_scoped(
    db_clean, registered_supabase_user,
):
    _, account_a = await registered_supabase_user()
    _, account_b = await registered_supabase_user()
    completion = date(2026, 8, 10)
    await _persist_adherence(
        account_a, status="retired", rows=[
            ("shampoo", completion, True),
            ("conditioner", completion, True),
            ("styling", date(2026, 8, 14), True),
            ("pre_wash_oil", date(2026, 8, 15), True),
            ("shampoo", date(2026, 8, 20), True),
            ("conditioner", date(2026, 8, 12), False),
        ],
    )
    await _persist_adherence(
        account_b, rows=[
            ("styling", completion, True),
            ("shampoo", completion, False),
        ],
    )
    factory = get_sessionmaker()
    async with factory() as session:
        result_a = await adherence.last_completed_wash_on(
            session, account_id=account_a, through=date(2026, 8, 17),
        )
        result_b = await adherence.last_completed_wash_on(
            session, account_id=account_b, through=date(2026, 8, 17),
        )
        future_ignored = await adherence.last_completed_wash_on(
            session, account_id=account_a, through=date(2026, 8, 19),
        )
        joined = (await session.execute(
            select(Routine, RoutineAdherence).join(
                RoutineAdherence, RoutineAdherence.routine_id == Routine.id,
            ).where(RoutineAdherence.account_id == account_a)
        )).all()

    assert result_a == completion
    assert result_b is None
    assert future_ignored == completion
    assert joined and all(row[0].account_id == account_a for row in joined)


@pytest.mark.asyncio
async def test_routines_today_uses_cadence_not_calendar_weekend(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "weekly")
    await _ensure_weekly_routine(account_id)
    await _beauty_product(app_client, token, name="Weekly Exfoliant", product_type="exfoliant")
    generated = (await _generate(
        app_client, token, kinds=["morning", "evening", "wash_day", "weekly"],
    )).json()
    wash = next(row for row in generated["routines"] if row["kind"] == "wash_day")
    shampoo = next(step for step in wash["steps"] if step["slot"] == "shampoo")
    completed = await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": "2026-08-10", "completed": True},
    )
    assert completed.status_code == 200, completed.text

    monday = (await app_client.get(
        "/api/v2/routines/today?on=2026-08-17", headers=auth(token),
    )).json()
    saturday = (await app_client.get(
        "/api/v2/routines/today?on=2026-08-15", headers=auth(token),
    )).json()
    assert monday["hair_wash_cadence"]["status"] == "due"
    assert "wash_day" in {row["kind"] for row in monday["routines"]}
    assert saturday["hair_wash_cadence"]["status"] == "not_due"
    assert "wash_day" not in {row["kind"] for row in saturday["routines"]}
    assert "weekly" in {row["kind"] for row in saturday["routines"]}


@pytest.mark.asyncio
async def test_needs_anchor_weekend_hides_wash_but_keeps_weekly_extra(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "weekly")
    await _ensure_weekly_routine(account_id)
    await _beauty_product(app_client, token, name="Weekly Exfoliant", product_type="exfoliant")
    await _generate(app_client, token, kinds=["morning", "evening", "wash_day", "weekly"])
    sunday = (await app_client.get(
        "/api/v2/routines/today?on=2026-08-16", headers=auth(token),
    )).json()
    assert sunday["hair_wash_cadence"]["status"] == "needs_anchor"
    assert "wash_day" not in {row["kind"] for row in sunday["routines"]}
    assert "weekly" in {row["kind"] for row in sunday["routines"]}


@pytest.mark.asyncio
async def test_cadence_is_an_independent_today_material_dimension(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "daily")
    generated = (await _generate(app_client, token)).json()
    shampoo = next(
        step for row in generated["routines"] if row["kind"] == "wash_day"
        for step in row["steps"] if step["slot"] == "shampoo"
    )
    url = f"/api/v2/today?plan_date={TODAY.isoformat()}"
    before_body = (await app_client.get(url, headers=auth(token))).json()
    before = await _plan_snapshot(account_id)
    assert any("Hair wash routine" in row["title"] for row in before_body["primary"] + before_body["optional_modules"])
    await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": TODAY.isoformat(), "completed": True},
    )
    after_body = (await app_client.get(url, headers=auth(token))).json()
    after = await _plan_snapshot(account_id)
    assert before["care_values"]["care_decision_fingerprint"] == after["care_values"]["care_decision_fingerprint"]
    assert before["care_values"]["care_routine_plan_fingerprint"] == after["care_values"]["care_routine_plan_fingerprint"]
    assert before["care_values"]["care_hair_wash_cadence_fingerprint"] != after["care_values"]["care_hair_wash_cadence_fingerprint"]
    assert before["cache_key"] != after["cache_key"]
    assert not any("Hair wash routine" in row["title"] for row in after_body["primary"] + after_body["optional_modules"])
    assert "Weekend hair" not in str(after_body)


@pytest.mark.asyncio
async def test_locked_today_refreshes_only_care_for_cadence_change(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _stock_wardrobe(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "daily")
    generated = (await _generate(app_client, token)).json()
    shampoo = next(
        step for row in generated["routines"] if row["kind"] == "wash_day"
        for step in row["steps"] if step["slot"] == "shampoo"
    )
    url = f"/api/v2/today?plan_date={TODAY.isoformat()}"
    await app_client.get(url, headers=auth(token))
    locked = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )
    assert locked.status_code == 200, locked.text
    locked = await app_client.post(
        f"/api/v2/planner/day/{TODAY.isoformat()}/lock", headers=auth(token),
        json={"locked": True},
    )
    assert locked.status_code == 200, locked.text
    before = await _plan_snapshot(account_id)
    await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": TODAY.isoformat(), "completed": True},
    )
    refreshed = (await app_client.get(url, headers=auth(token))).json()
    after = await _plan_snapshot(account_id)
    assert refreshed["locked"] is True
    assert after["version"] == before["version"] + 1
    assert after["cache_key"] == before["cache_key"]
    assert after["look_id"] == before["look_id"]
    assert after["schedule"] == before["schedule"]
    assert after["care_values"]["care_hair_wash_cadence_fingerprint"] != before["care_values"]["care_hair_wash_cadence_fingerprint"]
    assert after["events"] == before["events"] + 1
    assert not any("Hair wash routine" in row["title"] for row in refreshed["primary"] + refreshed["optional_modules"])
    second = await app_client.get(url, headers=auth(token))
    stable = await _plan_snapshot(account_id)
    assert second.status_code == 200
    assert stable["version"] == after["version"]
    assert stable["care_input_ids"] == after["care_input_ids"]
    assert stable["events"] == after["events"]


@pytest.mark.asyncio
async def test_locked_plan_missing_only_cadence_fingerprint_is_upgraded_in_place(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _stock_wardrobe(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "daily")
    url = f"/api/v2/today?plan_date={TODAY.isoformat()}"
    await app_client.get(url, headers=auth(token))
    locked = await app_client.post(
        "/api/v2/planner/week/generate", headers=auth(token),
        json={"week_start": TODAY.isoformat()},
    )
    assert locked.status_code == 200, locked.text
    locked = await app_client.post(
        f"/api/v2/planner/day/{TODAY.isoformat()}/lock", headers=auth(token),
        json={"locked": True},
    )
    assert locked.status_code == 200, locked.text
    before = await _plan_snapshot(account_id)
    factory = get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(DailyPlanInput).where(
                DailyPlanInput.plan_id == before["plan_id"],
                DailyPlanInput.input_type == "care",
                DailyPlanInput.input_key == "care_hair_wash_cadence_fingerprint",
            )
        )).scalar_one()
        await session.delete(row)
        await session.commit()
    current = await app_client.get(url, headers=auth(token))
    assert current.status_code == 200, current.text
    after = await _plan_snapshot(account_id)
    assert after["version"] == before["version"] + 1
    assert after["cache_key"] == before["cache_key"]
    assert after["look_id"] == before["look_id"]
    assert after["schedule"] == before["schedule"]
    assert after["care_values"].get("care_hair_wash_cadence_fingerprint")
    second = await app_client.get(url, headers=auth(token))
    stable = await _plan_snapshot(account_id)
    assert second.status_code == 200
    assert stable["version"] == after["version"]
    assert stable["events"] == after["events"]


@pytest.mark.asyncio
async def test_frequency_change_is_cadence_only_and_pattern_isolated(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "weekly")
    generated = (await _generate(app_client, token)).json()
    shampoo = next(
        step for row in generated["routines"] if row["kind"] == "wash_day"
        for step in row["steps"] if step["slot"] == "shampoo"
    )
    await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": "2026-08-14", "completed": True},
    )
    url = f"/api/v2/today?plan_date={TODAY.isoformat()}"
    await app_client.get(url, headers=auth(token))
    before = await _plan_snapshot(account_id)
    assert before["care_values"]["care_hair_wash_status"] == "not_due"
    await _profile(app_client, token, "care_hair_wash_frequency", "daily")
    await app_client.get(url, headers=auth(token))
    daily = await _plan_snapshot(account_id)
    assert daily["care_values"]["care_decision_fingerprint"] == before["care_values"]["care_decision_fingerprint"]
    assert daily["care_values"]["care_routine_plan_fingerprint"] == before["care_values"]["care_routine_plan_fingerprint"]
    assert daily["care_values"]["care_hair_wash_cadence_fingerprint"] != before["care_values"]["care_hair_wash_cadence_fingerprint"]
    assert daily["care_values"]["care_hair_wash_status"] == "due"
    await _profile(app_client, token, "care_hair_pattern", "curly")
    await app_client.get(url, headers=auth(token))
    pattern = await _plan_snapshot(account_id)
    assert pattern["care_values"]["care_hair_wash_cadence_fingerprint"] == daily["care_values"]["care_hair_wash_cadence_fingerprint"]


@pytest.mark.asyncio
async def test_hair_safety_remains_visible_when_cadence_is_not_due(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, _ = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "weekly")
    generated = (await _generate(app_client, token)).json()
    shampoo = next(
        step for row in generated["routines"] if row["kind"] == "wash_day"
        for step in row["steps"] if step["slot"] == "shampoo"
    )
    await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": "2026-08-15", "completed": True},
    )
    expired_id = await _hair_product(
        app_client, token, name="Expired Hair Shampoo", expiry=date(2026, 8, 1),
    )
    today = (await app_client.get(
        f"/api/v2/today?plan_date={TODAY.isoformat()}", headers=auth(token),
    )).json()
    actions = today["primary"] + today["optional_modules"]
    assert today["hair_wash_cadence"]["status"] == "not_due"
    assert not any("Hair wash routine" in row["title"] for row in actions)
    assert any(
        row["action_type"] == "care_safety" and row.get("inventory_item_id") == expired_id
        for row in actions
    )


@pytest.mark.asyncio
async def test_wash_completion_preserves_inventory_usage_and_unmark_removes_anchor(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda _: "morning")
    token, account_id = await registered_supabase_user()
    await _seeded_shelf(app_client, token)
    await _profile(app_client, token, "care_hair_wash_frequency", "weekly")
    generated = (await _generate(app_client, token)).json()
    wash = next(row for row in generated["routines"] if row["kind"] == "wash_day")
    shampoo = next(step for step in wash["steps"] if step["slot"] == "shampoo")
    shampoo_item_id = shampoo["inventory_item_id"]
    factory = get_sessionmaker()
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(shampoo_item_id))
        assert item is not None
        before_usage = (item.usage_count, item.last_used_at)
    complete = await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": TODAY.isoformat(), "completed": True},
    )
    assert complete.status_code == 200, complete.text
    async with factory() as session:
        item = await session.get(InventoryItem, uuid.UUID(shampoo_item_id))
        assert item is not None
        after_usage = (item.usage_count, item.last_used_at)
        anchored = await adherence.last_completed_wash_on(
            session, account_id=account_id, through=TODAY,
        )
    assert after_usage == before_usage
    assert anchored == TODAY
    unmark = await app_client.post(
        f"/api/v2/routines/steps/{shampoo['id']}/complete", headers=auth(token),
        json={"done_on": TODAY.isoformat(), "completed": False},
    )
    assert unmark.status_code == 200, unmark.text
    async with factory() as session:
        removed = await adherence.last_completed_wash_on(
            session, account_id=account_id, through=TODAY,
        )
    assert removed is None
    today = (await app_client.get(
        f"/api/v2/routines/today?on={TODAY.isoformat()}", headers=auth(token),
    )).json()
    assert today["hair_wash_cadence"]["status"] == "needs_anchor"
