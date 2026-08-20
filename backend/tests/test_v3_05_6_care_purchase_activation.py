"""V3-05.6 Care Purchase activation and strategy-registry coverage."""
from __future__ import annotations

import pytest
from app.domains.purchase import (
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CATEGORY_LABELS,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    STYLE_PURCHASE_CATEGORIES,
    is_active_care_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.contract import boundary_message

from tests.conftest import auth


def test_registry_version_and_states_are_exact():
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.9"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert [strategy.key for strategy in (
        resolve_purchase_strategy("wardrobe"),
        resolve_purchase_strategy("beauty"),
        resolve_purchase_strategy("perfumes"),
        resolve_purchase_strategy("supplements"),
    )] == ["style_purchase", "care_purchase", "fragrance_purchase", "supplement_purchase"]
    assert resolve_purchase_strategy("wardrobe").state == "active"
    assert resolve_purchase_strategy("beauty").state == "active"
    assert resolve_purchase_strategy("hair").state == "active"
    assert resolve_purchase_strategy("perfumes").state == "active"
    assert resolve_purchase_strategy("supplements").state == "prohibited"


def test_active_care_category_helper_is_registry_backed_and_fails_closed():
    assert is_active_care_category("beauty") is True
    assert is_active_care_category("hair") is True
    for category in (*STYLE_PURCHASE_CATEGORIES, "perfumes", "supplements", "unknown", "Beauty", ""):
        assert is_active_care_category(category) is False
    assert is_active_care_category(None) is False


@pytest.mark.asyncio
async def test_strategy_discovery_endpoint_is_ordered_and_uses_customer_labels(
    app_client,
):
    response = await app_client.get("/api/v2/shopping/strategies")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purchase_strategy_registry_version"] == "v3-05.9"
    assert [strategy["key"] for strategy in body["strategies"]] == [
        "style_purchase", "care_purchase", "fragrance_purchase", "supplement_purchase",
    ]
    assert [strategy["state"] for strategy in body["strategies"]] == [
        "active", "active", "active", "prohibited",
    ]
    assert body["strategies"][0]["categories"] == [
        {"key": key, "label": PURCHASE_CATEGORY_LABELS[key]}
        for key in STYLE_PURCHASE_CATEGORIES
    ]
    assert body["strategies"][1]["categories"] == [
        {"key": "beauty", "label": "Skin Care"},
        {"key": "hair", "label": "Hair Care"},
    ]
    assert body["strategies"][2]["categories"] == [{"key": "perfumes", "label": "Perfumes"}]
    assert body["strategies"][3]["categories"] == [{"key": "supplements", "label": "Supplements"}]
    assert all("url" not in strategy and "module" not in strategy for strategy in body["strategies"])


@pytest.mark.asyncio
@pytest.mark.parametrize("category", ["beauty", "hair", "perfumes", "supplements"])
async def test_style_evaluate_keeps_non_style_categories_at_the_routing_boundary(
    app_client, db_clean, registered_supabase_user, monkeypatch, category,
):
    token, _ = await registered_supabase_user()

    def unexpected_style_evaluation(*args, **kwargs):
        raise AssertionError("non-Style category reached the Style evaluator")

    monkeypatch.setattr("app.domains.recommendation.roi.evaluate", unexpected_style_evaluation)
    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={"source": "manual", "item": {"category": category, "display_name": "Test product"}},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert "appearance_roi" not in body
    assert "verdict" not in body
    message = body["detail"]["message"]
    if category in {"beauty", "hair"}:
        assert "dedicated Care purchase flow" in message
        assert "does not evaluate them" in message
    elif category == "perfumes":
        assert "fragrance-specific" in message
    else:
        assert "does not recommend whether to buy supplements" in message


def test_care_boundary_is_routing_copy_not_an_efficacy_or_affordability_claim():
    message = boundary_message("beauty")
    assert "dedicated Care purchase flow" in message
    assert "product, routine, evidence and owned-value context" in message
    assert "Style purchase check does not evaluate" in message
    assert "efficacy" not in message.lower()
    assert "medical" not in message.lower()
    assert "afford" not in message.lower()
