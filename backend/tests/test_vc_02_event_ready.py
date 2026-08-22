"""Pure contract checks for the VC-02 Event Ready foundation."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.domains.care.decisions import CareDecisionAuthority, CareDecisionReason, CareDecisionReasonCode
from app.domains.inventory.models import InventoryItem
from app.domains.planning.event_ready import (
    EVENT_READY_VERSION,
    _actions,
    _context_payload,
    _event_payload,
    _sha,
    _status,
)
from app.domains.planning.models import EventReadyAction, EventReadyPlan
from app.domains.planning.schemas import EventReadyActionComplete, EventReadyLookPatch
from app.domains.recommendation.models import (
    Look,
    LookItem,
    PurchaseDecision,
    PurchaseEvaluation,
    RecommendationRun,
    ShoppingCandidate,
    StyleRequest,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth


def _event(**overrides):
    values = {
        "id": uuid4(), "title": "Wedding", "starts_at": datetime(2030, 1, 12, 12, tzinfo=UTC),
        "ends_at": None, "all_day": False, "location": "Hall", "occasion_key": "wedding", "dress_code_hint": None,
        "user_confirmed": True, "provider": "manual", "source": "user_declared", "status": "active", "inference_confidence": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_event_ready_version_and_fingerprint_are_stable():
    event = _event()
    first = _event_payload(event, "Asia/Kolkata")
    assert EVENT_READY_VERSION == "vc-02-v1"
    assert _sha(first) == _sha(dict(first))
    assert _sha(first) == _sha(first)


def test_unconfirmed_event_needs_confirmation_without_promoting_inference():
    event = _event(user_confirmed=False)
    day = SimpleNamespace(timezone_name="Asia/Kolkata", now_local=datetime(2030, 1, 1, tzinfo=UTC))
    assert _status(event, day) == "needs_confirmation"


def test_context_payload_sorts_unavailable_items():
    day = SimpleNamespace(
        unavailable_item_ids=[uuid4(), uuid4()],
        weather=None,
        air_quality=None,
    )
    payload = _context_payload(day)
    assert payload["weather"] is None
    assert payload["air_quality"] is None
    assert payload["unavailable_item_ids"] == sorted(payload["unavailable_item_ids"])


def test_mutation_contracts_forbid_account_injection_and_preserve_clear_values():
    assert EventReadyLookPatch(look_id=None).look_id is None
    assert EventReadyActionComplete(completed=False).completed is False
    for model, values in ((EventReadyLookPatch, {"account_id": "wrong"}), (EventReadyActionComplete, {"account_id": "wrong"})):
        try:
            model(**values)
        except Exception:
            pass
        else:
            raise AssertionError("Event Ready mutation accepted account_id")


def test_past_unconfirmed_event_is_past_not_confirmation_action():
    event = _event(starts_at=datetime(2020, 1, 12, 12, tzinfo=UTC), user_confirmed=False)
    day = SimpleNamespace(timezone_name="Asia/Kolkata", now_local=datetime(2020, 1, 13, tzinfo=UTC))
    assert _status(event, day) == "past"
    material = SimpleNamespace(
        hair_wash_cadence=SimpleNamespace(status=SimpleNamespace(value="not_due")),
        decisions=SimpleNamespace(product_decisions=[]),
    )
    assert _actions(event, day, material, None, [], "past") == []


def test_paused_product_is_not_reported_as_care_safety_but_expiry_is():
    event = _event()
    day = SimpleNamespace(unavailable_item_ids=[], missing_information=[])
    cadence = SimpleNamespace(status=SimpleNamespace(value="not_due"), as_payload=dict)
    paused = SimpleNamespace(
        item_id=uuid4(),
        blocking_reasons=[CareDecisionReason(CareDecisionReasonCode.USER_PAUSED_FOR_ROUTINE, CareDecisionAuthority.USER_CONSTRAINT)],
    )
    material = SimpleNamespace(hair_wash_cadence=cadence, decisions=SimpleNamespace(product_decisions=[paused]))
    assert not any(row["action_key"] == "care:attention" for row in _actions(event, day, material, None, [], "preparing"))
    expired = SimpleNamespace(
        item_id=uuid4(),
        blocking_reasons=[CareDecisionReason(CareDecisionReasonCode.PRODUCT_EXPIRED, CareDecisionAuthority.SYSTEM_POLICY)],
    )
    material.decisions.product_decisions = [expired]
    safety = [row for row in _actions(event, day, material, None, [], "preparing") if row["action_key"] == "care:attention"]
    assert len(safety) == 1
    assert "product_expired" in safety[0]["material"]["reason_codes"]


@pytest.mark.asyncio
async def test_event_ready_get_before_generation_is_non_mutating_and_generated_get_has_care(
    app_client, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    response = await app_client.post(
        "/api/v2/today/events", headers=auth(token),
        json={"title": "Future wedding", "starts_at": "2030-01-12T12:00:00+05:30", "occasion_key": "wedding"},
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json()["event"]["id"]
    factory = get_sessionmaker()
    async with factory() as session:
        before = await session.scalar(select(func.count()).select_from(EventReadyPlan).where(EventReadyPlan.account_id == account_id))
        before_actions = await session.scalar(select(func.count()).select_from(EventReadyAction).join(EventReadyPlan).where(EventReadyPlan.account_id == account_id))
    assert before == 0
    assert before_actions == 0

    first = await app_client.get(f"/api/v2/planner/events/{event_id}/ready", headers=auth(token))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "not_generated"
    assert first.json()["timeline"] == []
    assert first.json()["care"] is None

    async with factory() as session:
        after_get = await session.scalar(select(func.count()).select_from(EventReadyPlan).where(EventReadyPlan.account_id == account_id))
    assert after_get == 0

    boundary_models = {
        "style_requests": StyleRequest,
        "recommendation_runs": RecommendationRun,
        "looks": Look,
        "shopping_candidates": ShoppingCandidate,
        "purchase_evaluations": PurchaseEvaluation,
        "purchase_decisions": PurchaseDecision,
        "inventory_items": InventoryItem,
    }
    async with factory() as session:
        boundary_before = {
            key: await session.scalar(select(func.count()).select_from(model).where(model.account_id == account_id))
            for key, model in boundary_models.items()
        }
        boundary_before["look_items"] = await session.scalar(
            select(func.count()).select_from(LookItem).join(Look).where(Look.account_id == account_id)
        )

    generated = await app_client.post(f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token))
    assert generated.status_code == 200, generated.text
    assert generated.json()["status"] == "preparing"
    assert [row["action_key"] for row in generated.json()["timeline"]] == ["style:choose_event_look"]
    assert generated.json()["care"] is not None
    async with factory() as session:
        boundary_after = {
            key: await session.scalar(select(func.count()).select_from(model).where(model.account_id == account_id))
            for key, model in boundary_models.items()
        }
        boundary_after["look_items"] = await session.scalar(
            select(func.count()).select_from(LookItem).join(Look).where(Look.account_id == account_id)
        )
    assert boundary_after == boundary_before
    care = generated.json()["care"]
    action_id = generated.json()["timeline"][0]["id"]
    async with factory() as session:
        plan = (await session.execute(select(EventReadyPlan).where(EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id == event_id))).scalar_one()
        action = (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == plan.id))).scalar_one()
        plan_id, action_material = plan.id, action.material_fingerprint
        assert str(action.id) == action_id

    second = await app_client.post(f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token))
    assert second.status_code == 200, second.text
    assert second.json()["timeline"][0]["id"] == action_id
    async with factory() as session:
        same_plan = (await session.execute(select(EventReadyPlan).where(EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id == event_id))).scalar_one()
        same_action = (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == same_plan.id))).scalar_one()
        assert same_plan.id == plan_id
        assert same_action.material_fingerprint == action_material
        assert await session.scalar(select(func.count()).select_from(EventReadyAction).where(EventReadyAction.event_ready_plan_id == same_plan.id)) == 1

    completed = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/actions/{action_id}/complete",
        headers=auth(token), json={"completed": True},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["care"]["decision_fingerprint"] == care["decision_fingerprint"]
    assert completed.json()["care"]["routine_plan_fingerprint"] == care["routine_plan_fingerprint"]
    assert completed.json()["timeline"][0]["completed"] is True
    completed_at = completed.json()["timeline"][0]["completed_at"]
    assert completed_at is not None
    repeated = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/actions/{action_id}/complete",
        headers=auth(token), json={"completed": True},
    )
    assert repeated.status_code == 200
    assert repeated.json()["timeline"][0]["completed_at"] == completed_at
    undone = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/actions/{action_id}/complete",
        headers=auth(token), json={"completed": False},
    )
    assert undone.status_code == 200
    assert undone.json()["care"] is not None
    assert undone.json()["timeline"][0]["completed"] is False
    assert undone.json()["timeline"][0]["completed_at"] is None
    undone_again = await app_client.post(
        f"/api/v2/planner/events/{event_id}/ready/actions/{action_id}/complete",
        headers=auth(token), json={"completed": False},
    )
    assert undone_again.status_code == 200
    assert undone_again.json()["timeline"][0]["completed_at"] is None

    async with factory() as session:
        plan = (await session.execute(select(EventReadyPlan).where(EventReadyPlan.id == plan_id))).scalar_one()
        action = (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == plan.id))).scalar_one()
        await session.delete(action)
        await session.commit()
    repaired = await app_client.post(f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token))
    assert repaired.status_code == 200, repaired.text
    assert [row["action_key"] for row in repaired.json()["timeline"]] == ["style:choose_event_look"]

    read = await app_client.get(f"/api/v2/planner/events/{event_id}/ready", headers=auth(token))
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "preparing"
    assert read.json()["care"]["decision_fingerprint"] == care["decision_fingerprint"]
    assert read.json()["care"]["routine_plan_fingerprint"] == care["routine_plan_fingerprint"]
    assert read.json()["care"]["hair_wash"]["fingerprint"] == care["hair_wash"]["fingerprint"]

    async with factory() as session:
        after_generate = await session.scalar(select(func.count()).select_from(EventReadyPlan).where(EventReadyPlan.account_id == account_id))
    assert after_generate == 1


@pytest.mark.asyncio
async def test_event_ready_targets_requested_event_and_is_tenant_scoped(
    app_client, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    other_token, _ = await registered_supabase_user()
    headers = auth(token)
    same_day = "2030-02-14T12:00:00+05:30"
    office = await app_client.post(
        "/api/v2/today/events", headers=headers,
        json={"title": "Office", "starts_at": same_day, "occasion_key": "office"},
    )
    wedding = await app_client.post(
        "/api/v2/today/events", headers=headers,
        json={"title": "Wedding", "starts_at": same_day, "occasion_key": "wedding"},
    )
    assert office.status_code in (200, 201), office.text
    assert wedding.status_code in (200, 201), wedding.text
    office_id = office.json()["event"]["id"]
    wedding_id = wedding.json()["event"]["id"]

    generated_office = await app_client.post(f"/api/v2/planner/events/{office_id}/ready/generate", headers=headers)
    generated_wedding = await app_client.post(f"/api/v2/planner/events/{wedding_id}/ready/generate", headers=headers)
    assert generated_office.status_code == 200, generated_office.text
    assert generated_wedding.status_code == 200, generated_wedding.text
    assert generated_office.json()["event"]["id"] == office_id
    assert generated_office.json()["event"]["occasion_key"] == "office"
    assert generated_wedding.json()["event"]["id"] == wedding_id
    assert generated_wedding.json()["event"]["occasion_key"] == "wedding"

    assert (await app_client.get(f"/api/v2/planner/events/{office_id}/ready", headers=auth(other_token))).status_code == 404
    assert (await app_client.post(f"/api/v2/planner/events/{office_id}/ready/generate", headers=auth(other_token))).status_code == 404


@pytest.mark.asyncio
async def test_event_ready_rejects_unconfirmed_non_null_look_and_allows_clear(
    app_client, registered_supabase_user,
):
    token, _ = await registered_supabase_user()
    response = await app_client.post(
        "/api/v2/today/events", headers=auth(token),
        json={"title": "Unconfirmed commitment", "starts_at": "2030-04-20T12:00:00+05:30"},
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json()["event"]["id"]
    rejected = await app_client.patch(
        f"/api/v2/planner/events/{event_id}/ready/look", headers=auth(token),
        json={"look_id": str(uuid4())},
    )
    assert rejected.status_code in (400, 422)

    generated = await app_client.post(f"/api/v2/planner/events/{event_id}/ready/generate", headers=auth(token))
    assert generated.status_code == 200, generated.text
    cleared = await app_client.patch(
        f"/api/v2/planner/events/{event_id}/ready/look", headers=auth(token),
        json={"look_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["style"]["selected_look"] is None


@pytest.mark.asyncio
async def test_event_ready_concurrent_generation_has_one_deterministic_plan_and_action(
    app_client, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    response = await app_client.post(
        "/api/v2/today/events", headers=auth(token),
        json={"title": "Concurrent wedding", "starts_at": "2030-03-20T12:00:00+05:30", "occasion_key": "wedding"},
    )
    assert response.status_code in (200, 201), response.text
    event_id = response.json()["event"]["id"]
    factory = get_sessionmaker()

    # Both transactions target the same persisted event and therefore exercise
    # the database uniqueness constraint plus the EventReadyPlan row lock.
    from app.domains.planning import event_ready
    async def generate_once():
        async with factory() as session:
            result = await event_ready.generate(session, account_id, event_id)
            await session.commit()
            return result

    results = await asyncio.gather(generate_once(), generate_once())
    assert all(result["status"] == "preparing" for result in results)
    async with factory() as session:
        plans = await session.scalar(select(func.count()).select_from(EventReadyPlan).where(EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id == event_id))
        plan = (await session.execute(select(EventReadyPlan).where(EventReadyPlan.account_id == account_id, EventReadyPlan.calendar_event_id == event_id))).scalar_one()
        actions = (await session.execute(select(EventReadyAction).where(EventReadyAction.event_ready_plan_id == plan.id))).scalars().all()
        assert plans == 1
        assert [row.action_key for row in actions] == ["style:choose_event_look"]
        assert len({row.action_key for row in actions}) == len(actions)
        assert results[0]["event_ready_fingerprint"] == results[1]["event_ready_fingerprint"] == plan.input_fingerprint
