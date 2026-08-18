"""V3-05.1 Care purchase candidate facts and review boundary."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from app.domains.inventory.models import InventoryAttribute, InventoryItem
from app.domains.purchase.candidate_truth import (
    CARE_CANDIDATE_DETAIL_KEYS,
    resolve_care_slot,
)
from app.domains.purchase.contract import (
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
)
from app.domains.purchase.schemas import CarePurchaseItemInput
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.routines.ontology import slot_for_product_type
from app.domains.routines.parser import parse_product, unmatched_terms
from app.shared.database.registry import Base
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB

from tests.conftest import auth

CARE_ITEM = {
    "category": "beauty",
    "display_name": "Calm Cleanser",
    "brand": "Clear Day",
    "subcategory": "face wash",
    "details": {
        "product_type": "cleanser",
        "size": "150 ml",
        "purpose": "A gentle daily cleansing step.",
        "ingredients_text": "Water, Niacinamide, mystery-label-term",
        "active_ingredients": ["niacinamide"],
    },
    "price": "799.00",
    "currency": "INR",
    "product_url": "https://example.invalid/product",
}


async def _counts(account_id):
    factory = get_sessionmaker()
    async with factory() as session:
        return {
            "candidates": await session.scalar(select(func.count(ShoppingCandidate.id)).where(ShoppingCandidate.account_id == account_id)),
            "inventory": await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)),
            "attributes": await session.scalar(select(func.count(InventoryAttribute.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "runs": await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id)),
            "evaluations": await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id)),
            "factors": await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id)),
            "decisions": await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id)),
            "entitlement": await session.scalar(select(RecommendationEntitlement.used).where(
                RecommendationEntitlement.account_id == account_id,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )),
        }


def test_versions_and_exact_prospective_detail_subset():
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert {
        "product_type", "size", "purpose", "ingredients_text", "active_ingredients",
    } == CARE_CANDIDATE_DETAIL_KEYS
    with pytest.raises(ValueError):
        CarePurchaseItemInput(
            category="beauty",
            display_name="Owned-shaped input",
            details={"product_type": "serum", "opened_date": "2026-01-01"},
        )


def test_canonical_slot_and_ingredient_parser_authority():
    for category, product_type, slot in (
        ("beauty", "cleanser", "cleanser"),
        ("beauty", "sunscreen", "sunscreen"),
        ("hair", "shampoo", "shampoo"),
        ("hair", "conditioner", "conditioner"),
    ):
        assert resolve_care_slot(category, {"product_type": product_type}, None, "unknown") == slot
        assert slot_for_product_type(product_type, category) == slot
    parsed = parse_product({"ingredients_text": "Nicotinamide", "active_ingredients": ["vitamin b3"]})
    assert {row.key for row in parsed} == {"niacinamide"}
    assert "mystery ingredient" in unmatched_terms("mystery ingredient")


def test_candidate_truth_does_not_define_a_second_product_mapping():
    path = Path(__file__).parents[1] / "app" / "domains" / "purchase" / "candidate_truth.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert not any("PRODUCT_TYPE" in name or "SLOT_BY" in name for name in assigned_names)


def test_one_purchase_candidate_persistence_family_and_details_column():
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
    column = ShoppingCandidate.__table__.c.details
    assert isinstance(column.type, JSONB)
    assert column.nullable is False
    assert column.server_default is not None


@pytest.mark.asyncio
async def test_manual_skin_capture_is_truth_only(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    before = await _counts(account_id)
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect",
        headers=auth(token),
        json={"source": "manual", "item": CARE_ITEM, "client_mutation_id": "care-manual-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["candidate_truth_version"] == "v3-05.1"
    assert body["candidate"]["details"] == CARE_ITEM["details"]
    assert body["candidate"]["verification_state"] == "user_declared"
    assert body["facts_trusted"] is True
    assert body["review_required"] is False
    assert body["care_slot"] == "cleanser"
    after = await _counts(account_id)
    assert after["candidates"] == before["candidates"] + 1
    for key in ("inventory", "attributes", "runs", "evaluations", "factors", "decisions", "entitlement"):
        assert after[key] == before[key]
    assert "unrecognised_ingredient:mystery-label-term" in body["missing_information"]


@pytest.mark.asyncio
async def test_manual_hair_capture_uses_canonical_slot(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    item = {
        "category": "hair", "display_name": "Soft Conditioner", "brand": "Care Day",
        "details": {"product_type": "conditioner", "purpose": "Adds slip."},
    }
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect",
        headers=auth(token),
        json={"source": "manual", "item": item, "client_mutation_id": "hair-manual-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["care_slot"] == "conditioner"
    assert (await _counts(account_id))["inventory"] == 0


@pytest.mark.asyncio
async def test_purpose_is_metadata_not_efficacy_authority(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    item = {**CARE_ITEM, "details": {**CARE_ITEM["details"], "purpose": "Cures every skin concern instantly."}}
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "manual", "item": item},
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["details"]["purpose"] == item["details"]["purpose"]
    counts = await _counts(account_id)
    assert counts["evaluations"] == counts["factors"] == counts["decisions"] == 0


@pytest.mark.asyncio
async def test_ai_draft_stays_untrusted_at_high_confidence_and_confirm_preserves_provenance(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    from tests.conftest import png_bytes

    token, account_id = await registered_supabase_user()
    fake_provider.text = (
        '{"category":"beauty","display_name":"Glow Serum","brand":"Visible Brand",'
        '"subcategory":"serum","details":{"product_type":"serum","purpose":"Visible hydration",'
        '"ingredients_text":"Water, Niacinamide","active_ingredients":["niacinamide"]},'
        '"price":899,"currency":"INR","confidence":0.99,"uncertain_fields":["purpose"],'
        '"photo_quality_notes":"The label is readable."}'
    )
    upload = await app_client.post(
        "/api/v2/media/upload", headers=auth(token),
        files={"file": ("serum.png", png_bytes(), "image/png")},
    )
    assert upload.status_code in (200, 201), upload.text
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"screenshot", "media_asset_id":upload.json()["id"], "client_mutation_id":"draft-1"},
    )
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["candidate"]["verification_state"] == "draft"
    assert draft["facts_trusted"] is False
    assert draft["review_required"] is True
    assert draft["candidate"]["extraction_confidence"] == 0.99
    candidate_id = draft["candidate"]["id"]
    confirmed = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm", headers=auth(token),
        json={"details": {"product_type":"serum", "purpose":"User-confirmed label purpose", "ingredients_text":"Water, Niacinamide", "active_ingredients":["niacinamide"]}},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["candidate"]["id"] == candidate_id
    assert body["candidate"]["verification_state"] == "confirmed"
    assert body["facts_trusted"] is True
    assert body["review_required"] is False
    assert body["candidate"]["uncertain_fields"] == []
    assert body["candidate"]["ai_run_id"]
    assert body["candidate"]["model_version"] == fake_provider.model
    assert body["candidate"]["prompt_version"] == "v3-05.1"
    assert body["candidate"]["schema_version"] == "v3-05.1"
    assert (await _counts(account_id))["inventory"] == 0


@pytest.mark.asyncio
async def test_manual_capture_idempotency_and_mismatched_key(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    first = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"manual", "item":CARE_ITEM, "client_mutation_id":"manual-replay"},
    )
    second = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"manual", "item":CARE_ITEM, "client_mutation_id":"manual-replay"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["candidate"]["id"] == second.json()["candidate"]["id"]
    assert (await _counts(account_id))["candidates"] == 1
    different = {**CARE_ITEM, "display_name": "Different Product"}
    mismatch = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"manual", "item":different, "client_mutation_id":"manual-replay"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["field"] == "client_mutation_id"
    assert (await _counts(account_id))["candidates"] == 1


@pytest.mark.asyncio
async def test_screenshot_idempotency_checks_media_before_second_extraction(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    from tests.conftest import png_bytes

    token, account_id = await registered_supabase_user()
    fake_provider.text = '{"category":"hair","display_name":"Wash","details":{"product_type":"shampoo"},"confidence":0.8,"photo_quality_notes":"clear label"}'
    assets = []
    for name in ("a.png", "b.png"):
        upload = await app_client.post(
            "/api/v2/media/upload", headers=auth(token),
            files={"file": (name, png_bytes(), "image/png")},
        )
        assert upload.status_code in (200, 201), upload.text
        assets.append(upload.json()["id"])
    first = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"screenshot", "media_asset_id":assets[0], "client_mutation_id":"photo-replay"},
    )
    assert first.status_code == 200, first.text
    calls = fake_provider.calls
    same = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"screenshot", "media_asset_id":assets[0], "client_mutation_id":"photo-replay"},
    )
    assert same.status_code == 200
    assert same.json()["candidate"]["id"] == first.json()["candidate"]["id"]
    assert fake_provider.calls == calls
    different = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source":"screenshot", "media_asset_id":assets[1], "client_mutation_id":"photo-replay"},
    )
    assert different.status_code == 422
    assert different.json()["detail"]["field"] == "client_mutation_id"
    assert fake_provider.calls == calls
    assert (await _counts(account_id))["candidates"] == 1


@pytest.mark.asyncio
async def test_account_isolation_and_noncare_boundaries(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root,
):
    owner_token, owner_id = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    created = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(owner_token),
        json={"source":"manual", "item":CARE_ITEM},
    )
    assert created.status_code == 200
    candidate_id = created.json()["candidate"]["id"]
    assert (await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}", headers=auth(intruder_token))).status_code == 404
    assert (await app_client.post(f"/api/v2/shopping/candidates/{candidate_id}/confirm", headers=auth(intruder_token), json={})).status_code == 404

    for category in ("wardrobe", "shoes", "accessories", "perfumes", "supplements"):
        response = await app_client.post(
            "/api/v2/shopping/candidates/inspect", headers=auth(owner_token),
            json={"source":"manual", "item":{"category":category, "display_name":"Not Care"}},
        )
        assert response.status_code == 422
        message = response.json()["detail"]["message"].lower()
        assert message
        if category in {"wardrobe", "shoes", "accessories"}:
            assert "style purchase" in message
        elif category == "perfumes":
            assert "fragrance-specific" in message
        else:
            assert "does not recommend whether to buy supplements" in message
    assert (await _counts(owner_id))["candidates"] == 1


def test_purchase_runtime_has_no_sales_or_merchant_imports():
    runtime_root = Path(__file__).parents[1] / "app" / "domains" / "purchase"
    banned = {"payments", "payment", "billing", "checkout", "affiliate", "merchant", "marketplace", "cart", "amazon", "flipkart", "nykaa"}
    for path in runtime_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(any(term in name.lower().split(".") for term in banned) for name in imported), path
