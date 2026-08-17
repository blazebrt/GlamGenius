from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date
from uuid import UUID, uuid4

import pytest
from app.bootstrap import run as run_reference_seed
from app.domains.care import cadence as care_cadence
from app.domains.care import guidance
from app.domains.care.decisions import decision_fingerprint, evaluate_care_context
from app.domains.care.evidence_applicability import CareRuleApplicabilityResult
from app.domains.care.guidance import CARE_GUIDANCE_VERSION, CareGuidanceItem, CareGuidanceSet, guidance_fingerprint
from app.domains.care.guidance_rules import CARE_GUIDANCE_RULESET_VERSION, GUIDANCE_RULES
from app.domains.care.routine_plan import plan_care_routine, routine_plan_fingerprint
from app.domains.care.schemas import CareFact
from app.domains.evidence.applicability import EVIDENCE_APPLICABILITY_VERSION, EvidenceApplicability
from app.domains.evidence.guidance_seed import SOURCE_DEFS
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.service import (
    BehaviorEligibleEvidencePath,
    EvidenceRuleResolutionError,
    RuleEvidenceAssessment,
    assert_rule_exists,
    assess_rule_evidence,
)
from app.domains.reference import SeedVersionRecord
from app.domains.routines import compiler
from app.domains.routines import service as routines_service
from app.domains.routines.models import RoutineRecommendationRun
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth
from tests.test_care_decisions import _context as _base_context
from tests.test_care_decisions import _product
from tests.test_v3_03_3_integration import _product as _integration_product
from tests.test_v3_03_3_integration import _seed as _integration_seed


def _fact(key: str, value: str) -> CareFact:
    return CareFact(key, value, "user_declared", "user_declared", 1.0, "confirmed", uuid4(), False)


def _context(*, uv_index=None, moisture_regime=None, skin_feel=None, heat_frequency=None, wash_frequency=None, products=()):
    base = _base_context(*products, uv_index=uv_index, moisture_regime=moisture_regime)
    hair_facts = {"care_heat_styling_frequency": _fact("care_heat_styling_frequency", heat_frequency)} if heat_frequency else {}
    if wash_frequency:
        hair_facts["care_hair_wash_frequency"] = _fact("care_hair_wash_frequency", wash_frequency)
    return replace(
        base,
        skin_facts={"care_skin_usual_feel": _fact("care_skin_usual_feel", skin_feel)} if skin_feel else {},
        hair_facts=hair_facts,
    )


def _eligible_assessment() -> RuleEvidenceAssessment:
    claim_id = uuid4()
    applicability = EvidenceApplicability(
        schema_version=EVIDENCE_APPLICABILITY_VERSION,
        jurisdictions=("global",), populations=("general_population",),
        formulations=("sun_protection", "moisturiser", "heat_protection"),
        usage_contexts=("outdoor_uv_exposure", "after_cleansing", "heat_styling"),
    )
    path = BehaviorEligibleEvidencePath(claim_id=claim_id, applicability=applicability)
    return RuleEvidenceAssessment(True, True, True, behavior_eligible_paths=(path,))


async def _stub_evidence(*_args, **_kwargs):
    return _eligible_assessment()


def _stub_applicability(_assessment, _signals):
    return CareRuleApplicabilityResult("v3-03.16", True, True, (UUID(int=1),))


def test_registry_has_exactly_three_advice_only_rules_and_stable_identity():
    assert CARE_GUIDANCE_VERSION == "v3-03.17"
    assert CARE_GUIDANCE_RULESET_VERSION == "v3-03.17-r1"
    assert len(GUIDANCE_RULES) == 3
    assert [(rule.domain, rule.rule_kind, rule.rule_id, rule.rule_version) for rule in GUIDANCE_RULES] == [
        ("skin_care", "routine_guidance", "care.skin.uv_protection_uvi_3", "v3-03.17-r1"),
        ("skin_care", "routine_guidance", "care.skin.dry_air_moisture_support", "v3-03.17-r1"),
        ("hair_care", "routine_guidance", "care.hair.frequent_heat_styling_protection", "v3-03.17-r1"),
    ]


def test_guidance_set_is_sorted_and_fingerprint_is_content_addressed():
    claim_id = uuid4()
    items = (
        CareGuidanceItem("hair_care", "care.hair.frequent_heat_styling_protection", "v3-03.17-r1", 30, "Heat", "Body", ("heat",), (claim_id,), "v3-03.16"),
        CareGuidanceItem("skin_care", "care.skin.uv_protection_uvi_3", "v3-03.17-r1", 10, "Sun", "Body", ("uv",), (claim_id,), "v3-03.16"),
    )
    guidance = CareGuidanceSet(CARE_GUIDANCE_VERSION, CARE_GUIDANCE_RULESET_VERSION, items)
    assert [item.priority for item in guidance.items] == [10, 30]
    assert guidance.fingerprint == guidance_fingerprint(guidance)
    assert guidance.as_payload()["items"][0]["evidence_claim_ids"] == [str(claim_id)]
    assert guidance.audit_payload()["items"][0].get("title") is None


def test_guidance_rules_do_not_carry_product_selection_or_action_fields():
    assert all(not hasattr(rule, "selected_item_id") for rule in GUIDANCE_RULES)
    assert all(rule.applicability_signals.formulations for rule in GUIDANCE_RULES)
    assert "CLIMATE_RULES" not in inspect.getsource(guidance)


def test_aad_hair_provenance_uses_last_updated_as_revision_not_publication_date():
    source = next(row for row in SOURCE_DEFS if row["source_key"] == "aad.healthy_hair.tips.2024")
    assert source["publication_date"] is None
    assert source["version_or_revision"] == "Last updated 2024-08-12"


@pytest.mark.asyncio
@pytest.mark.parametrize("rule", GUIDANCE_RULES)
async def test_all_three_routine_guidance_identities_resolve(rule):
    await assert_rule_exists(None, domain=rule.domain, rule_kind=rule.rule_kind, rule_id=rule.rule_id, rule_version=rule.rule_version)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain", "rule_id", "rule_version"),
    [("skin_care", "care.skin.uv_protection_uvi_3", "wrong"), ("hair_care", "care.skin.uv_protection_uvi_3", CARE_GUIDANCE_RULESET_VERSION), ("home_care", "care.skin.uv_protection_uvi_3", CARE_GUIDANCE_RULESET_VERSION), ("skin_care", "care.unknown", CARE_GUIDANCE_RULESET_VERSION)],
)
async def test_unknown_wrong_version_domain_and_home_care_fail_closed(domain, rule_id, rule_version):
    with pytest.raises(EvidenceRuleResolutionError):
        await assert_rule_exists(None, domain=domain, rule_kind="routine_guidance", rule_id=rule_id, rule_version=rule_version)


@pytest.mark.asyncio
@pytest.mark.parametrize("uv_index, expected", [(None, False), (2.9, False), (3.0, True), (8.0, True)])
async def test_uv_trigger_boundary(monkeypatch, uv_index, expected):
    monkeypatch.setattr(guidance, "assess_rule_evidence", _stub_evidence)
    monkeypatch.setattr(guidance, "resolve_care_evidence_applicability", _stub_applicability)
    context = _context(uv_index=uv_index)
    result = await guidance.build_care_guidance(None, care_context=context, care_plan=plan_care_routine(context, evaluate_care_context(context)))
    assert any(item.rule_id == "care.skin.uv_protection_uvi_3" for item in result.items) is expected


@pytest.mark.asyncio
async def test_uv_evidence_ineligible_and_applicability_mismatch_omit_guidance(monkeypatch):
    context = _context(uv_index=3.0)
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    async def ineligible(*_args, **_kwargs):
        return RuleEvidenceAssessment(False, False, False)
    monkeypatch.setattr(guidance, "assess_rule_evidence", ineligible)
    assert not (await guidance.build_care_guidance(None, care_context=context, care_plan=plan)).items
    monkeypatch.setattr(guidance, "assess_rule_evidence", _stub_evidence)
    monkeypatch.setattr(guidance, "resolve_care_evidence_applicability", lambda *_args: CareRuleApplicabilityResult("v3-03.16", False, True))
    assert not (await guidance.build_care_guidance(None, care_context=context, care_plan=plan)).items


@pytest.mark.asyncio
@pytest.mark.parametrize("skin_feel, moisture, has_product, expected", [("often_dry_or_tight", "dry", True, True), ("comfortable", "dry", True, False), ("often_dry_or_tight", "normal", True, False), ("often_dry_or_tight", "dry", False, False)])
async def test_dry_air_rule_requires_all_trusted_conditions(monkeypatch, skin_feel, moisture, has_product, expected):
    monkeypatch.setattr(guidance, "assess_rule_evidence", _stub_evidence)
    monkeypatch.setattr(guidance, "resolve_care_evidence_applicability", _stub_applicability)
    products = (_product("beauty", "moisturiser"),) if has_product else ()
    context = _context(skin_feel=skin_feel, moisture_regime=moisture, products=products)
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    before = plan.selected_item_ids
    result = await guidance.build_care_guidance(None, care_context=context, care_plan=plan)
    assert any(item.rule_id == "care.skin.dry_air_moisture_support" for item in result.items) is expected
    assert plan.selected_item_ids == before


@pytest.mark.asyncio
@pytest.mark.parametrize("frequency, expected", [("daily", True), ("frequent", True), ("occasional", False), ("never", False), ("not_sure", False), (None, False)])
async def test_heat_trigger_matrix(monkeypatch, frequency, expected):
    monkeypatch.setattr(guidance, "assess_rule_evidence", _stub_evidence)
    monkeypatch.setattr(guidance, "resolve_care_evidence_applicability", _stub_applicability)
    context = _context(heat_frequency=frequency)
    result = await guidance.build_care_guidance(None, care_context=context, care_plan=plan_care_routine(context, evaluate_care_context(context)))
    assert any(item.rule_id == "care.hair.frequent_heat_styling_protection" for item in result.items) is expected


@pytest.mark.asyncio
async def test_guidance_preserves_authority_and_deterministic_order(monkeypatch):
    product = _product("beauty", "moisturiser")
    context = _context(
        uv_index=3.0,
        moisture_regime="dry",
        skin_feel="often_dry_or_tight",
        heat_frequency="daily",
        wash_frequency="daily",
        products=(product,),
    )
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    before_decisions, before_plan = decision_fingerprint(decisions), routine_plan_fingerprint(plan)
    monkeypatch.setattr(guidance, "assess_rule_evidence", _stub_evidence)
    monkeypatch.setattr(guidance, "resolve_care_evidence_applicability", _stub_applicability)
    wash_frequency = context.hair_facts["care_hair_wash_frequency"].value
    cadence_before = care_cadence.decide_hair_wash_cadence(
        wash_frequency,
        plan_date=context.plan_date,
        last_wash_on=None,
    )
    first = await guidance.build_care_guidance(None, care_context=context, care_plan=plan)
    cadence_after = care_cadence.decide_hair_wash_cadence(
        wash_frequency,
        plan_date=context.plan_date,
        last_wash_on=None,
    )
    second = await guidance.build_care_guidance(None, care_context=context, care_plan=plan)
    assert [(item.priority, item.rule_id) for item in first.items] == sorted((item.priority, item.rule_id) for item in first.items)
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != replace(first, items=first.items[:-1]).fingerprint
    assert decision_fingerprint(decisions) == before_decisions
    assert routine_plan_fingerprint(plan) == before_plan
    assert plan.selected_item_ids == tuple(row.selected_item_id for row in (*plan.skin_slots, *plan.hair_slots) if row.selected_item_id)
    assert cadence_before == cadence_after
    assert cadence_before.as_payload() == cadence_after.as_payload()
    assert care_cadence.hair_wash_cadence_fingerprint(cadence_before) == care_cadence.hair_wash_cadence_fingerprint(cadence_after)


@pytest.mark.asyncio
async def test_guidance_seed_is_idempotent_and_has_exact_reviewed_rows(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        first = (await run_reference_seed(session))["guidance_evidence"]
        second = (await run_reference_seed(session))["guidance_evidence"]
        source_keys = {"who.ultraviolet_radiation.fact_sheet.2022", "aad.dry_skin.relief_tips", "aad.healthy_hair.tips.2024"}
        claim_keys = {"skin.uv_index_3_sun_protection", "skin.dry_air_moisture_support", "hair.frequent_heat_styling_protection"}
        sources = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key.in_(source_keys)))).scalars().all()
        claims = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key.in_(claim_keys)))).scalars().all()
        claim_ids = {claim.id for claim in claims}
        claim_links = (await session.execute(select(EvidenceClaimSource).where(EvidenceClaimSource.claim_id.in_(claim_ids)))).scalars().all()
        rule_links = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.claim_id.in_(claim_ids)))).scalars().all()
        audit = (await session.execute(select(SeedVersionRecord).where(SeedVersionRecord.seed_domain == "evidence_guidance"))).scalar_one()
    assert first == second
    assert (len(sources), len(claims), len(claim_links), len(rule_links)) == (3, 3, 3, 3)
    hair_source = next(row for row in sources if row.source_key == "aad.healthy_hair.tips.2024")
    assert hair_source.publication_date is None
    assert hair_source.version_or_revision == "Last updated 2024-08-12"
    assert all(row.reviewed_at and row.reviewed_by for row in (*claims, *claim_links, *rule_links))
    assert audit.rows_written == 12


@pytest.mark.asyncio
async def test_seeded_guidance_rules_are_behavior_eligible_and_pilot_stays_draft(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        await run_reference_seed(session)
        links = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.rule_kind == "routine_guidance"))).scalars().all()
        assessments = [await assess_rule_evidence(session, domain=link.domain, rule_kind=link.rule_kind, rule_id=link.rule_id, rule_version=link.rule_version) for link in links]
        pilot = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key.in_(("skin.topical_retinoid_pregnancy_regulatory_context", "skin.tretinoin_salicylic_concurrent_irritation_context"))))).scalars().all()
    assert len(links) == 3
    assert all(assessment.provenance_present and assessment.substantive_support_present and assessment.behavior_evidence_eligible for assessment in assessments)
    assert all(claim.review_status == "draft" for claim in pilot)


@pytest.mark.asyncio
async def test_generate_routines_returns_guidance_and_preserves_runtime_authority(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    token, account_id = await registered_supabase_user()
    await _integration_seed(app_client)
    await _integration_product(app_client, token, name="Runtime cleanser", product_type="cleanser")
    await _integration_product(app_client, token, name="Runtime moisturiser", product_type="moisturiser")
    plan_date = date(2026, 8, 12)
    factory = get_sessionmaker()
    async with factory() as session:
        day_context, gathered_context, _ = await routines_service._current_care_decisions(
            session, account_id, plan_date,
        )
    context = replace(
        gathered_context,
        plan_date=plan_date,
        environment=replace(gathered_context.environment, uv_index=3.0),
    )
    decisions = evaluate_care_context(context)
    plan = plan_care_routine(context, decisions)
    selection_plan = routines_service._routine_selection_plan(plan)
    baseline_compiled = compiler.compile_all(
        context.skin_products,
        context.hair_products,
        allergies=context.allergies,
        climate=None,
        today=plan_date,
        eligibility=routines_service._routine_eligibility(decisions),
        selection_plan=selection_plan,
    )
    baseline_morning = next(row for row in baseline_compiled if row.kind == "morning")
    baseline_steps = {step.slot: step.item_id for step in baseline_morning.steps}
    baseline_product_ids = {step.item_id for step in baseline_morning.steps if step.item_id}

    async def current_inputs(*_args, **_kwargs):
        return day_context, context, decisions

    monkeypatch.setattr("app.domains.routines.service._current_care_decisions", current_inputs)
    response = await app_client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"kinds": ["morning"], "as_of": plan_date.isoformat(), "explain": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    guidance_payload = body["care_guidance"]
    assert guidance_payload["guidance_version"] == "v3-03.17"
    assert [item["rule_id"] for item in guidance_payload["items"]] == ["care.skin.uv_protection_uvi_3"]
    assert plan.selected_item_ids == tuple(row.selected_item_id for row in (*plan.skin_slots, *plan.hair_slots) if row.selected_item_id)
    morning = next(row for row in body["routines"] if row["kind"] == "morning")
    runtime_steps = {step["slot"]: step["inventory_item_id"] for step in morning["steps"]}
    assert runtime_steps == baseline_steps
    assert {value for value in runtime_steps.values() if value} == baseline_product_ids
    assert baseline_product_ids

    async with factory() as session:
        run = (await session.execute(
            select(RoutineRecommendationRun).where(RoutineRecommendationRun.account_id == account_id)
        )).scalars().one()
    snapshot = run.inputs["care_snapshot"]
    assert snapshot["snapshot_version"] == "v3-03.18"
    assert snapshot["care_guidance"]["guidance_version"] == "v3-03.17"
    assert snapshot["care_guidance"]["fingerprint"] == guidance_payload["fingerprint"]
    assert run.inputs["care_decision_version"] == decisions.decision_version
    assert run.inputs["care_routine_plan_fingerprint"] == routine_plan_fingerprint(plan)
    assert decision_fingerprint(decisions) == decision_fingerprint(evaluate_care_context(context))


@pytest.mark.asyncio
async def test_routines_today_serves_fresh_guidance_without_daily_plan_or_cadence_mutation(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch
):
    token, account_id = await registered_supabase_user()
    await _integration_seed(app_client)
    await _integration_product(app_client, token, name="Today cleanser", product_type="cleanser")
    await _integration_product(app_client, token, name="Today moisturiser", product_type="moisturiser")
    plan_date = date(2026, 8, 12)
    factory = get_sessionmaker()
    async with factory() as session:
        day_context, gathered_context, _ = await routines_service._current_care_decisions(
            session, account_id, plan_date,
        )
    context = replace(
        gathered_context,
        plan_date=plan_date,
        environment=replace(gathered_context.environment, uv_index=3.0),
    )
    decisions = evaluate_care_context(context)

    async def current_inputs(*_args, **_kwargs):
        return day_context, context, decisions

    monkeypatch.setattr("app.domains.routines.service._current_care_decisions", current_inputs)
    response = await app_client.post(
        "/api/v2/routines/generate",
        headers=auth(token),
        json={"kinds": ["morning"], "as_of": plan_date.isoformat(), "explain": False},
    )
    assert response.status_code == 200, response.text
    from app.domains.planning.models import DailyPlan
    from app.domains.routines.models import RoutineStep

    async with factory() as session:
        before_steps = {
            row.slot: (str(row.inventory_item_id) if row.inventory_item_id else None)
            for row in (await session.execute(select(RoutineStep))).scalars().all()
        }
        before_daily_plans = await session.scalar(select(func.count()).select_from(DailyPlan))
        before_cadence = care_cadence.decide_hair_wash_cadence(
            None, plan_date=plan_date, last_wash_on=None,
        )
        body = await routines_service.routines_today(session, account_id=account_id, on=plan_date)
        after_daily_plans = await session.scalar(select(func.count()).select_from(DailyPlan))
        after_steps = {
            row.slot: (str(row.inventory_item_id) if row.inventory_item_id else None)
            for row in (await session.execute(select(RoutineStep))).scalars().all()
        }
    after_cadence = care_cadence.decide_hair_wash_cadence(None, plan_date=plan_date, last_wash_on=None)
    assert [item["rule_id"] for item in body["care_guidance"]["items"]] == ["care.skin.uv_protection_uvi_3"]
    assert body["care_guidance"]["guidance_version"] == "v3-03.17"
    assert before_cadence == after_cadence
    assert before_daily_plans == after_daily_plans
    assert before_steps == after_steps
