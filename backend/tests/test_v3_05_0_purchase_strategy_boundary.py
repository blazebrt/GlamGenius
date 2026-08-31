"""Authoritative V3-05.0 purchase strategy and quality-contract coverage."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.domains.purchase.contract import (
    CARE_PURCHASE_CATEGORIES,
    FRAGRANCE_PURCHASE_CATEGORIES,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PRODUCT_QUALITY_DIMENSIONS,
    PURCHASE_CATEGORY_LABELS,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_PROHIBITED_CATEGORIES,
    PURCHASE_STRATEGY_REGISTRY,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    STYLE_PURCHASE_CATEGORIES,
    resolve_purchase_strategy,
)
from app.domains.recommendation import explanation as explanation_stage
from app.domains.recommendation import roi
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.recommendation.schemas import ExtractedShoppingItem
from app.shared.database.registry import Base
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

LINEN_SHIRT = {
    "category": "wardrobe",
    "display_name": "Ivory Linen Shirt",
    "brand": "Fable",
    "subcategory": "shirt",
    "colour": "ivory",
    "fabric": "linen",
    "fit": "relaxed",
    "formality": "smart_casual",
    "occasion_tags": ["work", "casual_day"],
    "season_tags": ["summer"],
}


def test_contract_versions_categories_labels_and_quality_dimensions_are_frozen():
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.9"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert STYLE_PURCHASE_CATEGORIES == ("wardrobe", "shoes", "accessories")
    assert CARE_PURCHASE_CATEGORIES == ("beauty", "hair")
    assert FRAGRANCE_PURCHASE_CATEGORIES == ("perfumes",)
    assert PURCHASE_PROHIBITED_CATEGORIES == ("supplements",)
    assert dict(PURCHASE_CATEGORY_LABELS) == {
        "wardrobe": "Wardrobe", "shoes": "Shoes", "accessories": "Accessories",
        "beauty": "Skin Care", "hair": "Hair Care", "perfumes": "Perfumes",
        "supplements": "Supplements",
    }
    assert PRODUCT_QUALITY_DIMENSIONS == (
        "identity_confidence", "role_utility", "redundancy", "compatibility",
        "evidence_support", "value_context",
    )
    public_contract = repr(PURCHASE_STRATEGY_REGISTRY) + repr(PRODUCT_QUALITY_DIMENSIONS)
    for forbidden in ("quality_score", "product_score", "brand_score", "prestige_score", "attractiveness_score"):
        assert forbidden not in public_contract


def test_strategy_registry_is_exact_and_unknown_fails_closed():
    expected = {
        "wardrobe": ("style_purchase", "active"), "shoes": ("style_purchase", "active"),
        "accessories": ("style_purchase", "active"), "beauty": ("care_purchase", "active"),
        "hair": ("care_purchase", "active"), "perfumes": ("fragrance_purchase", "active"),
        "supplements": ("supplement_purchase", "prohibited"),
    }
    assert {category: (resolve_purchase_strategy(category).key, resolve_purchase_strategy(category).state)
            for category in expected} == expected
    assert resolve_purchase_strategy("Beauty") is None
    assert resolve_purchase_strategy("furniture") is None


def test_style_roi_is_unchanged_and_guarded_before_factor_work(monkeypatch):
    assert roi.ROI_VERSION == "appearance-roi-v1"
    assert roi.BUY_THRESHOLD == 0.65
    assert roi.WAIT_THRESHOLD == 0.45
    assert roi.FACTOR_WEIGHTS == {
        "new_combinations": 0.22, "category_gap": 0.16, "duplicate_penalty": 0.16,
        "occasion_relevance": 0.12, "colour_compatibility": 0.12,
        "climate_suitability": 0.08, "expected_use_frequency": 0.08,
        "versatility": 0.06, "price_context": 0.10,
    }
    assert roi.FACTOR_LABELS == {
        "new_combinations": "New outfit combinations", "category_gap": "Fills a gap",
        "duplicate_penalty": "How different it is from what you own",
        "occasion_relevance": "Occasions it covers", "colour_compatibility": "Colour fit with your wardrobe",
        "climate_suitability": "Suits your climate", "expected_use_frequency": "How often you would wear it",
        "versatility": "Versatility", "price_context": "Price against expected wears",
    }
    for category in STYLE_PURCHASE_CATEGORIES:
        result = roi.evaluate(roi.Candidate(category=category, display_name="test item"), [])
        assert result.version == "appearance-roi-v1"
        assert result.factors

    def unexpected_factor(*args, **kwargs):
        raise AssertionError("unsupported category reached a Style factor")

    monkeypatch.setattr(roi.Candidate, "as_details", unexpected_factor)
    for category in (*CARE_PURCHASE_CATEGORIES, *FRAGRANCE_PURCHASE_CATEGORIES, *PURCHASE_PROHIBITED_CATEGORIES):
        with pytest.raises(ValueError):
            roi.evaluate(roi.Candidate(category=category, display_name="unsupported"), [])


@pytest.mark.asyncio
async def test_roi_model_publishes_style_scope_only(app_client, db_clean, registered_supabase_user):
    token, _ = await registered_supabase_user()
    response = await app_client.get("/api/v2/shopping/roi-model", headers=auth(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supported_categories"] == ["wardrobe", "shoes", "accessories"]
    assert body["strategy"] == "style_purchase"
    assert body["purchase_strategy_registry_version"] == "v3-05.9"
    assert body["formula"].startswith("roi = sum(factor value")
    assert {row["key"] for row in body["factors"]} == set(roi.FACTOR_WEIGHTS)


async def _counts(uid):
    factory = get_sessionmaker()
    async with factory() as session:
        return {
            "runs": await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == uid)),
            "candidates": await session.scalar(select(func.count(ShoppingCandidate.id)).where(ShoppingCandidate.account_id == uid)),
            "evaluations": await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == uid)),
            "factors": await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == uid)),
            "decisions": await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == uid)),
            "entitlement": await session.scalar(select(RecommendationEntitlement.used).where(
                RecommendationEntitlement.account_id == uid,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )),
        }


async def _evaluate_style(client, token, *, mutation_id: str):
    return await client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={
            "source": "manual",
            "item": dict(LINEN_SHIRT),
            "currency": "INR",
            "client_mutation_id": mutation_id,
        },
    )


@pytest.mark.asyncio
async def test_valid_style_replay_returns_same_evaluation_without_side_effects(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    token, uid = await registered_supabase_user()
    first = await _evaluate_style(app_client, token, mutation_id="v3-05-replay")
    assert first.status_code == 200, first.text
    first_body = first.json()
    before = await _counts(uid)

    replay = await _evaluate_style(app_client, token, mutation_id="v3-05-replay")
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["id"] == first_body["id"]
    assert replay_body["replayed"] is True
    assert await _counts(uid) == before


@pytest.mark.parametrize("category", ["beauty", "hair", "perfumes", "supplements"])
@pytest.mark.asyncio
async def test_unsupported_manual_same_key_cannot_replay_style_result(
    app_client, db_clean, registered_supabase_user, fake_provider, monkeypatch, category,
):
    token, uid = await registered_supabase_user()
    first = await _evaluate_style(app_client, token, mutation_id="v3-05-policy-bypass")
    assert first.status_code == 200, first.text
    before = await _counts(uid)

    def unexpected_roi(*args, **kwargs):
        raise AssertionError("unsupported purchase category invoked Style ROI")

    monkeypatch.setattr(roi, "evaluate", unexpected_roi)
    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={
            "source": "manual",
            "item": {"category": category, "display_name": "Unsupported product"},
            "client_mutation_id": "v3-05-policy-bypass",
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert "verdict" not in body
    assert "appearance_roi" not in body
    message = body["detail"]["message"]
    if category in {"beauty", "hair"}:
        assert "Skin Care" in message and "Hair Care" in message
    elif category == "perfumes":
        assert "fragrance-specific" in message
    else:
        assert "does not recommend whether to buy supplements" in message
    assert await _counts(uid) == before


@pytest.mark.asyncio
async def test_different_screenshot_cannot_replay_old_style_result(
    app_client, db_clean, registered_supabase_user, monkeypatch, media_root,
):
    from tests.conftest import png_bytes

    token, uid = await registered_supabase_user()
    assets = []
    for name in ("product-a.png", "product-b.png"):
        uploaded = await app_client.post(
            "/api/v2/media/upload",
            headers=auth(token),
            files={"file": (name, png_bytes(), "image/png")},
        )
        assert uploaded.status_code in (200, 201), uploaded.text
        assets.append(uploaded.json()["id"])

    extraction_calls = 0

    async def extract_style(*args, **kwargs):
        nonlocal extraction_calls
        extraction_calls += 1
        return (
            ExtractedShoppingItem(
                category="wardrobe", display_name="Linen shirt", confidence=0.95,
                photo_quality_notes="The item is readable.",
            ),
            None, "fake-model", "v3-test", "v3-test",
        )

    monkeypatch.setattr(explanation_stage, "extract_shopping_item", extract_style)
    first = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={"source": "screenshot", "media_asset_id": assets[0], "client_mutation_id": "v3-05-photo"},
    )
    assert first.status_code == 200, first.text
    before = await _counts(uid)

    second = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={"source": "screenshot", "media_asset_id": assets[1], "client_mutation_id": "v3-05-photo"},
    )
    assert second.status_code == 422, second.text
    assert second.json()["detail"]["field"] == "client_mutation_id"
    assert "different shopping check" in second.json()["detail"]["message"]
    assert "verdict" not in second.json()
    assert extraction_calls == 1
    assert await _counts(uid) == before


@pytest.mark.parametrize("category", ["beauty", "hair", "perfumes", "supplements"])
@pytest.mark.asyncio
async def test_inactive_and_prohibited_manual_categories_fail_before_side_effects(
    app_client, db_clean, registered_supabase_user, monkeypatch, category,
):
    token, uid = await registered_supabase_user()
    before = await _counts(uid)

    def unexpected_roi(*args, **kwargs):
        raise AssertionError("unsupported purchase category invoked Style ROI")

    monkeypatch.setattr(roi, "evaluate", unexpected_roi)
    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={"source": "manual", "item": {"category": category, "display_name": "Test product"}},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert detail["retryable"] is False
    assert detail["field"] == "item.category"
    assert detail["message"]
    if category == "perfumes":
        assert "fragrance-specific" in detail["message"]
    if category == "supplements":
        assert "does not recommend whether to buy supplements" in detail["message"]
    after = await _counts(uid)
    assert after == before


@pytest.mark.asyncio
async def test_screenshot_inactive_category_fails_before_purchase_persistence(
    app_client, db_clean, registered_supabase_user, monkeypatch, media_root,
):
    """Extraction provenance may exist, but an extracted Care item gets no purchase path."""
    from tests.conftest import png_bytes

    token, uid = await registered_supabase_user()
    upload = await app_client.post(
        "/api/v2/media/upload",
        headers=auth(token),
        files={"file": ("product.png", png_bytes(), "image/png")},
    )
    assert upload.status_code in (200, 201), upload.text

    async def extract_care(*args, **kwargs):
        return (
            ExtractedShoppingItem(
                category="beauty", display_name="Skin cleanser", confidence=0.95,
                photo_quality_notes="The label is readable.",
            ),
            None, "fake-model", "v3-test", "v3-test",
        )

    monkeypatch.setattr(explanation_stage, "extract_shopping_item", extract_care)
    before = await _counts(uid)
    response = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(token),
        json={"source": "screenshot", "media_asset_id": upload.json()["id"]},
    )
    assert response.status_code == 422, response.text
    assert "Skin Care" in response.json()["detail"]["message"]
    after = await _counts(uid)
    assert after == before


def test_purchase_metadata_has_one_persistence_engine():
    purchase_tables = {
        table.name
        for table in Base.metadata.tables.values()
        if "purchase" in table.name or table.name == "shopping_candidates"
    }
    assert purchase_tables == {
        "shopping_candidates",
        "purchase_evaluations",
        "purchase_evaluation_factors",
        "purchase_decisions",
    }


def test_purchase_runtime_has_no_sales_or_merchant_imports():
    runtime_root = Path(__file__).parents[1] / "app" / "domains" / "purchase"
    banned = {
        "payments", "payment", "billing", "checkout", "affiliate", "merchant",
        "marketplace", "cart", "amazon", "flipkart", "nykaa",
    }
    for path in runtime_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.append(node.module)
        assert not any(
            any(term in name.lower().split(".") for term in banned)
            for name in imported_names
        ), path
