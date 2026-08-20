"""Pure V3-05.7 Care Purchase read-model coverage."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.bootstrap import seed_inventory_categories
from app.domains.inventory.models import InventoryItem, InventoryValueEvent
from app.domains.purchase import (
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_CHECK_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    check_service,
)
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationInput,
    RecommendationRun,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from app.shared.errors.exceptions import ValidationFailedError
from sqlalchemy import func, select

from tests.conftest import auth


def test_v3_05_7_version_does_not_bump_existing_authorities():
    assert CARE_PURCHASE_CHECK_VERSION == "v3-05.7"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.6"


def _authority(candidate_id: uuid.UUID, account_id: uuid.UUID, category: str, fingerprint: str):
    return {
        "account_id": str(account_id),
        "candidate_id": str(candidate_id),
        "category": category,
        "plan_date": "2026-08-20",
        "assessment_fingerprint": fingerprint,
    }


@pytest.mark.asyncio
async def test_composed_check_reuses_one_assessment_and_aligns_fingerprints(monkeypatch):
    account_id = uuid.uuid4()
    candidate_id = uuid.uuid4()
    candidate = SimpleNamespace(id=candidate_id, category="beauty")
    truth = SimpleNamespace(facts_trusted=True)
    assessment = {
        **_authority(candidate_id, account_id, "beauty", "assessment-a"),
        "dimensions": {"identity_confidence": {"missing_information": []}},
    }
    evidence = {**_authority(candidate_id, account_id, "beauty", "assessment-a"), "projection_fingerprint": "evidence-a"}
    value = {**_authority(candidate_id, account_id, "beauty", "assessment-a"), "value_fingerprint": "value-a"}
    verdict = {
        **_authority(candidate_id, account_id, "beauty", "assessment-a"),
        "evidence_projection_fingerprint": "evidence-a",
        "value_fingerprint": "value-a",
        "verdict": "wait",
    }
    monkeypatch.setattr(check_service.purchase_service, "owned_purchase_candidate", lambda *args, **kwargs: _async(candidate))
    monkeypatch.setattr(check_service.purchase_service, "_require_care", lambda category: None)
    monkeypatch.setattr(check_service, "build_care_candidate_truth", lambda row: truth)
    monkeypatch.setattr(check_service, "serialize_care_candidate_truth", lambda row: {"candidate": {"id": str(candidate_id), "category": "beauty"}})
    monkeypatch.setattr(check_service.purchase_service, "care_purchase_assessment", lambda *args, **kwargs: _async(assessment))
    monkeypatch.setattr(check_service, "resolve_care_purchase_evidence", lambda *args, **kwargs: _async(evidence))
    monkeypatch.setattr(check_service, "resolve_care_purchase_value", lambda *args, **kwargs: _async(value))
    monkeypatch.setattr(check_service, "resolve_care_purchase_verdict", lambda *args, **kwargs: _async(verdict))

    result = await check_service.resolve_care_purchase_check(
        object(), account_id=account_id, account_id_str=str(account_id), candidate_id=candidate_id, plan_date=date(2026, 8, 20)
    )
    assert result["care_purchase_check_version"] == "v3-05.7"
    assert result["strategy"] == "care_purchase"
    assert result["assessment"] is assessment
    assert result["evidence"] is evidence
    assert result["value"] is value
    assert result["verdict"] is verdict


@pytest.mark.asyncio
async def test_draft_candidate_is_rejected_before_read_model_assembly(monkeypatch):
    candidate = SimpleNamespace(id=uuid.uuid4(), category="hair")
    monkeypatch.setattr(check_service.purchase_service, "owned_purchase_candidate", lambda *args, **kwargs: _async(candidate))
    monkeypatch.setattr(check_service.purchase_service, "_require_care", lambda category: None)
    monkeypatch.setattr(check_service, "build_care_candidate_truth", lambda row: SimpleNamespace(facts_trusted=False))
    with pytest.raises(ValidationFailedError):
        await check_service.resolve_care_purchase_check(
            object(), account_id=uuid.uuid4(), account_id_str="account", candidate_id=candidate.id, plan_date=None
        )


async def _async(value):
    return value


async def _seed_db_candidate(
    account_id: uuid.UUID,
    *,
    category: str = "beauty",
    verification_state: str = "confirmed",
    price: Decimal | None = Decimal("1299.00"),
) -> uuid.UUID:
    candidate_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await seed_inventory_categories(session)
        session.add(
            ShoppingCandidate(
                id=candidate_id,
                account_id=account_id,
                source="manual",
                category=category,
                display_name="V3-05.7 Care candidate",
                details={
                    "product_type": "shampoo" if category == "hair" else "cleanser",
                    "purpose": "cleanse",
                    "active_ingredients": ["Niacinamide"],
                },
                verification_state=verification_state,
                extraction_confidence=0.99,
                price=price,
                currency="INR",
            )
        )
        await session.commit()
    return candidate_id


async def _care_read_only_counts(account_id: uuid.UUID) -> dict[str, object]:
    factory = get_sessionmaker()
    async with factory() as session:
        entitlement = await session.scalar(
            select(RecommendationEntitlement).where(
                RecommendationEntitlement.account_id == account_id,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )
        )
        return {
            "evaluations": await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id)),
            "factors": await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id)),
            "decisions": await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id)),
            "runs": await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id)),
            "inputs": await session.scalar(select(func.count(RecommendationInput.id)).join(RecommendationRun).where(RecommendationRun.account_id == account_id)),
            "inventory": await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)),
            "value_events": await session.scalar(select(func.count(InventoryValueEvent.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)),
            "entitlement": None if entitlement is None else (entitlement.included, entitlement.used, entitlement.period_key),
        }


@pytest.mark.asyncio
async def test_runtime_care_check_supports_skin_and_hair_with_aligned_read_only_projections(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    beauty_id = await _seed_db_candidate(account_id, category="beauty")
    hair_id = await _seed_db_candidate(account_id, category="hair")
    before = await _care_read_only_counts(account_id)
    for candidate_id, category in ((beauty_id, "beauty"), (hair_id, "hair")):
        response = await app_client.get(
            f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20",
            headers=auth(token),
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["strategy"] == "care_purchase"
        assert payload["candidate_truth"]["candidate"]["id"] == str(candidate_id)
        assert payload["candidate_truth"]["candidate"]["category"] == category
        assert payload["assessment"]["candidate_id"] == str(candidate_id)
        assert payload["assessment"]["category"] == category
        assert payload["evidence"]["assessment_fingerprint"] == payload["assessment"]["assessment_fingerprint"]
        assert payload["value"]["assessment_fingerprint"] == payload["assessment"]["assessment_fingerprint"]
        assert payload["verdict"]["assessment_fingerprint"] == payload["assessment"]["assessment_fingerprint"]
        assert payload["verdict"]["evidence_projection_fingerprint"] == payload["evidence"]["projection_fingerprint"]
        assert payload["verdict"]["value_fingerprint"] == payload["value"]["value_fingerprint"]
    assert await _care_read_only_counts(account_id) == before


@pytest.mark.asyncio
async def test_runtime_care_check_rejects_draft_until_confirmation_without_persistence(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id, verification_state="draft")
    before = await _care_read_only_counts(account_id)
    rejected = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20",
        headers=auth(token),
    )
    assert rejected.status_code == 422
    confirmed = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm",
        headers=auth(token),
        json={
            "details": {"product_type": "cleanser", "purpose": "cleanse", "active_ingredients": ["Niacinamide"]},
            "price": 1299,
            "currency": "INR",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    checked = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20",
        headers=auth(token),
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["candidate_truth"]["facts_trusted"] is True
    assert await _care_read_only_counts(account_id) == before


@pytest.mark.asyncio
async def test_runtime_confirmation_clears_extracted_price_and_reaches_missing_price_verdict(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id, price=Decimal("1299.00"))
    confirmed = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm",
        headers=auth(token),
        json={"price": None},
    )
    assert confirmed.status_code == 200, confirmed.text
    factory = get_sessionmaker()
    async with factory() as session:
        stored_price = await session.scalar(
            select(ShoppingCandidate.price).where(ShoppingCandidate.id == candidate_id)
        )
    assert stored_price is None
    checked = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-check?on=2026-08-20",
        headers=auth(token),
    )
    assert checked.status_code == 200, checked.text
    payload = checked.json()
    assert payload["value"]["value_context"]["candidate_spend"]["status"] == "missing"
    assert payload["verdict"]["primary_reason_code"] == "candidate_price_missing"
    assert payload["verdict"]["verdict"] == "wait"
