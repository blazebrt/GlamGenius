"""Pure provenance contract tests for V3-03.9."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.domains.care import routine_plan as care_routine_plan
from app.domains.care.decisions import CareDecisionSet, decision_fingerprint, evaluate_care_context
from app.domains.care.routine_plan import plan_care_routine, routine_plan_fingerprint
from app.domains.care.schemas import CareContext, CareEnvironment, CareEvent, CareFact, MissingCareFact
from app.domains.care.snapshot import (
    CARE_RECOMMENDATION_SNAPSHOT_VERSION,
    build_care_recommendation_snapshot,
    care_recommendation_snapshot_fingerprint,
)
from app.domains.inventory.models import InventoryItem
from app.domains.media.storage import factory as storage_factory
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import service as routines_service
from app.domains.routines.compiler import CompiledRoutine, RoutineStep
from app.domains.routines.models import Routine, RoutineRecommendationRun
from app.domains.routines.models import RoutineStep as StoredRoutineStep
from app.domains.routines.ontology import ONTOLOGY_VERSION
from app.domains.routines.parser import ParsedIngredient
from app.domains.routines.rules import ShelfProduct
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_v3_03_3_integration import (
    GENERATION_DATE,
    _allergy,
    _stored_ingredient,
)


class _DeletionStorage:
    backend_name = "v3-03.9-test"

    async def delete_prefix(self, _prefix: str) -> int:
        return 0

    async def list_prefix(self, _prefix: str) -> list[str]:
        return []


@pytest.fixture
def deletion_storage():
    adapter = _DeletionStorage()
    storage_factory.set_storage(adapter)
    yield adapter
    storage_factory.set_storage(None)


@pytest.fixture
def deletion_auth_spy(monkeypatch):
    calls: list[str] = []

    class _Admin:
        @staticmethod
        def delete_user(uid):
            calls.append(str(uid))

    class _Auth:
        admin = _Admin()

    class _Supabase:
        auth = _Auth()

    monkeypatch.setattr(
        "app.domains.privacy.deletion_service.get_supabase_admin",
        lambda: _Supabase,
    )
    return calls
from tests.test_v3_03_3_integration import (
    _generate as _db_generate,
)
from tests.test_v3_03_3_integration import (
    _product as _db_product,
)
from tests.test_v3_03_3_integration import (
    _seed as _db_seed,
)

ACCOUNT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ITEM_A = uuid.UUID("22222222-2222-2222-2222-222222222222")
ITEM_B = uuid.UUID("33333333-3333-3333-3333-333333333333")
PLAN_DATE = date(2026, 8, 11)

def _product(item_id: uuid.UUID, name: str, *, slot: str = "cleanser", usage_count: int = 1) -> ShelfProduct:
    return ShelfProduct(
        item=OwnedItem(
            id=item_id, category="beauty", subcategory=None, display_name=name, brand="secret",
            details={"ingredients_text": "Retinol", "private_note": "do not copy"}, condition="new",
            usage_count=usage_count, last_used_at=PLAN_DATE, purchase_price=99.0, currency="INR",
        ),
        slot=slot,
        ingredients=[ParsedIngredient(
            key="retinol", display_name="Retinol", family="retinoid", matched_text="RETINOL",
            position=1, confidence=0.55, source="label_text",
        )],
        effective_expiry=date(2026, 9, 1), low_use=False,
    )


def _context(products: tuple[ShelfProduct, ...]) -> CareContext:
    fact = CareFact(
        key="care_skin_usual_feel", value="dry", fact_source="care_user_declared",
        record_source="user_declared", confidence=1.0, verification_state="confirmed",
        profile_attribute_id=uuid.UUID("44444444-4444-4444-4444-444444444444"), explicit_unknown=False,
    )
    return CareContext(
        context_version="v3-03.1", account_id=ACCOUNT_ID, plan_date=PLAN_DATE,
        skin_facts={fact.key: fact}, hair_facts={}, preferences={},
        environment=CareEnvironment(
            weather_snapshot_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
            air_quality_snapshot_id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
            condition="clear", temp_min_c=20.0, temp_max_c=30.0, humidity=40,
            precipitation_chance=0, uv_index=5.0, aqi=42, aqi_index_system="india_naqi",
            aqi_category="Good", climate_region="north_plains", calendar_prior="winter",
            season="winter", temperature_band="warm", moisture_regime="dry", daily_regime="clear",
            climate_confidence=0.9, climate_reason="manual", weather_unavailable_reason=None,
        ),
        primary_event=CareEvent(
            id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
            starts_at=datetime(2026, 8, 11, 10, tzinfo=UTC), ends_at=None,
            all_day=False, occasion_key="office", confidence=0.85, user_confirmed=True,
        ),
        allergies=("fragrance", "retinol"), skin_products=products, hair_products=(),
        draft_product_count=1,
        missing_information=(MissingCareFact("hair", "care_hair_pattern", "missing"),),
    )


def _compiled() -> tuple[CompiledRoutine, ...]:
    return (
        CompiledRoutine(
            kind="morning", label="Morning routine", frequency="Every morning",
            steps=[RoutineStep(
                slot="cleanser", label="Cleanser", order=1, required=True, why="Cleanse",
                frequency="Every morning", item_id=str(ITEM_A), product_name="Cleanser A",
            )],
        ),
    )


def _snapshot(products: tuple[ShelfProduct, ...] = (_product(ITEM_A, "Cleanser A"),)) -> dict:
    context = _context(products)
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    return build_care_recommendation_snapshot(
        care_context=context, decisions=decisions, care_plan=plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
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


def test_snapshot_is_json_compatible_and_excludes_sensitive_or_mutable_identity():
    snapshot = _snapshot()
    json.dumps(snapshot)
    assert snapshot["snapshot_version"] == CARE_RECOMMENDATION_SNAPSHOT_VERSION
    text = json.dumps(snapshot)
    assert "secret" not in text
    assert "ingredients_text" not in text
    assert "private_note" not in text
    assert "purchase_price" not in text
    assert "matched_text" not in text
    assert "77777777-7777-7777-7777-777777777777" in text
    assert "fingerprint" in snapshot
    assert snapshot["rendered_routines"][0]["steps"][0]["slot"] == "cleanser"


def test_same_material_and_reordered_set_like_inputs_have_same_fingerprint():
    first = _snapshot((_product(ITEM_A, "Cleanser A"), _product(ITEM_B, "Cleanser B")))
    second = _snapshot((_product(ITEM_B, "Cleanser B"), _product(ITEM_A, "Cleanser A")))
    assert first["fingerprint"] == second["fingerprint"]
    assert care_recommendation_snapshot_fingerprint(first) == first["fingerprint"]


def test_fingerprint_changes_for_environment_only_while_decisions_and_plan_stay_same():
    first = _snapshot()
    context = _context((_product(ITEM_A, "Cleanser A"),))
    context = replace(context, environment=replace(context.environment, humidity=41))
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    changed = build_care_recommendation_snapshot(
        care_context=context, decisions=decisions, care_plan=plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
    )
    first_context = _context((_product(ITEM_A, "Cleanser A"),))
    first_decisions = evaluate_care_context(first_context)
    first_plan = plan_care_routine(first_context, first_decisions)
    assert decision_fingerprint(first_decisions) == decision_fingerprint(decisions)
    assert routine_plan_fingerprint(first_plan) == routine_plan_fingerprint(plan)
    assert first["fingerprint"] != changed["fingerprint"]


def test_fingerprint_changes_for_effort_while_decision_fingerprint_stays_same():
    product = _product(ITEM_A, "Cleanser A", slot="cleanser")
    optional = _product(ITEM_B, "Toner B", slot="toner")
    context = _context((product, optional))
    balanced_decisions = evaluate_care_context(context)
    balanced_plan = plan_care_routine(context, balanced_decisions)
    balanced = build_care_recommendation_snapshot(
        care_context=context, decisions=balanced_decisions, care_plan=balanced_plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
    )
    effort = CareFact(
        key="care_routine_effort", value="minimal", fact_source="care_user_declared",
        record_source="user_declared", confidence=1.0, verification_state="confirmed",
        profile_attribute_id=uuid.UUID("88888888-8888-8888-8888-888888888888"), explicit_unknown=False,
    )
    minimal_context = replace(context, preferences={effort.key: effort})
    minimal_decisions = evaluate_care_context(minimal_context)
    minimal_plan = plan_care_routine(minimal_context, minimal_decisions)
    minimal = build_care_recommendation_snapshot(
        care_context=minimal_context, decisions=minimal_decisions, care_plan=minimal_plan,
        compiled_routines=_compiled(), requested_kinds=None, legacy_climate="clear",
        routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
    )
    assert decision_fingerprint(balanced_decisions) == decision_fingerprint(minimal_decisions)
    assert routine_plan_fingerprint(balanced_plan) != routine_plan_fingerprint(minimal_plan)
    assert balanced["fingerprint"] != minimal["fingerprint"]
    toner = next(row for row in minimal["routine_plan"]["slots"] if row["slot"] == "toner")
    assert toner["candidate_item_ids"] == [str(ITEM_B)]
    assert toner["active"] is False
    assert toner["selected_item_id"] is None
    assert toner["inclusion_reason"] == "minimal_effort_excluded"


def test_account_and_plan_date_mismatch_fails_loudly():
    context = _context((_product(ITEM_A, "Cleanser A"),))
    decisions = CareDecisionSet(
        decision_version="v3-03.2", account_id=uuid.uuid4(), plan_date=PLAN_DATE,
        product_decisions=(), skin_core_slots=(), hair_core_slots=(),
    )
    plan = plan_care_routine(context, evaluate_care_context(context))
    try:
        build_care_recommendation_snapshot(
            care_context=context, decisions=decisions, care_plan=plan,
            compiled_routines=(), requested_kinds=[], legacy_climate=None,
            routine_engine_version="care-v3-03.5", ontology_version=ONTOLOGY_VERSION,
        )
    except ValueError as exc:
        assert "account_id" in str(exc)
    else:
        raise AssertionError("snapshot accepted contradictory account material")


@pytest.mark.asyncio
async def test_real_generation_persists_snapshot_and_privacy_export_is_scoped(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token_a, account_a = await registered_supabase_user()
    token_b, account_b = await registered_supabase_user()
    await _db_seed(app_client)
    item_a = await _db_product(app_client, token_a, name="Account A Moisturiser")
    item_b = await _db_product(app_client, token_b, name="Account B Moisturiser")
    await _db_generate(app_client, token_a, kinds=["morning"])
    await _db_generate(app_client, token_b, kinds=["morning"])

    run_a = await _latest_run(account_a)
    run_b = await _latest_run(account_b)
    snapshot = run_a.inputs["care_snapshot"]
    assert snapshot["snapshot_version"] == CARE_RECOMMENDATION_SNAPSHOT_VERSION
    assert snapshot["account_id"] == str(account_a)
    assert run_a.inputs["as_of"] == snapshot["plan_date"]
    assert run_a.inputs["care_context_version"] == snapshot["care_context_version"]
    assert run_a.inputs["care_routine_plan_version"] == snapshot["care_routine_plan_version"]
    assert run_a.inputs["care_routine_plan_fingerprint"] == snapshot["routine_plan"]["routine_plan_fingerprint"]

    exported = (await app_client.get("/api/v2/privacy/export", headers=auth(token_a))).json()
    rows = exported["domains"]["routines"]["recommendation_runs"]
    row = next(item for item in rows if item["id"] == str(run_a.id))
    assert row["inputs"]["care_snapshot"]["snapshot_version"] == "v3-03.9"
    assert row["inputs"]["care_snapshot"]["fingerprint"] == snapshot["fingerprint"]
    assert str(run_b.id) not in {item["id"] for item in rows}
    assert item_b not in json.dumps(exported)
    assert str(account_b) not in json.dumps(exported)

    factory = get_sessionmaker()
    async with factory() as session:
        _, context, decisions = await routines_service._current_care_decisions(
            session, account_a, GENERATION_DATE,
        )
        plan = care_routine_plan.plan_care_routine(context, decisions)
        assert snapshot["decisions"]["decision_fingerprint"] == decision_fingerprint(decisions)
        assert snapshot["routine_plan"]["routine_plan_fingerprint"] == care_routine_plan.routine_plan_fingerprint(plan)


@pytest.mark.asyncio
async def test_historical_snapshot_survives_current_routine_replacement(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    item_a = await _db_product(app_client, token, name="A Moisturiser")
    item_b = await _db_product(app_client, token, name="B Moisturiser")
    await _db_generate(app_client, token, kinds=["morning"])
    run_a = await _latest_run(account_id)
    snapshot_a = json.loads(json.dumps(run_a.inputs["care_snapshot"]))

    archived = await app_client.delete(f"/api/v2/inventory/items/{item_a}", headers=auth(token))
    assert archived.status_code == 200, archived.text
    await _db_generate(app_client, token, kinds=["morning"])
    run_b = await _latest_run(account_id)
    snapshot_b = run_b.inputs["care_snapshot"]
    selected_a = next(row for row in snapshot_a["routine_plan"]["slots"] if row["slot"] == "moisturiser")
    selected_b = next(row for row in snapshot_b["routine_plan"]["slots"] if row["slot"] == "moisturiser")
    assert selected_a["selected_item_id"] == item_a
    assert selected_b["selected_item_id"] == item_b
    assert snapshot_b["fingerprint"] != snapshot_a["fingerprint"]

    factory = get_sessionmaker()
    async with factory() as session:
        current = (await session.execute(
            select(StoredRoutineStep).join(Routine).where(
                Routine.account_id == account_id, Routine.kind == "morning",
                StoredRoutineStep.slot == "moisturiser",
            )
        )).scalar_one()
        first = await session.get(RoutineRecommendationRun, run_a.id)
    assert str(current.inventory_item_id) == item_b
    assert first.inputs["care_snapshot"] == snapshot_a


@pytest.mark.asyncio
async def test_a_b_a_snapshot_fingerprints_are_stable_and_runs_remain_distinct(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    item_a = await _db_product(app_client, token, name="A Moisturiser")
    item_b = await _db_product(app_client, token, name="B Moisturiser")
    await _db_generate(app_client, token, kinds=["morning"])
    first = await _latest_run(account_id)
    await app_client.delete(f"/api/v2/inventory/items/{item_a}", headers=auth(token))
    await _db_generate(app_client, token, kinds=["morning"])
    middle = await _latest_run(account_id)
    factory = get_sessionmaker()
    async with factory() as session:
        a_row = await session.get(InventoryItem, uuid.UUID(item_a))
        b_row = await session.get(InventoryItem, uuid.UUID(item_b))
        a_row.status = "active"
        b_row.status = "archived"
        await session.commit()
    await _db_generate(app_client, token, kinds=["morning"])
    last = await _latest_run(account_id)
    assert first.id != middle.id != last.id
    assert first.inputs["care_snapshot"]["fingerprint"] == last.inputs["care_snapshot"]["fingerprint"]
    assert middle.inputs["care_snapshot"]["fingerprint"] != first.inputs["care_snapshot"]["fingerprint"]


@pytest.mark.asyncio
async def test_explain_false_and_true_preserve_snapshot_material(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _db_product(app_client, token, name="Explained Moisturiser")
    first = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"kinds": ["morning"], "as_of": GENERATION_DATE.isoformat(), "explain": False},
    )
    assert first.status_code == 200, first.text
    run_false = await _latest_run(account_id)
    second = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"kinds": ["morning"], "as_of": GENERATION_DATE.isoformat(), "explain": True},
    )
    assert second.status_code == 200, second.text
    run_true = await _latest_run(account_id)
    assert run_false.inputs["care_snapshot"] == run_true.inputs["care_snapshot"]


@pytest.mark.asyncio
async def test_observations_and_sensitive_inventory_fields_are_not_shadow_logged(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    sentinel = "PRIVATE_OBSERVATION_SENTINEL_V3_03_9"
    observed = await app_client.post(
        "/api/v2/routines/observations", headers=auth(token),
        json={"area": "skin", "note": sentinel},
    )
    assert observed.status_code == 200, observed.text
    created = await app_client.post(
        "/api/v2/inventory/items", headers=auth(token),
        json={
            "category": "beauty", "display_name": "Safe Display Name",
            "subcategory": "moisturiser", "brand": "PRIVATE_BRAND_SENTINEL",
            "purchase_price": 9876.5,
            "details": {"product_type": "moisturiser", "ingredients_text": "PRIVATE_RAW_INCI_SENTINEL"},
        },
    )
    assert created.status_code in (200, 201), created.text
    await _db_generate(app_client, token, kinds=["morning"])
    run = await _latest_run(account_id)
    serialized = json.dumps(run.inputs["care_snapshot"])
    assert sentinel not in serialized
    assert "PRIVATE_BRAND_SENTINEL" not in serialized
    assert "9876.5" not in serialized
    assert "PRIVATE_RAW_INCI_SENTINEL" not in serialized
    assert "Safe Display Name" in serialized


@pytest.mark.asyncio
async def test_account_deletion_removes_recommendation_run(
    app_client, db_clean, registered_supabase_user, fake_provider,
    deletion_storage, deletion_auth_spy,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _db_product(app_client, token, name="Delete Me Moisturiser")
    await _db_generate(app_client, token, kinds=["morning"])
    await app_client.delete("/api/v2/privacy/account", headers=auth(token))
    from app.domains.privacy import deletion_service
    factory = get_sessionmaker()
    async with factory() as session:
        await deletion_service.drain_all(session)
        await session.commit()
        remaining = (await session.execute(
            select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id)
        )).scalars().all()
        assert remaining == []


@pytest.mark.asyncio
async def test_persisted_snapshot_safety_gap_optional_and_requested_kind_material(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    expired = await _db_product(
        app_client, token, name="Expired Moisturiser", expiry=date(2026, 8, 1),
    )
    optional = await _db_product(app_client, token, name="Optional Toner", product_type="toner")
    await _db_generate(app_client, token, kinds=["morning"])
    run = await _latest_run(account_id)
    snapshot = run.inputs["care_snapshot"]
    assert snapshot["requested_kinds"] == ["morning"]
    assert {row["kind"] for row in snapshot["rendered_routines"]} == {"morning"}
    expired_decision = next(row for row in snapshot["decisions"]["product_decisions"] if row["item_id"] == expired)
    assert expired_decision["eligible"] is False
    assert {row["code"] for row in expired_decision["blocking_reasons"]} == {"product_expired"}
    assert all(row["authority"] == "system_policy" for row in expired_decision["blocking_reasons"])

    await _db_product(app_client, token, name="Required Gap Seed", product_type="sunscreen", expiry=date(2026, 8, 1))
    await _db_generate(app_client, token, kinds=["morning"])
    gap_run = await _latest_run(account_id)
    gap_snapshot = gap_run.inputs["care_snapshot"]
    sunscreen = next(row for row in gap_snapshot["routine_plan"]["slots"] if row["slot"] == "sunscreen")
    assert sunscreen["active"] is True
    assert sunscreen["selected_item_id"] is None
    assert sunscreen["is_gap"] is True
    assert sunscreen["inclusion_reason"] == "required"
    morning = next(row for row in gap_snapshot["rendered_routines"] if row["kind"] == "morning")
    assert any(step["slot"] == "sunscreen" and step["is_gap"] for step in morning["steps"])
    assert optional in json.dumps(gap_snapshot)


@pytest.mark.asyncio
async def test_persisted_safety_reason_authority_and_profile_provenance(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    confirmed = await _db_product(
        app_client, token, name="Confirmed Allergy Product", active_ingredients=["fragrance"],
    )
    expired = await _db_product(
        app_client, token, name="Expired Safety Product", expiry=date(2026, 8, 1),
    )
    uncertain = await _db_product(
        app_client, token, name="Unconfirmed Allergy Product", active_ingredients=["fragrance"],
    )
    await _stored_ingredient(account_id, confirmed, confirmed=True)
    await _stored_ingredient(account_id, uncertain, confirmed=False)
    await _allergy(app_client, token)
    profile = await app_client.patch(
        "/api/v2/profile", headers=auth(token),
        json={"attributes": [{"key": "care_skin_usual_feel", "value": "not_sure"}]},
    )
    assert profile.status_code == 200, profile.text
    await _db_generate(app_client, token, kinds=["morning"])
    snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    confirmed_row = next(row for row in snapshot["decisions"]["product_decisions"] if row["item_id"] == confirmed)
    expired_row = next(row for row in snapshot["decisions"]["product_decisions"] if row["item_id"] == expired)
    uncertain_row = next(row for row in snapshot["decisions"]["product_decisions"] if row["item_id"] == uncertain)
    assert confirmed_row["eligible"] is False
    assert {row["code"] for row in confirmed_row["blocking_reasons"]} == {"confirmed_allergy_match"}
    assert all(row["authority"] == "user_constraint" for row in confirmed_row["blocking_reasons"])
    assert expired_row["eligible"] is False
    assert {row["code"] for row in expired_row["blocking_reasons"]} == {"product_expired"}
    assert all(row["authority"] == "system_policy" for row in expired_row["blocking_reasons"])
    assert uncertain_row["eligible"] is True
    assert {row["code"] for row in uncertain_row["advisory_reasons"]} == {"ingredient_confirmation_needed"}
    fact = next(row for row in snapshot["skin_facts"] if row["key"] == "care_skin_usual_feel")
    assert set(fact) == {
        "key", "value", "fact_source", "record_source", "confidence",
        "verification_state", "profile_attribute_id", "explicit_unknown",
    }
    assert fact["explicit_unknown"] is True


@pytest.mark.asyncio
async def test_empty_generation_still_persists_a_snapshot(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, account_id = await registered_supabase_user()
    await _db_seed(app_client)
    await _db_generate(app_client, token, kinds=["morning"])
    snapshot = (await _latest_run(account_id)).inputs["care_snapshot"]
    assert snapshot["rendered_routines"] == []
    assert snapshot["fingerprint"]
