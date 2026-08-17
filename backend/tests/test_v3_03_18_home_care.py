"""V3-03.18 exact Home Care registry, gates, and authority boundaries."""
from __future__ import annotations

import inspect
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from app.bootstrap import run as run_seed
from app.domains.care import cadence as care_cadence
from app.domains.care import home_care
from app.domains.care.decisions import decision_fingerprint, evaluate_care_context
from app.domains.care.evidence_applicability import CareRuleApplicabilityResult
from app.domains.care.guidance_rules import GUIDANCE_RULES
from app.domains.care.home_care_rules import HOME_CARE_RULES, HOME_CARE_RULESET_VERSION
from app.domains.care.routine_plan import plan_care_routine, routine_plan_fingerprint
from app.domains.care.schemas import CareFact
from app.domains.evidence import home_care_seed
from app.domains.evidence.applicability import EVIDENCE_APPLICABILITY_VERSION, EvidenceApplicability
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import (
    BehaviorEligibleEvidencePath,
    EvidenceRuleResolutionError,
    RuleEvidenceAssessment,
    assert_rule_exists,
    assess_rule_evidence,
)
from app.domains.routines import service as routines_service
from app.domains.routines.models import RoutineRecommendationRun
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_care_decisions import _context as _base_context
from tests.test_v3_03_3_integration import _product as _integration_product
from tests.test_v3_03_3_integration import _seed as _integration_seed


def _fact(key: str, value: str, *, unknown: bool = False) -> CareFact:
    return CareFact(key, value, "user_declared", "user_declared", 1.0, "confirmed", uuid4(), unknown)


def _context(*, skin_feel: str | None = None, moisture: str | None = None):
    base = _base_context(uv_index=None, moisture_regime=moisture)
    return replace(
        base,
        skin_facts={"care_skin_usual_feel": _fact("care_skin_usual_feel", skin_feel)} if skin_feel else {},
    )


def _eligible_assessment() -> RuleEvidenceAssessment:
    claim_id = uuid4()
    applicability = EvidenceApplicability(
        schema_version=EVIDENCE_APPLICABILITY_VERSION,
        jurisdictions=("global",), populations=("general_population",),
        formulations=("non_product_home_care",),
        usage_contexts=("dry_skin_bathing", "post_wash_hair_drying"),
    )
    return RuleEvidenceAssessment(
        True, True, True,
        behavior_eligible_paths=(BehaviorEligibleEvidencePath(claim_id, applicability),),
    )


async def _eligible(*_args, **_kwargs):
    return _eligible_assessment()


def _applicable(_assessment, signals):
    return CareRuleApplicabilityResult(
        EVIDENCE_APPLICABILITY_VERSION, True, True, (UUID(int=1),),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skin_feel", "moisture", "expected"),
    [("often_dry_or_tight", "dry", True), ("comfortable", "dry", False),
     ("often_dry_or_tight", "normal", False), ("often_dry_or_tight", "humid", False),
     ("not_sure", "dry", False), (None, "dry", False)],
)
async def test_skin_home_care_requires_exact_trusted_context(monkeypatch, skin_feel, moisture, expected):
    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(home_care, "resolve_care_evidence_applicability", _applicable)
    context = _context(skin_feel=skin_feel, moisture=moisture)
    cadence = care_cadence.decide_hair_wash_cadence(None, plan_date=context.plan_date, last_wash_on=None)
    result = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    assert any(row.rule_id == "care.home.skin_gentle_bathing" for row in result.items) is expected


@pytest.mark.asyncio
async def test_skin_explicit_unknown_and_evidence_or_applicability_failure_fail_closed(monkeypatch):
    context = replace(_context(skin_feel="often_dry_or_tight", moisture="dry"), skin_facts={
        "care_skin_usual_feel": _fact("care_skin_usual_feel", "often_dry_or_tight", unknown=True),
    })
    cadence = care_cadence.decide_hair_wash_cadence(None, plan_date=context.plan_date, last_wash_on=None)
    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    result = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    assert not result.items

    async def ineligible(*_args, **_kwargs):
        return RuleEvidenceAssessment(False, False, False)
    monkeypatch.setattr(home_care, "assess_rule_evidence", ineligible)
    context = _context(skin_feel="often_dry_or_tight", moisture="dry")
    assert not (await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)).items

    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(home_care, "resolve_care_evidence_applicability", lambda *_args: CareRuleApplicabilityResult(EVIDENCE_APPLICABILITY_VERSION, False, True))
    assert not (await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)).items


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [care_cadence.HairWashCadenceStatus.NOT_DUE, care_cadence.HairWashCadenceStatus.NEEDS_ANCHOR, care_cadence.HairWashCadenceStatus.UNSCHEDULED],
)
async def test_hair_home_care_requires_due_cadence(monkeypatch, status):
    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(home_care, "resolve_care_evidence_applicability", _applicable)
    context = _context()
    cadence = replace(care_cadence.decide_hair_wash_cadence(None, plan_date=context.plan_date, last_wash_on=None), status=status)
    result = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    assert not any(row.rule_id == "care.home.hair_gentle_drying" for row in result.items)


@pytest.mark.asyncio
async def test_due_hair_home_care_is_evidence_gated_and_does_not_recompute_cadence(monkeypatch):
    context = _context()
    cadence = care_cadence.decide_hair_wash_cadence("daily", plan_date=context.plan_date, last_wash_on=None)
    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(home_care, "resolve_care_evidence_applicability", _applicable)
    result = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    assert [row.rule_id for row in result.items] == ["care.home.hair_gentle_drying"]


@pytest.mark.asyncio
async def test_due_hair_home_care_fails_closed_for_evidence_or_applicability(monkeypatch):
    context = _context()
    cadence = care_cadence.decide_hair_wash_cadence("daily", plan_date=context.plan_date, last_wash_on=None)

    async def ineligible(*_args, **_kwargs):
        return RuleEvidenceAssessment(False, False, False)

    monkeypatch.setattr(home_care, "assess_rule_evidence", ineligible)
    assert not (await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)).items

    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(
        home_care, "resolve_care_evidence_applicability",
        lambda *_args: CareRuleApplicabilityResult(EVIDENCE_APPLICABILITY_VERSION, False, True),
    )
    assert not (await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)).items


@pytest.mark.asyncio
async def test_home_care_is_sorted_bounded_deterministic_and_preserves_authority(monkeypatch):
    monkeypatch.setattr(home_care, "assess_rule_evidence", _eligible)
    monkeypatch.setattr(home_care, "resolve_care_evidence_applicability", _applicable)
    context = _context(skin_feel="often_dry_or_tight", moisture="dry")
    cadence = care_cadence.decide_hair_wash_cadence("daily", plan_date=context.plan_date, last_wash_on=None)
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    before_decision = decision_fingerprint(decisions)
    before_plan = routine_plan_fingerprint(plan)
    before_cadence = care_cadence.hair_wash_cadence_fingerprint(cadence)
    first = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    second = await home_care.build_home_care(None, care_context=context, hair_wash_cadence=cadence)
    assert [row.priority for row in first.items] == sorted(row.priority for row in first.items)
    assert first.fingerprint == second.fingerprint
    assert len(first.items) <= 2
    assert decision_fingerprint(decisions) == before_decision
    assert routine_plan_fingerprint(plan) == before_plan
    assert care_cadence.hair_wash_cadence_fingerprint(cadence) == before_cadence
    assert "CLIMATE_RULES" not in inspect.getsource(home_care)
    assert "routine_templates" not in inspect.getsource(home_care)
    assert "decide_hair_wash_cadence" not in inspect.getsource(home_care)


def test_registry_has_exactly_two_non_product_rules_and_no_duplicate_identity():
    assert len(HOME_CARE_RULES) == 2
    assert [(row.domain, row.rule_kind, row.rule_id, row.rule_version) for row in HOME_CARE_RULES] == [
        ("home_care", "routine_guidance", "care.home.skin_gentle_bathing", HOME_CARE_RULESET_VERSION),
        ("home_care", "routine_guidance", "care.home.hair_gentle_drying", HOME_CARE_RULESET_VERSION),
    ]
    assert all(row.applicability_signals.formulations == ("non_product_home_care",) for row in HOME_CARE_RULES)
    assert all(not hasattr(row, "selected_item_id") for row in HOME_CARE_RULES)


def test_hair_home_care_copy_matches_linked_evidence_boundary():
    hair_rule = next(row for row in HOME_CARE_RULES if row.rule_id == "care.home.hair_gentle_drying")
    hair_claim = next(row for row in home_care_seed.CLAIM_DEFS if row["claim_key"] == "home.hair.gentle_drying_after_wash")
    for text in (hair_rule.body, hair_claim["summary"]):
        assert "air-dry" not in text.lower()
        assert "air dry" not in text.lower()
        assert "towel" in text.lower()
        assert "t-shirt" in text.lower()


@pytest.mark.asyncio
async def test_exact_home_care_rule_resolution_is_closed_and_preserves_guidance():
    for rule in (*GUIDANCE_RULES, *HOME_CARE_RULES):
        await assert_rule_exists(None, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)
    with pytest.raises(EvidenceRuleResolutionError):
        await assert_rule_exists(None, domain="home_care", rule_kind="routine_guidance", rule_id="care.home.third", rule_version=HOME_CARE_RULESET_VERSION)
    with pytest.raises(EvidenceRuleResolutionError):
        await assert_rule_exists(None, domain="home_care", rule_kind="routine_guidance", rule_id=HOME_CARE_RULES[0].rule_id, rule_version="wrong")
    with pytest.raises(EvidenceRuleResolutionError):
        await assert_rule_exists(None, domain="skin_care", rule_kind="routine_guidance", rule_id=HOME_CARE_RULES[0].rule_id, rule_version=HOME_CARE_RULES[0].rule_version)


@pytest.mark.asyncio
async def test_home_care_seed_reuses_sources_and_is_idempotent(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        first = await run_seed(session)
        second = await run_seed(session)
        sources = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key.in_(("aad.dry_skin.relief_tips", "aad.healthy_hair.tips.2024"))))).scalars().all()
        claims = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key.like("home.%")))).scalars().all()
        links = (await session.execute(select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id.in_([row.id for row in claims])))).scalars().all()
        rule_links = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.domain == "home_care"))).scalars().all()
    assert first["home_care_evidence"] == second["home_care_evidence"]
    assert first["home_care_evidence"] == {
        "seed_version": home_care_seed.HOME_CARE_EVIDENCE_SEED_VERSION,
        "sources": 0, "claims": 2, "claim_source_links": 2,
        "rule_links": 2, "rows_written": 6,
    }
    assert len(sources) == 2
    assert len(claims) == len(links) == len(rule_links) == 2
    assert {row.claim_key for row in claims} == {"home.skin.gentle_bathing_for_dry_skin", "home.hair.gentle_drying_after_wash"}
    assert all(row.review_status == "approved" and row.claim_status == "supported" and row.ai_generated for row in claims)
    assert all(row.reviewed_by == "repository_review:v3-03.18" and row.reviewed_at for row in claims)


@pytest.mark.asyncio
async def test_seeded_home_care_rules_are_behavior_evidence_eligible(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        assessments = [
            await assess_rule_evidence(
                session, domain=rule.domain, rule_kind=rule.rule_kind,
                rule_id=rule.rule_id, rule_version=rule.rule_version,
            )
            for rule in HOME_CARE_RULES
        ]
    assert all(assessment.provenance_present for assessment in assessments)
    assert all(assessment.substantive_support_present for assessment in assessments)
    assert all(assessment.behavior_evidence_eligible for assessment in assessments)


@pytest.mark.asyncio
async def test_generate_routines_adds_home_care_without_changing_compiled_authority(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _integration_seed(app_client)
    await _integration_product(app_client, token, name="Home Care cleanser", product_type="cleanser")
    await _integration_product(app_client, token, name="Home Care moisturiser", product_type="moisturiser")
    plan_date = _base_context().plan_date
    factory = get_sessionmaker()
    async with factory() as session:
        day_context, gathered, _ = await routines_service._current_care_decisions(
            session, account_id, plan_date,
        )
    context = replace(
        gathered,
        plan_date=plan_date,
        skin_facts={"care_skin_usual_feel": _fact("care_skin_usual_feel", "often_dry_or_tight")},
        environment=replace(gathered.environment, moisture_regime="dry"),
    )
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    from app.domains.routines import compiler
    baseline = compiler.compile_all(
        context.skin_products, context.hair_products, allergies=context.allergies, climate=None,
        today=plan_date, eligibility=routines_service._routine_eligibility(decisions),
        selection_plan=routines_service._routine_selection_plan(plan),
    )
    baseline_material = {(row.kind, step.slot): (step.item_id, step.is_gap) for row in baseline for step in row.steps}

    async def current_inputs(*_args, **_kwargs):
        return day_context, context, decisions

    monkeypatch.setattr("app.domains.routines.service._current_care_decisions", current_inputs)
    response = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"as_of": plan_date.isoformat(), "explain": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    home_payload = body["home_care"]
    assert [item["rule_id"] for item in home_payload["items"]] == ["care.home.skin_gentle_bathing"]
    assert decision_fingerprint(decisions) == decision_fingerprint(evaluate_care_context(context))
    assert plan.selected_item_ids == tuple(row.selected_item_id for row in (*plan.skin_slots, *plan.hair_slots) if row.selected_item_id)
    runtime_material = {
        (row["kind"], step["slot"]): (step["inventory_item_id"], step["is_gap"])
        for row in body["routines"] for step in row["steps"]
    }
    assert runtime_material == baseline_material
    async with factory() as session:
        run = (await session.execute(select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id))).scalars().one()
    snapshot = run.inputs["care_snapshot"]
    assert snapshot["snapshot_version"] == "v3-03.18"
    assert snapshot["home_care"]["fingerprint"] == home_payload["fingerprint"]
    assert snapshot["hair_wash_cadence"]["fingerprint"] == run.inputs["hair_wash_cadence_fingerprint"]
    assert run.inputs["care_routine_plan_fingerprint"] == routine_plan_fingerprint(plan)
    assert run.inputs["home_care_fingerprint"] == home_payload["fingerprint"]


@pytest.mark.asyncio
async def test_routines_today_projects_fresh_home_care_read_only(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch,
):
    token, account_id = await registered_supabase_user()
    await _integration_seed(app_client)
    await _integration_product(app_client, token, name="Today Home Care cleanser", product_type="cleanser")
    plan_date = _base_context().plan_date
    factory = get_sessionmaker()
    async with factory() as session:
        day_context, gathered, _ = await routines_service._current_care_decisions(
            session, account_id, plan_date,
        )
    context = replace(
        gathered,
        plan_date=plan_date,
        skin_facts={"care_skin_usual_feel": _fact("care_skin_usual_feel", "often_dry_or_tight")},
        environment=replace(gathered.environment, moisture_regime="dry"),
    )
    decisions = evaluate_care_context(context)

    async def current_inputs(*_args, **_kwargs):
        return day_context, context, decisions

    monkeypatch.setattr("app.domains.routines.service._current_care_decisions", current_inputs)
    response = await app_client.post(
        "/api/v2/routines/generate", headers=auth(token),
        json={"as_of": plan_date.isoformat(), "explain": False},
    )
    assert response.status_code == 200, response.text
    from app.domains.planning.models import DailyPlan
    from app.domains.routines.models import RoutineStep
    async with factory() as session:
        before_cadence = await routines_service._current_hair_wash_cadence(
            session, account_id=account_id, care_context=context,
        )
        before_steps = {
            row.slot: (str(row.inventory_item_id) if row.inventory_item_id else None)
            for row in (await session.execute(select(RoutineStep))).scalars().all()
        }
        before_daily_plans = await session.scalar(select(func.count()).select_from(DailyPlan))
        body = await routines_service.routines_today(session, account_id=account_id, on=plan_date)
        after_daily_plans = await session.scalar(select(func.count()).select_from(DailyPlan))
        after_steps = {
            row.slot: (str(row.inventory_item_id) if row.inventory_item_id else None)
            for row in (await session.execute(select(RoutineStep))).scalars().all()
        }
        after_cadence = await routines_service._current_hair_wash_cadence(
            session, account_id=account_id, care_context=context,
        )
    assert [item["rule_id"] for item in body["home_care"]["items"]] == ["care.home.skin_gentle_bathing"]
    assert before_steps == after_steps
    assert before_daily_plans == after_daily_plans
    assert before_cadence == after_cadence
