"""V3-04.1 acceptance coverage over the real evidence/database path."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.bootstrap import run as run_seed
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource, RuleEvidenceLink
from app.domains.evidence.nutrition_guidance_seed import (
    NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION,
    SEED_DOMAIN,
    SOURCE_KEY,
)
from app.domains.evidence.service import EvidenceRuleResolutionError, RuleEvidenceAssessment, assert_rule_exists
from app.domains.identity import service as identity
from app.domains.nutrition.evidence_applicability import (
    NutritionApplicabilitySignals,
    resolve_nutrition_evidence_applicability,
)
from app.domains.nutrition.guidance_rules import NUTRITION_GUIDANCE_RULES
from app.domains.nutrition.models import FoodCompositionDataset, FoodNutrientValue, FoodReferenceItem
from app.domains.reference import SeedVersionRecord
from app.domains.routines import nutrition as legacy_nutrition
from app.domains.routines import service
from app.domains.routines.models import HydrationPreference, NutritionPreference
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _seed_account() -> uuid.UUID:
    from app.domains.identity import service as identity

    account_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await identity.register_account(session, account_id)
        session.add(NutritionPreference(account_id=account_id))
        session.add(HydrationPreference(account_id=account_id))
        await session.commit()
    return account_id


async def _set_preferences(account_id: uuid.UUID, *, enabled: bool, focus: list[str] | None = None, hydration: bool = False, hot_only: bool = False) -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        nutrition = (await session.execute(select(NutritionPreference).where(NutritionPreference.account_id == account_id))).scalar_one()
        water = (await session.execute(select(HydrationPreference).where(HydrationPreference.account_id == account_id))).scalar_one()
        nutrition.enabled = enabled
        nutrition.focus_nutrients = focus or []
        water.enabled = hydration
        water.remind_in_hot_weather_only = hot_only
        await session.commit()


async def _suggestions(account_id: uuid.UUID, *, climate: str = "normal") -> dict:
    original = service.shelf.gather

    async def gathered(*args, **kwargs):
        return SimpleNamespace(climate=climate)

    service.shelf.gather = gathered
    try:
        factory = get_sessionmaker()
        async with factory() as session:
            return await service.nutrition_suggestions(session, account_id=account_id)
    finally:
        service.shelf.gather = original


def _ids(payload: dict) -> list[str]:
    return [row["rule_id"] for row in payload["suggestions"]]


async def test_real_service_and_api_trigger_matrix_and_legacy_deactivation(db_clean, monkeypatch, app_client, fake_supabase_user):
    account_id = await _seed_account()

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy nutrition engine must not execute")

    monkeypatch.setattr(legacy_nutrition, "suggestions", forbidden)
    await _set_preferences(account_id, enabled=False)
    assert (await _suggestions(account_id))["suggestions"] == []
    await _set_preferences(account_id, enabled=True)
    assert _ids(await _suggestions(account_id)) == ["nutrition.pattern.balanced_variety"]
    await _set_preferences(account_id, enabled=True, focus=["protein"])
    assert _ids(await _suggestions(account_id)) == ["nutrition.pattern.balanced_variety", "nutrition.pattern.protein_food_first"]
    await _set_preferences(account_id, enabled=True, focus=["iron"])
    assert _ids(await _suggestions(account_id)) == ["nutrition.pattern.balanced_variety"]
    await _set_preferences(account_id, enabled=True, focus=["vitamin_c"])
    assert _ids(await _suggestions(account_id)) == ["nutrition.pattern.balanced_variety"]
    await _set_preferences(account_id, enabled=True, hydration=True)
    assert _ids(await _suggestions(account_id)) == ["nutrition.pattern.balanced_variety", "nutrition.pattern.hydration_context"]
    await _set_preferences(account_id, enabled=True, hydration=True, hot_only=True)
    assert _ids(await _suggestions(account_id, climate="normal")) == ["nutrition.pattern.balanced_variety"]
    assert _ids(await _suggestions(account_id, climate="humid")) == ["nutrition.pattern.balanced_variety", "nutrition.pattern.hydration_context"]
    await _set_preferences(account_id, enabled=True, focus=["protein"], hydration=True)
    assert len((await _suggestions(account_id))["suggestions"]) == 3

    token, _ = fake_supabase_user(user_id=account_id)
    response = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    public = response.json()
    assert public["enabled"] is True and len(public["suggestions"]) <= 3
    assert all({"rule_id", "rule_version", "title", "body", "trigger_codes"} <= set(item) for item in public["suggestions"])
    assert "evidence_claim_ids" not in str(public) and "source_id" not in str(public) and "applicability" not in str(public)
    repeated = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers={"Authorization": f"Bearer {token}"})
    assert repeated.status_code == 200 and repeated.json() == public

    other_account = uuid.uuid4()
    async with get_sessionmaker()() as session:
        await identity.register_account(session, other_account)
        await session.commit()
    other_token, _ = fake_supabase_user(user_id=other_account)
    other_response = await app_client.get("/api/v2/nutrition/appearance-suggestions", headers={"Authorization": f"Bearer {other_token}"})
    assert other_response.status_code == 200 and other_response.json()["enabled"] is False and other_response.json()["suggestions"] == []


async def test_seed_is_idempotent_and_ifct_remains_metadata_only(db_clean):
    factory = get_sessionmaker()
    async with factory() as session:
        first = await run_seed(session)
        second = await run_seed(session)
        source_count = await session.scalar(select(func.count()).select_from(EvidenceSource).where(EvidenceSource.source_key == SOURCE_KEY))
        claim_count = await session.scalar(select(func.count()).select_from(EvidenceClaim).where(EvidenceClaim.claim_key.like("nutrition.%")))
        claim_source_count = await session.scalar(select(func.count()).select_from(EvidenceClaimSource).join(EvidenceClaim).where(EvidenceClaim.claim_key.like("nutrition.%")))
        link_count = await session.scalar(select(func.count()).select_from(RuleEvidenceLink).where(RuleEvidenceLink.domain == "nutrition", RuleEvidenceLink.rule_kind == "nutrition_context"))
        audit = (await session.execute(select(SeedVersionRecord).where(SeedVersionRecord.seed_domain == SEED_DOMAIN, SeedVersionRecord.seed_version == NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION))).scalar_one()
        datasets = (await session.execute(select(FoodCompositionDataset))).scalars().all()
        items = (await session.execute(select(FoodReferenceItem))).scalars().all()
        values = (await session.execute(select(FoodNutrientValue))).scalars().all()
    expected = {"seed_version": NUTRITION_GUIDANCE_EVIDENCE_SEED_VERSION, "sources": 0, "claims": 3, "claim_source_links": 3, "rule_links": 3, "rows_written": 9}
    assert first["nutrition_guidance_evidence"] == second["nutrition_guidance_evidence"] == expected
    assert source_count == 1 and claim_count == claim_source_count == link_count == 3 and audit.rows_written == 9
    assert datasets and datasets[0].rights_status == "restricted_reference" and datasets[0].import_status == "metadata_only"
    assert items == [] and values == []


async def test_evidence_fail_closed_and_exact_resolution(db_clean):
    account_id = await _seed_account()
    await _set_preferences(account_id, enabled=True, focus=["protein"], hydration=True)
    factory = get_sessionmaker()
    async with factory() as session:
        links = (await session.execute(select(RuleEvidenceLink).where(RuleEvidenceLink.domain == "nutrition"))).scalars().all()
        assert len(links) == 3
        removed = next(link for link in links if link.rule_id == "nutrition.pattern.balanced_variety")
        removed_id = removed.rule_id
        await session.delete(removed)
        await session.commit()
    payload = await _suggestions(account_id)
    assert len(payload["suggestions"]) == 2 and removed_id not in _ids(payload)
    # Each reviewed provenance layer is independently required.  Mutate one
    # layer at a time and restore it so the assertions exercise the same
    # account/rules without multiplying expensive database bootstrap work.
    async with factory() as session:
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == SOURCE_KEY))).scalar_one()
        original_source_status = source.status
        source.status = "retired"
        await session.commit()
    assert (await _suggestions(account_id))["suggestions"] == []
    async with factory() as session:
        source = (await session.execute(select(EvidenceSource).where(EvidenceSource.source_key == SOURCE_KEY))).scalar_one()
        source.status = original_source_status
        await session.commit()

    async with factory() as session:
        claim = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == "nutrition.protein_food_first"))).scalar_one()
        original_review_status = claim.review_status
        claim.review_status = "draft"
        await session.commit()
    assert "nutrition.pattern.protein_food_first" not in _ids(await _suggestions(account_id))
    async with factory() as session:
        claim = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == "nutrition.protein_food_first"))).scalar_one()
        claim.review_status = original_review_status
        await session.commit()

    async with factory() as session:
        claim = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == "nutrition.protein_food_first"))).scalar_one()
        original_structured_value = claim.structured_value
        claim.structured_value = {"behavior_applicability": {"schema_version": "v3-03.15", "jurisdictions": "india"}}
        await session.commit()
    assert "nutrition.pattern.protein_food_first" not in _ids(await _suggestions(account_id))
    async with factory() as session:
        claim = (await session.execute(select(EvidenceClaim).where(EvidenceClaim.claim_key == "nutrition.protein_food_first"))).scalar_one()
        claim.structured_value = original_structured_value
        await session.commit()

    with pytest.raises(EvidenceRuleResolutionError):
        async with factory() as session:
            await assert_rule_exists(session, domain="nutrition", rule_kind="nutrition_context", rule_id="nutrition.unknown", rule_version="v1")
    with pytest.raises(EvidenceRuleResolutionError):
        async with factory() as session:
            await assert_rule_exists(session, domain="nutrition", rule_kind="nutrition_context", rule_id=NUTRITION_GUIDANCE_RULES[1].rule_id, rule_version="wrong")
    with pytest.raises(EvidenceRuleResolutionError):
        async with factory() as session:
            await assert_rule_exists(session, domain="skin_care", rule_kind="nutrition_context", rule_id=NUTRITION_GUIDANCE_RULES[1].rule_id, rule_version="v1")


def test_public_copy_and_runtime_modules_are_bounded():
    forbidden = ("diagnose", "diagnosis", "deficient", "deficiency", "treat", "treatment", "cure", "prescribe", "prescription", "mg/day", "grams/day", "g/day", "litres/day", "calorie target", "bmi target", "weight loss", "weight gain")
    from app.domains.routines.safety import NUTRITION_DISCLAIMER

    visible = " ".join([NUTRITION_DISCLAIMER, *(row.title + " " + row.body for row in NUTRITION_GUIDANCE_RULES)]).lower()
    assert "does not set a litre target or treat hydration as a diagnosis" in visible
    # The exact hydration safety sentence is intentionally required public
    # copy; exclude that boundary sentence from the generic forbidden-term
    # sweep rather than weakening the production wording.
    visible = visible.replace("or treat hydration as a diagnosis", "")
    assert not any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", visible) for term in forbidden)
    guidance_dir = Path(__file__).parents[1] / "app" / "domains" / "nutrition"
    source = "\n".join(path.read_text(encoding="utf-8") for path in guidance_dir.glob("guidance*.py"))
    assert "FoodReferenceItem" not in source and "FoodNutrientValue" not in source
    assert "IFCT" not in source and "RDA" not in source


def test_applicability_fails_closed_for_malformed_or_unmatched_signals():
    assessment = RuleEvidenceAssessment(False, False, False)
    malformed = NutritionApplicabilitySignals("india", ("general_population",), ("food_pattern_guidance",), ("nutrition_enabled",))
    assert not resolve_nutrition_evidence_applicability(assessment, malformed).applicable
