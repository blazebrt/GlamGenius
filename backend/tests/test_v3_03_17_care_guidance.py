from __future__ import annotations

from uuid import uuid4

from app.domains.care.guidance import CARE_GUIDANCE_VERSION, CareGuidanceItem, CareGuidanceSet, guidance_fingerprint
from app.domains.care.guidance_rules import CARE_GUIDANCE_RULESET_VERSION, GUIDANCE_RULES


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
