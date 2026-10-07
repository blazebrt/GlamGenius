"""Existing F12 protection: the real export reads snapshots, never today's authority."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.domains.care import guidance, guidance_rules, home_care, home_care_rules, snapshot
from app.domains.care.decisions import evaluate_care_context
from app.domains.care.routine_plan import plan_care_routine
from app.domains.evidence import service as evidence_service
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, RuleEvidenceLink
from app.domains.routines import service as routines_service
from app.domains.routines.models import Routine, RoutineRecommendationRun, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

from tests.conftest import auth
from tests.test_v3_03_3_integration import _product, _seed
from tests.test_v3_03_18_home_care import _fact

pytestmark = pytest.mark.asyncio
UV_RULE = "care.skin.uv_protection_uvi_3"


async def _generated(client, token, account_id, monkeypatch):
    await _product(client, token, name="Historical cleanser", product_type="cleanser")
    await _product(client, token, name="Historical moisturiser", product_type="moisturiser")
    day = date(2026, 8, 12)
    async with get_sessionmaker()() as session:
        day_context, context, _ = await routines_service._current_care_decisions(session, account_id, day)
    context = replace(
        context,
        skin_facts={"care_skin_usual_feel": _fact("care_skin_usual_feel", "often_dry_or_tight")},
        environment=replace(context.environment, uv_index=3.0, moisture_regime="dry"),
    )
    decisions = evaluate_care_context(context)

    async def current_inputs(*_args, **_kwargs):
        return day_context, context, decisions

    monkeypatch.setattr(routines_service, "_current_care_decisions", current_inputs)
    response = await client.post("/api/v2/routines/generate", headers=auth(token), json={
        "kinds": ["morning"], "as_of": day.isoformat(), "explain": False,
    })
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as session:
        run = await session.scalar(select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id))
        stored = deepcopy(run.inputs)
    assert stored["care_snapshot"]["home_care"]["items"]
    assert stored["care_snapshot"]["care_guidance"]["items"]
    assert stored["care_snapshot"]["rendered_routines"]
    return run.id, stored, context, decisions


def _forbid_reconstruction(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("historical export must not consult today's rules, evidence or rendering")

    for module, name in (
        (guidance, "build_care_guidance"), (guidance, "assess_rule_evidence"),
        (home_care, "build_home_care"), (home_care, "assess_rule_evidence"),
        (evidence_service, "assess_rule_evidence"), (evidence_service, "assert_rule_exists"),
        (routines_service, "_current_care_decisions"),
        (snapshot, "build_care_recommendation_snapshot"),
    ):
        monkeypatch.setattr(module, name, forbidden)


async def _assert_export(client, token, run_id, stored, account_id):
    response = await client.get("/api/v2/privacy/export", headers=auth(token))
    assert response.status_code == 200, response.text
    history = response.json()["domains"]["routines"]["routine_history"]
    buckets = [*history["by_subject"].values(), history["account_holder_legacy"], history["unattributed"]]
    rows = [row for bucket in buckets for row in bucket["recommendation_runs"]]
    assert len(rows) == 1
    assert rows[0]["id"] == str(run_id)
    assert rows[0]["account_id"] == str(account_id)
    assert rows[0]["inputs"] == stored
    historical = rows[0]["inputs"]["care_snapshot"]
    assert snapshot.care_recommendation_snapshot_fingerprint(historical) == stored["care_snapshot"]["fingerprint"]
    # Today's customer copy was deliberately never a historical contract.
    for area in ("care_guidance", "home_care"):
        assert all("title" not in row and "body" not in row for row in historical[area]["items"])


async def test_history_ignores_current_registry_versions_and_mutable_routine_rows(
    db_clean, app_client, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    _, other_id = await registered_supabase_user()
    await _seed(app_client)
    run_id, stored, _, _ = await _generated(app_client, token, account_id, monkeypatch)
    async with get_sessionmaker()() as session:
        routines = (await session.scalars(select(Routine).where(Routine.account_id == account_id))).all()
        steps = (await session.scalars(select(RoutineStep).where(RoutineStep.routine_id.in_([row.id for row in routines])))).all()
        assert routines and steps
        for routine in routines:
            routine.label = "Today's replacement routine"
        for step in steps:
            step.why = "Today's replacement reason"
            step.inventory_item_id = None
        session.add(RoutineRecommendationRun(account_id=other_id, inputs={"foreign_history": True}))
        await session.commit()
    async with get_sessionmaker()() as session:
        assert all(row.label == "Today's replacement routine" for row in (await session.scalars(
            select(Routine).where(Routine.account_id == account_id),
        )).all())
        assert all(row.why == "Today's replacement reason" and row.inventory_item_id is None
                   for row in (await session.scalars(select(RoutineStep).where(
                       RoutineStep.routine_id.in_([routine.id for routine in routines]),
                   ))).all())
    for registry, runtime, name, version_name, map_name in (
        (guidance_rules, guidance, "GUIDANCE_RULES", "CARE_GUIDANCE_RULESET_VERSION", "GUIDANCE_RULE_BY_ID"),
        (home_care_rules, home_care, "HOME_CARE_RULES", "HOME_CARE_RULESET_VERSION", "HOME_CARE_RULE_BY_ID"),
    ):
        changed = tuple(replace(rule, rule_version="future-ruleset-B", title="B", body="B") for rule in getattr(registry, name))
        monkeypatch.setattr(registry, name, changed)
        monkeypatch.setattr(runtime, name, changed)
        monkeypatch.setattr(registry, version_name, "future-ruleset-B")
        monkeypatch.setattr(runtime, version_name, "future-ruleset-B")
        monkeypatch.setattr(registry, map_name, {rule.rule_id: rule for rule in changed})
    _forbid_reconstruction(monkeypatch)
    await _assert_export(app_client, token, run_id, stored, account_id)


async def test_missing_legacy_snapshot_is_exported_as_missing_not_backfilled_from_current_rules(
    db_clean, app_client, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    legacy = {"legacy_rule_id": UV_RULE}
    async with get_sessionmaker()() as session:
        run = RoutineRecommendationRun(account_id=account_id, inputs=legacy)
        session.add(run)
        await session.commit()
        run_id = run.id
    _forbid_reconstruction(monkeypatch)
    response = await app_client.get("/api/v2/privacy/export", headers=auth(token))
    assert response.status_code == 200, response.text
    rows = response.json()["domains"]["routines"]["routine_history"]["account_holder_legacy"]["recommendation_runs"]
    assert len(rows) == 1 and rows[0]["id"] == str(run_id)
    assert rows[0]["inputs"] == legacy
    assert "care_snapshot" not in rows[0]["inputs"]


async def test_retired_evidence_does_not_erase_its_pinned_historical_authority(
    db_clean, app_client, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    # Published at generation, then retired; these are real persisted states.
    async with get_sessionmaker()() as session:
        claims = (await session.scalars(select(EvidenceClaim).where(EvidenceClaim.claim_key.in_(
            ["skin.uv_index_3_sun_protection", "skin.dry_air_moisture_support", "home.skin.gentle_bathing_for_dry_skin"],
        )))).all()
        assert len(claims) == 3
        for claim in claims:
            claim.review_status = "published"
            claim.published_at = datetime.now(UTC)
        await session.commit()
    run_id, stored, context, decisions = await _generated(app_client, token, account_id, monkeypatch)
    original_ids = {
        claim_id for area in ("care_guidance", "home_care")
        for item in stored["care_snapshot"][area]["items"] for claim_id in item["evidence_claim_ids"]
    }
    assert original_ids == {str(claim.id) for claim in claims}
    async with get_sessionmaker()() as session:
        for claim in (await session.scalars(select(EvidenceClaim).where(EvidenceClaim.id.in_([row.id for row in claims])))).all():
            claim.review_status = "retired"
        await session.commit()
        current = await guidance.build_care_guidance(session, care_context=context, care_plan=plan_care_routine(context, decisions))
        assert not current.items  # Retirement really changes today's authority.
    _forbid_reconstruction(monkeypatch)
    await _assert_export(app_client, token, run_id, stored, account_id)


async def test_later_published_claim_is_not_retroactively_added_to_history(
    db_clean, app_client, registered_supabase_user, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _seed(app_client)
    async with get_sessionmaker()() as session:
        old = await session.scalar(select(EvidenceClaim).where(EvidenceClaim.claim_key == "skin.uv_index_3_sun_protection"))
        old.review_status = "draft"
        await session.commit()
        old_id = old.id
    run_id, stored, context, decisions = await _generated(app_client, token, account_id, monkeypatch)
    assert UV_RULE not in {item["rule_id"] for item in stored["care_snapshot"]["care_guidance"]["items"]}
    async with get_sessionmaker()() as session:
        old = await session.get(EvidenceClaim, old_id)
        fields = {col.name: deepcopy(getattr(old, col.name)) for col in EvidenceClaim.__table__.columns
                  if col.name not in {"id", "created_at", "updated_at"}}
        later = EvidenceClaim(**{**fields, "claim_version": old.claim_version + 1,
                                 "review_status": "published", "published_at": datetime.now(UTC)})
        session.add(later)
        await session.flush()
        later_id = later.id
        for model in (EvidenceClaimSource, RuleEvidenceLink):
            links = (await session.scalars(select(model).where(model.claim_id == old_id))).all()
            assert links
            for link in links:
                values = {col.name: deepcopy(getattr(link, col.name)) for col in model.__table__.columns
                          if col.name not in {"id", "created_at", "updated_at", "claim_id"}}
                session.add(model(claim_id=later_id, **values))
        await session.commit()
        current = await guidance.build_care_guidance(session, care_context=context, care_plan=plan_care_routine(context, decisions))
        uv = next(item for item in current.items if item.rule_id == UV_RULE)
        assert uv.evidence_claim_ids == (later_id,)
    assert str(later_id) not in str(stored)
    _forbid_reconstruction(monkeypatch)
    await _assert_export(app_client, token, run_id, stored, account_id)
