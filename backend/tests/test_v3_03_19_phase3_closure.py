"""V3-03.19 end-to-end closure tests for the frozen Skin + Hair system."""
from __future__ import annotations

import uuid
from datetime import date
from inspect import getsource

import pytest
from app.bootstrap import run as run_seed
from app.domains.care.guidance import CARE_GUIDANCE_VERSION
from app.domains.care.guidance_rules import CARE_GUIDANCE_RULESET_VERSION, GUIDANCE_RULES
from app.domains.care.home_care_rules import HOME_CARE_RULES, HOME_CARE_RULESET_VERSION
from app.domains.inventory.models import BeautyProductDetail, InventoryItem
from app.domains.planning import clock
from app.domains.planning.models import DailyPlan, WeatherSnapshot
from app.domains.routines.models import Routine, RoutineAdherence, RoutineRecommendationRun, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio

CLOSURE_DATE = date(2026, 8, 12)
GUIDANCE_IDS = {rule.rule_id for rule in GUIDANCE_RULES}
HOME_CARE_IDS = {rule.rule_id for rule in HOME_CARE_RULES}


async def _seed(client) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()


async def _profile(client, token: str, **values: str) -> None:
    response = await client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": key, "value": value} for key, value in values.items()]},
    )
    assert response.status_code == 200, response.text


async def _product(
    client,
    token: str,
    *,
    name: str,
    category: str,
    product_type: str,
    expiry: date | None = None,
) -> str:
    details: dict[str, object] = {"product_type": product_type}
    if expiry is not None:
        details["expiry_date"] = expiry.isoformat()
    response = await client.post(
        "/api/v2/inventory/items", headers=auth(token),
        json={
            "category": category,
            "display_name": name,
            "subcategory": product_type,
            "details": details,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


async def _weather(account_id: uuid.UUID, *, uv: float, humidity: int) -> uuid.UUID:
    factory = get_sessionmaker()
    async with factory() as session:
        row = WeatherSnapshot(
            account_id=account_id,
            for_date=CLOSURE_DATE,
            location="Delhi",
            condition="clear",
            temp_min_c=24.0,
            temp_max_c=34.0,
            precipitation_chance=0,
            humidity=humidity,
            uv_index=uv,
            provider="manual",
            source="v3-03.19-test",
            raw={"closure": True, "uv": uv, "humidity": humidity},
        )
        session.add(row)
        await session.commit()
        return row.id


async def _generate(client, token: str, *, explain: bool = False) -> dict:
    response = await client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"as_of": CLOSURE_DATE.isoformat(), "explain": explain},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _golden_scenario(client, registered_supabase_user) -> dict[str, object]:
    token, account_id = await registered_supabase_user()
    await _seed(client)
    await _profile(
        client,
        token,
        care_skin_usual_feel="often_dry_or_tight",
        care_heat_styling_frequency="frequent",
        care_hair_wash_frequency="daily",
        care_routine_effort="detailed",
    )
    await _weather(account_id, uv=5.0, humidity=20)
    products = {
        "cleanser": await _product(client, token, name="Owned Cleanser", category="beauty", product_type="cleanser"),
        "moisturiser_a": await _product(client, token, name="Owned Moisturiser A", category="beauty", product_type="moisturiser"),
        "moisturiser_b": await _product(client, token, name="Owned Moisturiser B", category="beauty", product_type="moisturiser"),
        "expired_moisturiser": await _product(client, token, name="Expired Moisturiser", category="beauty", product_type="moisturiser", expiry=date(2026, 8, 1)),
        "sunscreen": await _product(client, token, name="Owned Sunscreen", category="beauty", product_type="sunscreen"),
        "treatment": await _product(client, token, name="Owned Serum", category="beauty", product_type="serum"),
        "shampoo": await _product(client, token, name="Owned Shampoo", category="hair", product_type="shampoo"),
        "conditioner": await _product(client, token, name="Owned Conditioner", category="hair", product_type="conditioner"),
        "heat_protectant": await _product(client, token, name="Owned Heat Protectant", category="hair", product_type="heat_protectant"),
    }
    body = await _generate(client, token)
    return {"token": token, "account_id": account_id, "products": products, "body": body}


def _all_steps(body: dict) -> list[dict]:
    return [step for routine in body["routines"] for step in routine["steps"]]


def _routine(body: dict, kind: str) -> dict:
    return next(row for row in body["routines"] if row["kind"] == kind)


def _selected_ids(body: dict) -> set[str]:
    return {step["inventory_item_id"] for step in _all_steps(body) if step["inventory_item_id"]}


async def _latest_run(account_id: uuid.UUID) -> RoutineRecommendationRun:
    factory = get_sessionmaker()
    async with factory() as session:
        return (await session.execute(
            select(RoutineRecommendationRun)
            .where(RoutineRecommendationRun.account_id == account_id)
            .order_by(RoutineRecommendationRun.created_at.desc())
            .limit(1)
        )).scalar_one()


async def _routine_material(account_id: uuid.UUID) -> tuple[tuple, ...]:
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(Routine.kind, RoutineStep.slot, RoutineStep.inventory_item_id, RoutineStep.id)
            .join(RoutineStep, RoutineStep.routine_id == Routine.id)
            .where(Routine.account_id == account_id, Routine.status == "active")
            .order_by(Routine.kind, RoutineStep.position)
        )).all()
    return tuple((kind, slot, str(item_id) if item_id else None, str(step_id)) for kind, slot, item_id, step_id in rows)


def _routine_structure(body: dict) -> tuple[tuple, ...]:
    return tuple(sorted(
        (
            routine["kind"], step["slot"], step["inventory_item_id"],
            step["is_gap"], step["required"],
        )
        for routine in body["routines"] for step in routine["steps"]
    ))


async def _adherence_material(account_id: uuid.UUID) -> tuple[tuple, ...]:
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(
                RoutineAdherence.routine_id, RoutineAdherence.slot,
                RoutineAdherence.done_on, RoutineAdherence.completed,
                RoutineAdherence.step_id,
            )
            .where(RoutineAdherence.account_id == account_id)
            .order_by(RoutineAdherence.done_on, RoutineAdherence.slot)
        )).all()
    return tuple((str(routine_id), slot, done_on.isoformat(), completed, str(step_id) if step_id else None) for routine_id, slot, done_on, completed, step_id in rows)


@pytest.mark.asyncio
async def test_phase3_closure_golden_path_is_owned_first_safe_contextual_and_auditable(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id, products, body = scenario["token"], scenario["account_id"], scenario["products"], scenario["body"]
    owned_ids = set(products.values()) - {products["expired_moisturiser"]}

    blocked = next(row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == products["expired_moisturiser"])
    assert blocked["reasons"] == ["product_expired"]
    assert products["expired_moisturiser"] not in _selected_ids(body)
    assert _selected_ids(body) <= owned_ids
    assert products["moisturiser_a"] in _selected_ids(body) or products["moisturiser_b"] in _selected_ids(body)

    morning = _routine(body, "morning")
    wash_day = _routine(body, "wash_day")
    for slot in ("cleanser", "moisturiser", "sunscreen"):
        assert next(step for step in morning["steps"] if step["slot"] == slot)["inventory_item_id"] in owned_ids
    for slot in ("shampoo", "conditioner"):
        assert next(step for step in wash_day["steps"] if step["slot"] == slot)["inventory_item_id"] in owned_ids
    assert next(step for step in morning["steps"] if step["slot"] == "treatment")["inventory_item_id"] == products["treatment"]
    assert next(step for step in wash_day["steps"] if step["slot"] == "heat_protectant")["inventory_item_id"] == products["heat_protectant"]

    guidance = body["care_guidance"]
    assert guidance["guidance_version"] == CARE_GUIDANCE_VERSION == "v3-03.17"
    assert guidance["ruleset_version"] == CARE_GUIDANCE_RULESET_VERSION == "v3-03.17-r1"
    assert {item["rule_id"] for item in guidance["items"]} == GUIDANCE_IDS
    assert all(item["evidence_claim_ids"] for item in guidance["items"])
    home_care = body["home_care"]
    assert home_care["home_care_version"] == "v3-03.18"
    assert home_care["ruleset_version"] == HOME_CARE_RULESET_VERSION
    assert {item["rule_id"] for item in home_care["items"]} == HOME_CARE_IDS
    assert all(item["evidence_claim_ids"] for item in home_care["items"])
    assert not any("recipe" in str(item).lower() for item in home_care["items"])
    for item in (*guidance["items"], *home_care["items"]):
        visible = f"{item['title']} {item['body']}".lower()
        assert not any(term in visible for term in ("diagnos", "prescription", "medication", "buy", "shop", "add product", "http://", "https://"))
        assert not any(str(claim_id) in visible for claim_id in item["evidence_claim_ids"])

    run = await _latest_run(account_id)
    snapshot = run.inputs["care_snapshot"]
    assert run.engine_version == "care-v3-03.5"
    assert snapshot["snapshot_version"] == "v3-03.18"
    for key in (
        "care_context_version", "care_decision_version", "care_routine_plan_version",
        "care_routine_plan_fingerprint", "care_routine_effort", "care_guidance_version",
        "care_guidance_ruleset_version", "care_guidance_fingerprint", "care_guidance_item_count",
        "hair_wash_cadence_version", "hair_wash_cadence_fingerprint", "hair_wash_cadence_status",
        "home_care_version", "home_care_ruleset_version", "home_care_fingerprint", "home_care_item_count",
    ):
        assert key in run.inputs
    assert run.inputs["care_routine_effort"] == "detailed"
    assert run.inputs["care_routine_effort_source"] == "user_declared"
    assert run.inputs["hair_wash_cadence_version"] == "v3-03.8"
    assert run.inputs["hair_wash_cadence_status"] == "due"
    assert snapshot["environment"]["uv_index"] >= 3
    assert snapshot["environment"]["moisture_regime"] == "dry"
    assert snapshot["environment"]["weather_snapshot_id"] == run.inputs["weather_snapshot_id"]
    assert snapshot["routine_plan"]["effort_source"] == "user_declared"
    assert snapshot["hair_wash_cadence"]["version"] == "v3-03.8"
    assert snapshot["hair_wash_cadence"]["status"] == "due"
    assert snapshot["routine_plan"]["routine_plan_fingerprint"] == run.inputs["care_routine_plan_fingerprint"]
    assert snapshot["care_guidance"]["fingerprint"] == run.inputs["care_guidance_fingerprint"]
    assert snapshot["home_care"]["fingerprint"] == run.inputs["home_care_fingerprint"]
    assert {step["inventory_item_id"] for routine in snapshot["rendered_routines"] for step in routine["steps"] if step["inventory_item_id"]} == _selected_ids(body)
    assert token and account_id


@pytest.mark.asyncio
async def test_phase3_closure_user_preference_never_overrides_safety(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "local_today", lambda *_args, **_kwargs: CLOSURE_DATE)
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id, products, initial = scenario["token"], scenario["account_id"], scenario["products"], scenario["body"]
    before = (await _latest_run(account_id)).inputs["care_snapshot"]
    initial_selected = next(step["inventory_item_id"] for step in _routine(initial, "morning")["steps"] if step["slot"] == "moisturiser")
    preferred = products["moisturiser_b"] if initial_selected == products["moisturiser_a"] else products["moisturiser_a"]

    response = await app_client.post(f"/api/v2/routines/products/{preferred}/prefer", headers=auth(token))
    assert response.status_code == 200, response.text
    preferred_snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    preferred_slot = next(row for row in preferred_snapshot["routine_plan"]["slots"] if row["slot"] == "moisturiser")
    assert preferred_slot["selected_item_id"] == preferred
    assert preferred_slot["selection_basis"] == "user_preferred"
    assert preferred_snapshot["decisions"]["decision_fingerprint"] == before["decisions"]["decision_fingerprint"]
    assert next(row for row in preferred_snapshot["decisions"]["product_decisions"] if row["item_id"] == preferred)["eligible"] is True

    paused = await app_client.post(f"/api/v2/routines/products/{preferred}/pause", headers=auth(token))
    assert paused.status_code == 200, paused.text
    paused_snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    paused_slot = next(row for row in paused_snapshot["routine_plan"]["slots"] if row["slot"] == "moisturiser")
    assert paused_slot["selected_item_id"] == initial_selected
    assert preferred not in {row["selected_item_id"] for row in paused_snapshot["routine_plan"]["slots"]}
    assert any(row["item_id"] == preferred and not row["eligible"] for row in paused_snapshot["decisions"]["product_decisions"])

    resumed = await app_client.post(f"/api/v2/routines/products/{preferred}/resume", headers=auth(token))
    assert resumed.status_code == 200, resumed.text
    resumed_snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    resumed_slot = next(row for row in resumed_snapshot["routine_plan"]["slots"] if row["slot"] == "moisturiser")
    assert resumed_slot["selected_item_id"] == preferred
    assert resumed_slot["selection_basis"] == "user_preferred"
    assert products["expired_moisturiser"] not in {
        row["selected_item_id"] for row in resumed_snapshot["routine_plan"]["slots"]
    }


@pytest.mark.asyncio
async def test_phase3_closure_behavioral_signals_do_not_silently_change_care(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id, products = scenario["token"], scenario["account_id"], scenario["products"]
    before_snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    factory = get_sessionmaker()
    async with factory() as session:
        step = (await session.execute(
            select(RoutineStep).join(Routine).where(
                Routine.account_id == account_id, Routine.kind == "morning", RoutineStep.slot == "treatment",
            )
        )).scalar_one()
        step_id = str(step.id)

    observation = await app_client.post(
        "/api/v2/routines/observations", headers=auth(token),
        json={"observed_on": CLOSURE_DATE.isoformat(), "area": "skin", "item_id": products["treatment"], "note": "This toner feels heavy."},
    )
    assert observation.status_code in (200, 201), observation.text
    feedback = await app_client.post(
        "/api/v2/routines/experience-feedback", headers=auth(token),
        json={"subject_type": "routine_step", "subject_id": step_id, "dimension": "comfort", "sentiment": "negative", "note": "Too heavy."},
    )
    assert feedback.status_code in (200, 201), feedback.text
    for day in (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)):
        skipped = await app_client.post(
            f"/api/v2/routines/steps/{step_id}/complete", headers=auth(token),
            json={"done_on": day.isoformat(), "completed": False},
        )
        assert skipped.status_code == 200, skipped.text

    after = await _generate(app_client, token)
    after_snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    assert after_snapshot["decisions"]["decision_fingerprint"] == before_snapshot["decisions"]["decision_fingerprint"]
    assert after_snapshot["routine_plan"]["routine_plan_fingerprint"] == before_snapshot["routine_plan"]["routine_plan_fingerprint"]
    assert after_snapshot["routine_plan"]["resolved_effort"] == before_snapshot["routine_plan"]["resolved_effort"] == "detailed"
    assert after_snapshot["product_preferences"] == before_snapshot["product_preferences"]
    assert _selected_ids(after) == {
        step["inventory_item_id"] for routine in before_snapshot["rendered_routines"] for step in routine["steps"] if step["inventory_item_id"]
    }
    async with factory() as session:
        adherence = (await session.execute(
            select(RoutineAdherence).where(RoutineAdherence.account_id == account_id, RoutineAdherence.slot == "treatment")
        )).scalars().all()
    assert len(adherence) == 3 and all(row.completed is False for row in adherence)


@pytest.mark.asyncio
async def test_phase3_closure_simplification_requires_explicit_user_action(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "local_today", lambda *_args, **_kwargs: CLOSURE_DATE)
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id, products = scenario["token"], scenario["account_id"], scenario["products"]
    before = (await _latest_run(account_id)).inputs["care_snapshot"]
    assert products["treatment"] in _selected_ids(scenario["body"])
    response = await app_client.post("/api/v2/routines/simplify", headers=auth(token))
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert response.json()["previous_effort"] == "detailed"
    assert response.json()["new_effort"] == "balanced"
    after = (await _latest_run(account_id)).inputs["care_snapshot"]
    assert after["routine_plan"]["resolved_effort"] == "balanced"
    assert after["decisions"]["decision_fingerprint"] == before["decisions"]["decision_fingerprint"]
    assert after["routine_plan"]["routine_plan_fingerprint"] != before["routine_plan"]["routine_plan_fingerprint"]
    assert products["treatment"] not in {
        step["inventory_item_id"] for routine in after["rendered_routines"] for step in routine["steps"]
    }
    assert all(
        step["inventory_item_id"] in set(products.values())
        for routine in after["rendered_routines"] for step in routine["steps"] if step["inventory_item_id"]
    )
    assert {slot["slot"] for slot in after["routine_plan"]["slots"] if slot["required"] and slot["active"]} >= {"cleanser", "moisturiser", "sunscreen", "shampoo", "conditioner"}


@pytest.mark.asyncio
async def test_phase3_closure_today_refreshes_contextual_advice_without_mutating_plan(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda *_args, **_kwargs: "morning")
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id = scenario["token"], scenario["account_id"]
    before_material = await _routine_material(account_id)
    before_adherence = await _adherence_material(account_id)
    factory = get_sessionmaker()
    async with factory() as session:
        daily_before = await session.scalar(select(func.count()).select_from(DailyPlan).where(DailyPlan.account_id == account_id))
    await _weather(account_id, uv=1.0, humidity=50)
    response = await app_client.get(f"/api/v2/routines/today?on={CLOSURE_DATE.isoformat()}", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    guidance_ids = {item["rule_id"] for item in body["care_guidance"]["items"]}
    home_ids = {item["rule_id"] for item in body["home_care"]["items"]}
    assert guidance_ids == {"care.hair.frequent_heat_styling_protection"}
    assert home_ids == {"care.home.hair_gentle_drying"}
    assert "care.skin.uv_protection_uvi_3" not in guidance_ids
    assert "care.skin.dry_air_moisture_support" not in guidance_ids
    assert "care.home.skin_gentle_bathing" not in home_ids
    assert response.json()["refresh_required"] is False
    assert await _routine_material(account_id) == before_material
    assert await _adherence_material(account_id) == before_adherence
    async with factory() as session:
        daily_after = await session.scalar(select(func.count()).select_from(DailyPlan).where(DailyPlan.account_id == account_id))
    assert daily_after == daily_before


@pytest.mark.asyncio
async def test_phase3_closure_today_fails_closed_on_new_safety_drift(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    monkeypatch.setattr(clock, "part_of_day", lambda *_args, **_kwargs: "morning")
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id, products = scenario["token"], scenario["account_id"], scenario["products"]
    selected = next(
        step["inventory_item_id"] for step in _routine(scenario["body"], "morning")["steps"]
        if step["slot"] == "moisturiser"
    )
    factory = get_sessionmaker()
    async with factory() as session:
        detail = (await session.execute(
            select(BeautyProductDetail).join(InventoryItem).where(InventoryItem.id == uuid.UUID(selected))
        )).scalar_one()
        detail.expiry_date = date(2026, 8, 1)
        routine = (await session.execute(
            select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning")
        )).scalar_one()
        before_version = routine.version
        await session.commit()
    before_steps = await _routine_material(account_id)
    response = await app_client.get(f"/api/v2/routines/today?on={CLOSURE_DATE.isoformat()}", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refresh_required"] is True
    assert "morning" in body["refresh_required_kinds"]
    assert selected not in _selected_ids(body)
    blocked = next(row for row in body["care_safety"]["blocked_products"] if row["inventory_item_id"] == selected)
    assert "product_expired" in blocked["reasons"]
    assert await _routine_material(account_id) == before_steps
    async with factory() as session:
        current = await session.scalar(select(Routine).where(Routine.account_id == account_id, Routine.kind == "morning"))
    assert current.version == before_version
    assert products["expired_moisturiser"] not in _selected_ids(body)


@pytest.mark.asyncio
async def test_phase3_closure_ai_explanation_cannot_change_deterministic_care(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    scenario = await _golden_scenario(app_client, registered_supabase_user)
    token, account_id = scenario["token"], scenario["account_id"]
    deterministic = scenario["body"]
    before = (await _latest_run(account_id)).inputs["care_snapshot"]
    fake_provider.text = (
        '{"routines":[{"kind":"morning",'
        '"summary":"Your morning routine uses the products already selected for you.",'
        '"step_notes":{"cleanser":"Start with your selected cleanser."}}]}'
    )
    explained = await _generate(app_client, token, explain=True)
    after = (await _latest_run(account_id)).inputs["care_snapshot"]
    assert explained["explanation_source"] == "ai_validated"
    morning_explained = _routine(explained, "morning")
    assert morning_explained["summary"] == "Your morning routine uses the products already selected for you."
    assert next(step for step in morning_explained["steps"] if step["slot"] == "cleanser")["plain_english"] == "Start with your selected cleanser."
    assert after["decisions"]["decision_fingerprint"] == before["decisions"]["decision_fingerprint"]
    assert after["routine_plan"]["routine_plan_fingerprint"] == before["routine_plan"]["routine_plan_fingerprint"]
    assert after["hair_wash_cadence"]["fingerprint"] == before["hair_wash_cadence"]["fingerprint"]
    assert after["care_guidance"]["fingerprint"] == before["care_guidance"]["fingerprint"]
    assert after["home_care"]["fingerprint"] == before["home_care"]["fingerprint"]
    assert _routine_structure(explained) == _routine_structure(deterministic)
    fake_provider.raises = RuntimeError("provider unavailable")
    fallback = await _generate(app_client, token, explain=True)
    assert fallback["explanation_source"] == "deterministic"
    assert _routine_structure(fallback) == _routine_structure(deterministic)


@pytest.mark.asyncio
async def test_phase3_closure_privacy_export_is_account_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _seed(app_client)
    item_a = await _product(app_client, token_a, name="A cleanser", category="beauty", product_type="cleanser")
    item_b = await _product(app_client, token_b, name="B cleanser", category="beauty", product_type="cleanser")
    await _generate(app_client, token_a)
    await _generate(app_client, token_b)
    note = "private account B closure observation"
    observation = await app_client.post(
        "/api/v2/routines/observations", headers=auth(token_b),
        json={"observed_on": CLOSURE_DATE.isoformat(), "area": "skin", "item_id": item_b, "note": note},
    )
    assert observation.status_code in (200, 201), observation.text
    exported = (await app_client.get("/api/v2/privacy/export", headers=auth(token_a))).json()
    assert exported["domains"]["identity"]["id"] == str(account_a)
    assert note not in str(exported)
    assert item_b not in str(exported)
    assert item_a in str(exported)
    assert account_b != account_a


async def test_phase3_static_authority_and_customer_language_guards():
    from app.domains.care import guidance, home_care
    from app.domains.care.guidance import CareGuidanceItem
    from app.domains.care.home_care import HomeCareItem

    assert not hasattr(CareGuidanceItem, "selected_item_id")
    assert not hasattr(HomeCareItem, "selected_item_id")
    assert "decide_hair_wash_cadence" not in getsource(home_care)
    assert "plan_care_routine" not in getsource(guidance)
    assert "CLIMATE_RULES" not in getsource(home_care)
    assert "CLIMATE_RULES" not in getsource(guidance)
    assert "routine_templates" not in getsource(home_care)
    banned = ("diagnos", "prescription", "medication", "bad skin", "bad hair", "money wasted", "buy", "shop")
    for rule in (*GUIDANCE_RULES, *HOME_CARE_RULES):
        visible = f"{rule.title} {rule.body}".lower()
        assert not any(term in visible for term in banned)
    assert len(GUIDANCE_RULES) == 3
    assert len(HOME_CARE_RULES) == 2
    assert CARE_GUIDANCE_VERSION == "v3-03.17"
    assert HOME_CARE_RULES[0].rule_version == "v3-03.18-r1"
