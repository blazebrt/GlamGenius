"""V3-05.9 Fragrance Purchase strategy contract and deterministic policy."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.domains.inventory.models import InventoryItem
from app.domains.purchase.contract import (
    CARE_PURCHASE_CHECK_VERSION,
    CARE_PURCHASE_VERDICT_VERSION,
    FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    FRAGRANCE_PURCHASE_CHECK_VERSION,
    FRAGRANCE_PURCHASE_VERDICT_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    is_active_care_category,
    is_active_fragrance_category,
    resolve_purchase_strategy,
)
from app.domains.purchase.fragrance_check import OwnedFragrance, evaluate_fragrance_purchase
from app.domains.purchase.fragrance_truth import (
    FRAGRANCE_CANDIDATE_DETAIL_KEYS,
    build_fragrance_candidate_truth,
    validate_fragrance_candidate_details,
)
from app.domains.purchase.schemas import CarePurchaseCandidateConfirm, ExtractedFragranceCandidate
from app.domains.purchase.service import _apply_candidate_corrections
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
from sqlalchemy import func, select

from tests.conftest import auth


def _candidate(*, price=Decimal("1200"), **details):
    return SimpleNamespace(
        id=uuid4(), category="perfumes", display_name="Rain Garden", brand="House",
        details=details, price=price, currency="INR", verification_state="confirmed",
        source="manual", subcategory=None, product_url=None, media_asset_id=None,
        uncertain_fields=[], extraction_confidence=None, ai_run_id=None,
        model_version=None, prompt_version=None, schema_version=None,
    )


def _owned(*, name="Other", brand="House", family="woody", occasion=None, season=None, remaining=50):
    item = SimpleNamespace(
        id=uuid4(), display_name=name, brand=brand, usage_count=0, last_used_at=None,
    )
    detail = SimpleNamespace(
        fragrance_family=family, concentration="EDP", occasion=occasion or [], season=season or [],
        remaining_percent=remaining,
    )
    return OwnedFragrance(item=item, detail=detail)


def test_versions_activation_and_frozen_prior_authorities():
    assert FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.9"
    assert FRAGRANCE_PURCHASE_VERDICT_VERSION == "v3-05.9"
    assert FRAGRANCE_PURCHASE_CHECK_VERSION == "v3-05.9"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.9"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert CARE_PURCHASE_VERDICT_VERSION == "v3-05.5"
    assert CARE_PURCHASE_CHECK_VERSION == "v3-05.7"
    assert resolve_purchase_strategy("perfumes").state == "active"
    assert is_active_fragrance_category("perfumes")
    assert is_active_care_category("beauty")
    assert resolve_purchase_strategy("supplements").state == "prohibited"


def test_candidate_truth_reuses_family_ontology_and_rejects_owned_only_fields():
    assert {"fragrance_family", "concentration", "season", "occasion"} <= FRAGRANCE_CANDIDATE_DETAIL_KEYS
    truth = build_fragrance_candidate_truth(_candidate(fragrance_family="woody", occasion=["office"]))
    assert truth.normalized_fragrance_family == "woody"
    assert truth.facts_trusted is True
    assert truth.review_required is False
    try:
        validate_fragrance_candidate_details({"remaining_percent": 10})
    except ValueError as exc:
        assert "remaining_percent" in str(exc)
    else:
        raise AssertionError("owned-only remaining_percent must not be accepted on a candidate")


def test_extraction_accepts_visible_facts_only_and_rejects_customer_intent():
    base = {
        "category": "perfumes", "display_name": "Rain Garden", "confidence": 0.9,
        "photo_quality_notes": "clear label",
    }
    for key, value in (("occasion", ["office"]), ("season", ["summer"]), ("longevity_user_reported", "8 hours")):
        try:
            ExtractedFragranceCandidate(**{**base, "details": {key: value}})
        except ValueError:
            pass
        else:
            raise AssertionError(f"extraction must reject customer field {key}")
    non_perfume = ExtractedFragranceCandidate(
        category="beauty", display_name="Cleanser", confidence=0.9, photo_quality_notes="clear label", details={}
    )
    assert non_perfume.category == "beauty"
    try:
        ExtractedFragranceCandidate(
            category="beauty", display_name="Cleanser", confidence=0.9,
            photo_quality_notes="clear label", details={"fragrance_family": "woody"},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("non-perfume extraction must reject fragrance details")
    perfume = ExtractedFragranceCandidate(
        category="perfumes", display_name="Rain Garden", confidence=0.9,
        photo_quality_notes="clear label", details={"fragrance_family": "woody", "concentration": "EDP"},
    )
    assert perfume.details == {"fragrance_family": "woody", "concentration": "EDP"}


def test_exact_and_price_precedence():
    candidate = _candidate(fragrance_family="woody")
    exact = _owned(name="Rain Garden", brand="House", remaining=40)
    result = evaluate_fragrance_purchase(candidate=candidate, owned=[exact])
    assert result["verdict"] == "wait"
    assert result["primary_reason_code"] == "exact_bottle_available"
    result = evaluate_fragrance_purchase(candidate=_candidate(fragrance_family="woody", price=None), owned=[])
    assert result["primary_reason_code"] == "candidate_price_missing"
    result = evaluate_fragrance_purchase(candidate=_candidate(fragrance_family="woody"), owned=[_owned(name="Rain Garden", brand="House", remaining=10)])
    assert result["verdict"] == "buy"
    assert result["primary_reason_code"] == "exact_replacement_ready"
    result = evaluate_fragrance_purchase(candidate=candidate, owned=[exact, _owned(name="Rain Garden", brand="House", remaining=80)])
    assert result["verdict"] == "skip"
    assert result["primary_reason_code"] == "multiple_exact_bottles_owned"


def test_first_fragrance_and_context_coverage_are_owned_first():
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["office"], season=["summer"]), owned=[])
    assert result["verdict"] == "buy"
    assert result["primary_reason_code"] == "first_fragrance_gap"
    owner = _owned(occasion=["office"], season=["summer"])
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["office"], season=["summer"]), owned=[owner])
    assert result["primary_reason_code"] == "declared_use_already_covered"
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["festival"]), owned=[_owned(occasion=[])])
    assert result["primary_reason_code"] == "owned_context_incomplete"
    result = evaluate_fragrance_purchase(candidate=_candidate(occasion=["festival"]), owned=[_owned(occasion=["office"])])
    assert result["primary_reason_code"] == "declared_use_gap"


def test_family_overlap_is_supporting_only_and_fingerprint_is_order_stable():
    candidate = _candidate(fragrance_family="woody")
    first = _owned(name="Other A", family="woody", occasion=["office"], season=["summer"])
    second = _owned(name="Other B", family="woody", occasion=["office"], season=["summer"])
    one = evaluate_fragrance_purchase(candidate=candidate, owned=[first, second])
    two = evaluate_fragrance_purchase(candidate=candidate, owned=[second, first])
    assert one["verdict"] == two["verdict"] == "wait"
    assert one["primary_reason_code"] == "intended_use_missing"
    assert one["decision_fingerprint"] == two["decision_fingerprint"]
    assert one["supporting_reason_codes"] == ["same_family_owned"]


def test_context_covering_owned_options_are_not_same_family_shortcuts():
    candidate = _candidate(fragrance_family="woody", occasion=["office"])
    covering = _owned(name="Floral A", family="floral", occasion=["office"])
    family_only = _owned(name="Woody B", family="woody", occasion=["party"])
    result = evaluate_fragrance_purchase(candidate=candidate, owned=[family_only, covering])
    assert result["primary_reason_code"] == "declared_use_already_covered"
    assert [item["display_name"] for item in result["owned_options_to_use_first"]] == ["Floral A"]
    assert [item["display_name"] for item in result["same_family_owned"]] == ["Woody B"]


def test_context_fingerprint_canonicalises_declared_list_order():
    owned = [_owned(name="Floral A", family="floral", season=["summer"], occasion=["office"])]
    first_candidate = _candidate(occasion=["office", "party"], season=["winter", "summer"])
    candidate_fields = vars(first_candidate).copy()
    candidate_fields["details"] = {"occasion": ["party", "office"], "season": ["summer", "winter"]}
    second_candidate = SimpleNamespace(**candidate_fields)
    first = evaluate_fragrance_purchase(candidate=first_candidate, owned=owned)
    second = evaluate_fragrance_purchase(candidate=second_candidate, owned=owned)
    assert first["decision_fingerprint"] == second["decision_fingerprint"]


def test_context_vocab_and_customer_copy_fail_closed():
    try:
        evaluate_fragrance_purchase(candidate=_candidate(occasion=["ofice"]), owned=[])
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported occasion must be rejected")
    exact_unknown = _owned(name="Rain Garden", brand="House", remaining=None)
    result = evaluate_fragrance_purchase(candidate=_candidate(fragrance_family="woody"), owned=[exact_unknown])
    assert result["primary_reason_code"] == "exact_bottle_available"
    assert "plenty left" not in result["explanation"]
    assert "market" not in result["explanation"]


def test_owned_context_normalizes_legacy_labels_and_distinguishes_unknown_from_gap():
    candidate = _candidate(occasion=["business_meeting"], season=["summer"])
    legacy = _owned(occasion=["Business meeting"], season=["Summer"])
    covered = evaluate_fragrance_purchase(candidate=candidate, owned=[legacy])
    assert covered["primary_reason_code"] == "declared_use_already_covered"
    assert [row["owned_item_id"] for row in covered["owned_options_to_use_first"]] == [str(legacy.item.id)]

    unknown = evaluate_fragrance_purchase(
        candidate=_candidate(occasion=["office"]),
        owned=[_owned(occasion=["some old custom context"])],
    )
    assert unknown["primary_reason_code"] == "owned_context_incomplete"
    assert unknown["verdict"] == "wait"

    mixed = evaluate_fragrance_purchase(
        candidate=_candidate(occasion=["office"]),
        owned=[_owned(occasion=["party"]), _owned(occasion=["old legacy tag"])],
    )
    assert mixed["primary_reason_code"] == "owned_context_incomplete"

    uncovered = evaluate_fragrance_purchase(
        candidate=_candidate(occasion=["office"]),
        owned=[_owned(occasion=["party"]), _owned(occasion=["festival"])],
    )
    assert uncovered["primary_reason_code"] == "declared_use_gap"
    assert uncovered["verdict"] == "buy"


def test_fragrance_confirmation_can_explicitly_clear_extracted_details():
    candidate = _candidate(fragrance_family="woody", concentration="EDP")
    body = CarePurchaseCandidateConfirm(details=None, price=None)
    _apply_candidate_corrections(candidate, body)
    assert candidate.details == {}
    assert candidate.price is None


def _fragrance_item(name: str = "Rain Garden", *, price: int | None = 1200) -> dict:
    return {
        "category": "perfumes", "display_name": name, "brand": "House",
        "details": {"fragrance_family": "woody", "concentration": "EDP", "occasion": ["office"], "season": ["summer"]},
        "price": price, "currency": "INR",
    }


async def _count_model(model, account_id: uuid.UUID) -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        return int(await session.scalar(select(func.count(model.id)).where(model.account_id == account_id)))


async def _fragrance_side_effect_counts(account_id: uuid.UUID) -> dict[str, int | tuple[int, int] | None]:
    factory = get_sessionmaker()
    async with factory() as session:
        entitlement = await session.scalar(
            select(RecommendationEntitlement).where(
                RecommendationEntitlement.account_id == account_id,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )
        )
        return {
            "evaluations": int(await session.scalar(select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id))),
            "factors": int(await session.scalar(select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id))),
            "runs": int(await session.scalar(select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id))),
            "inputs": int(await session.scalar(select(func.count(RecommendationInput.id)).join(RecommendationRun).where(RecommendationRun.account_id == account_id))),
            "decisions": int(await session.scalar(select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id))),
            "entitlement": None if entitlement is None else (entitlement.included, entitlement.used),
            "inventory": int(await session.scalar(select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id))),
        }


def _perfume_extraction(name: str = "Rain Garden", *, category: str = "perfumes", price: int | None = 1200) -> str:
    details = '{"fragrance_family":"woody","concentration":"EDP"}' if category == "perfumes" else "{}"
    return (
        f'{{"category":"{category}","display_name":"{name}","brand":"House",'
        f'"details":{details},'
        f'"price":{price if price is not None else "null"},"currency":"INR",'
        '"confidence":0.98,"uncertain_fields":[],"photo_quality_notes":"clear label"}'
    )


@pytest.mark.asyncio
async def test_fragrance_manual_route_persists_candidate_without_inventory_and_checks(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    response = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "manual", "item": _fragrance_item()},
    )
    assert response.status_code == 200, response.text
    candidate = response.json()
    assert candidate["candidate"]["verification_state"] == "user_declared"
    candidate_id = candidate["candidate"]["id"]
    check = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token))
    assert check.status_code == 200, check.text
    assert check.json()["strategy"] == "fragrance_purchase"
    assert await _count_model(ShoppingCandidate, account_id) == 1
    assert await _count_model(InventoryItem, account_id) == 0


@pytest.mark.asyncio
async def test_fragrance_screenshot_rejects_wrong_category_and_requires_confirmation(
    app_client, db_clean, registered_supabase_user, fake_provider,
):
    from tests.conftest import png_bytes

    token, account_id = await registered_supabase_user()
    upload = await app_client.post("/api/v2/media/upload", headers=auth(token), files={"file": ("wrong.png", png_bytes(), "image/png")})
    assert upload.status_code in (200, 201), upload.text
    fake_provider.text = _perfume_extraction(category="beauty")
    wrong = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "screenshot", "media_asset_id": upload.json()["id"], "expected_category": "perfumes"},
    )
    assert wrong.status_code == 422, wrong.text
    assert await _count_model(ShoppingCandidate, account_id) == 0

    fake_provider.text = _perfume_extraction()
    upload = await app_client.post("/api/v2/media/upload", headers=auth(token), files={"file": ("perfume.png", png_bytes(), "image/png")})
    extracted = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "screenshot", "media_asset_id": upload.json()["id"], "expected_category": "perfumes"},
    )
    assert extracted.status_code == 200, extracted.text
    candidate = extracted.json()
    assert candidate["candidate"]["verification_state"] == "draft"
    candidate_id = candidate["candidate"]["id"]
    assert (await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token))).status_code == 422
    confirmed = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm", headers=auth(token),
        json={"details": {"fragrance_family": "woody", "concentration": None, "occasion": ["office"], "season": ["summer"]}, "price": None},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["candidate"]["details"]["concentration"] is None or "concentration" not in confirmed.json()["candidate"]["details"]
    check = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token))
    assert check.status_code == 200, check.text
    assert check.json()["verdict"]["primary_reason_code"] == "candidate_price_missing"


@pytest.mark.asyncio
async def test_fragrance_decision_memory_is_candidate_backed_idempotent_and_side_effect_free(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    inspected = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "manual", "item": _fragrance_item()},
    )
    assert inspected.status_code == 200, inspected.text
    candidate_id = inspected.json()["candidate"]["id"]
    before = await _fragrance_side_effect_counts(account_id)
    check = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/fragrance-check", headers=auth(token))
    assert check.status_code == 200, check.text
    saved = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        json={"decision": "waiting"},
    )
    assert saved.status_code == 200, saved.text
    memory = saved.json()
    assert memory["strategy"] == "fragrance_purchase"
    assert memory["evaluation_id"] is None
    assert memory["recommendation_version"] == "v3-05.9"
    assert memory["recommendation_fingerprint"] == check.json()["verdict"]["decision_fingerprint"]
    assert memory["recommendation_snapshot"]["strategy"] == "fragrance_purchase"
    repeated = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
        json={"decision": "bought"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["id"] == memory["id"]
    after = await _fragrance_side_effect_counts(account_id)
    for key in ("evaluations", "factors", "runs", "inputs", "inventory", "entitlement"):
        assert after[key] == before[key]
    assert after["decisions"] == before["decisions"] + 1


@pytest.mark.asyncio
async def test_fragrance_concurrent_first_decisions_leave_one_memory_row(
    app_client, db_clean, registered_supabase_user,
):
    token, account_id = await registered_supabase_user()
    inspected = await app_client.post(
        "/api/v2/shopping/candidates/inspect", headers=auth(token),
        json={"source": "manual", "item": _fragrance_item("Concurrent Rain")},
    )
    assert inspected.status_code == 200, inspected.text
    candidate_id = inspected.json()["candidate"]["id"]

    async def save(decision: str):
        return await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision", headers=auth(token),
            json={"decision": decision},
        )

    responses = await asyncio.gather(save("waiting"), save("skipped"))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(PurchaseDecision).where(
            PurchaseDecision.account_id == account_id,
            PurchaseDecision.candidate_id == uuid.UUID(candidate_id),
            PurchaseDecision.strategy_key == "fragrance_purchase",
        ))).scalars().all()
    assert len(rows) == 1
