"""Pure V3-04.1 registry, trigger, and public-contract coverage."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from app.domains.evidence.service import EvidenceRuleResolutionError, RuleEvidenceAssessment
from app.domains.nutrition.evidence_applicability import (
    NutritionApplicabilitySignals,
    resolve_nutrition_evidence_applicability,
)
from app.domains.nutrition.guidance import build_nutrition_guidance, public_nutrition_guidance
from app.domains.nutrition.guidance_rules import NUTRITION_GUIDANCE_RULES
from app.domains.routines import nutrition as legacy_nutrition


def test_registry_has_exactly_three_v3_04_1_rules() -> None:
    assert [row.rule_id for row in NUTRITION_GUIDANCE_RULES] == [
        "nutrition.pattern.balanced_variety",
        "nutrition.pattern.protein_food_first",
        "nutrition.pattern.hydration_context",
    ]
    assert all(row.domain == "nutrition" and row.rule_kind == "nutrition_context" for row in NUTRITION_GUIDANCE_RULES)


@pytest.mark.asyncio
async def test_guidance_is_opt_in_and_deterministic_without_ifct(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_ids = tuple(uuid.uuid4() for _ in NUTRITION_GUIDANCE_RULES)

    async def assessed(*args, **kwargs):
        from app.domains.evidence.applicability import EvidenceApplicability
        from app.domains.evidence.service import BehaviorEligibleEvidencePath

        index = next(i for i, row in enumerate(NUTRITION_GUIDANCE_RULES) if row.rule_id == kwargs["rule_id"])
        rule = NUTRITION_GUIDANCE_RULES[index]
        signals = rule.applicability_signals
        path = BehaviorEligibleEvidencePath(claim_ids[index], EvidenceApplicability("v3-03.15", signals.jurisdictions, signals.populations, signals.formulations, signals.usage_contexts))
        return RuleEvidenceAssessment(True, True, True, ("supports",), (claim_ids[index],), (path,))

    monkeypatch.setattr("app.domains.nutrition.guidance.assess_rule_evidence", assessed)
    session = object()
    disabled = await build_nutrition_guidance(session, nutrition_enabled=False, protein_focus=True, hydration_enabled=True, hot_weather=True, hot_weather_only=False)
    assert disabled.items == ()

    enabled = await build_nutrition_guidance(session, nutrition_enabled=True, protein_focus=True, hydration_enabled=True, hot_weather=True, hot_weather_only=False)
    payload = public_nutrition_guidance(enabled)
    assert [row["rule_id"] for row in payload["suggestions"]] == [row.rule_id for row in NUTRITION_GUIDANCE_RULES]
    assert all("evidence_claim_ids" not in row for row in payload["suggestions"])

    legacy = Path(__file__).parents[1] / "app" / "domains" / "routines" / "nutrition.py"
    assert "NUTRIENT_RULES" in legacy.read_text(encoding="utf-8")
    assert legacy_nutrition.NUTRIENT_RULES


def test_applicability_fails_closed_for_malformed_or_unmatched_signals() -> None:
    assessment = RuleEvidenceAssessment(False, False, False)
    malformed = NutritionApplicabilitySignals("india", ("general_population",), ("food_pattern_guidance",), ("nutrition_enabled",))
    assert not resolve_nutrition_evidence_applicability(assessment, malformed).applicable


@pytest.mark.asyncio
async def test_nutrition_rule_resolution_is_narrow() -> None:
    class NoopSession:
        pass

    from app.domains.evidence.service import assert_rule_exists

    with pytest.raises(EvidenceRuleResolutionError):
        await assert_rule_exists(NoopSession(), domain="nutrition", rule_kind="nutrition_context", rule_id="nutrition.unknown", rule_version="v1")
