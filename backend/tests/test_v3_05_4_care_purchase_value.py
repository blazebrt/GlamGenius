"""Pure V3-05.4 Care value-context contract tests."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.bootstrap import seed_inventory_categories
from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import (
    BeautyProductDetail,
    InventoryAttribute,
    InventoryItem,
    InventoryValueEvent,
    ItemUsageEvent,
)
from app.domains.purchase import service as purchase_service
from app.domains.purchase.care_value import (
    project_care_purchase_value,
)
from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION,
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION,
    CARE_PURCHASE_EVIDENCE_VERSION,
    CARE_PURCHASE_VALUE_SCHEMA_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
    PRODUCT_QUALITY_CONTRACT_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_INTELLIGENCE_FOUNDATION_VERSION,
    PURCHASE_STRATEGY_REGISTRY_VERSION,
    resolve_purchase_strategy,
)
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    PurchaseEvaluationFactor,
    RecommendationEntitlement,
    RecommendationRun,
    ShoppingCandidate,
)
from app.domains.routines.models import Routine, RoutineStep
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth


def _assessment(*, fingerprint: str = "assessment-a", price_role: str = "gap") -> dict:
    role_status = "addresses_required_gap" if price_role == "gap" else "required_role_already_covered"
    return {
        "care_purchase_assessment_version": "v3-05.2",
        "care_purchase_assessment_schema_version": "v3-05.2",
        "account_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "candidate_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "category": "beauty",
        "plan_date": "2026-08-19",
        "candidate_truth_version": "v3-05.1",
        "assessment_fingerprint": fingerprint,
        "dimensions": {
            "role_utility": {"status": role_status, "care_slot": "cleanser", "required": True, "is_gap": price_role == "gap"},
            "redundancy": {"status": "none_eligible_owned_same_slot", "eligible_owned_same_slot_count": 0, "selected_owned_item_id": None},
        },
    }


def _recovery(item_id: str = "22222222-2222-2222-2222-222222222222", *, value=420, currency="INR", missing=()):
    return {
        "item_id": item_id,
        "display_name": "Owned Cleanser",
        "metric_version": "v1",
        "is_estimate": True,
        "estimated_value": value,
        "currency": currency,
        "missing_inputs": list(missing),
        "inputs": {"purchase_price": 1000 if value is not None else None, "remaining_estimate": 0.8},
        "explanation": "Estimated Value to Recover; never exact.",
    }


async def _seed_runtime_records(
    account_id: uuid.UUID,
    *,
    item_specs: tuple[dict, ...] = (),
    price: Decimal | None = Decimal("1299.00"),
    category: str = "beauty",
    product_type: str = "cleanser",
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    """Seed one trusted candidate and real owned Care products for API tests."""
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
                display_name="Runtime candidate cleanser",
                details={
                    "product_type": product_type,
                    "purpose": "cleanse",
                    "active_ingredients": ["Niacinamide"],
                },
                verification_state="confirmed",
                price=price,
                currency="INR",
            )
        )
        item_ids: list[uuid.UUID] = []
        for index, spec in enumerate(item_specs):
            item_id = spec.get("id", uuid.uuid4())
            item_ids.append(item_id)
            session.add(
                InventoryItem(
                    id=item_id,
                    account_id=account_id,
                    category=category,
                    subcategory=spec.get("slot", product_type),
                    display_name=spec.get("display_name", f"Owned Care item {index}"),
                    status=spec.get("status", "active"),
                    verification_state="confirmed",
                    purchase_price=spec.get("purchase_price", Decimal("1000.00")),
                    currency=spec.get("currency", "INR"),
                    usage_count=spec.get("usage_count", 0),
                    last_used_at=spec.get("last_used_at", date(2026, 1, 1)),
                    condition=spec.get("condition", "good"),
                    created_at=datetime(2026, 1, 1),
                )
            )
            await session.flush()
            session.add(
                BeautyProductDetail(
                    item_id=item_id,
                    product_type=spec.get("slot", product_type),
                    purpose="cleanse",
                    remaining_percent=spec.get("remaining_percent", 80),
                    expiry_date=spec.get("expiry_date"),
                    ingredients_text="Niacinamide",
                )
            )
        await session.commit()
    return candidate_id, tuple(item_ids)


async def _runtime_counts(account_id: uuid.UUID) -> dict[str, object]:
    factory = get_sessionmaker()
    async with factory() as session:
        entitlement = await session.scalar(
            select(RecommendationEntitlement).where(
                RecommendationEntitlement.account_id == account_id,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )
        )
        return {
            "candidates": await session.scalar(
                select(func.count(ShoppingCandidate.id)).where(ShoppingCandidate.account_id == account_id)
            ),
            "inventory": await session.scalar(
                select(func.count(InventoryItem.id)).where(InventoryItem.account_id == account_id)
            ),
            "attributes": await session.scalar(
                select(func.count(InventoryAttribute.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)
            ),
            "value_events": await session.scalar(
                select(func.count(InventoryValueEvent.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)
            ),
            "usage_events": await session.scalar(
                select(func.count(ItemUsageEvent.id)).join(InventoryItem).where(InventoryItem.account_id == account_id)
            ),
            "runs": await session.scalar(
                select(func.count(RecommendationRun.id)).where(RecommendationRun.account_id == account_id)
            ),
            "evaluations": await session.scalar(
                select(func.count(PurchaseEvaluation.id)).where(PurchaseEvaluation.account_id == account_id)
            ),
            "factors": await session.scalar(
                select(func.count(PurchaseEvaluationFactor.id)).join(PurchaseEvaluation).where(PurchaseEvaluation.account_id == account_id)
            ),
            "decisions": await session.scalar(
                select(func.count(PurchaseDecision.id)).where(PurchaseDecision.account_id == account_id)
            ),
            "routines": await session.scalar(
                select(func.count(Routine.id)).where(Routine.account_id == account_id)
            ),
            "steps": await session.scalar(
                select(func.count(RoutineStep.id)).join(Routine).where(Routine.account_id == account_id)
            ),
            "entitlement": None
            if entitlement is None
            else (entitlement.included, entitlement.used, entitlement.period_key),
        }


async def _candidate_updated_at(candidate_id: uuid.UUID):
    factory = get_sessionmaker()
    async with factory() as session:
        return await session.scalar(
            select(ShoppingCandidate.updated_at).where(ShoppingCandidate.id == candidate_id)
        )


def test_versions_and_care_strategy_remain_frozen():
    assert CARE_PURCHASE_VALUE_VERSION == "v3-05.4"
    assert CARE_PURCHASE_VALUE_SCHEMA_VERSION == "v3-05.4"
    assert CARE_PURCHASE_EVIDENCE_VERSION == "v3-05.3"
    assert CARE_PURCHASE_EVIDENCE_SCHEMA_VERSION == "v3-05.3"
    assert CARE_PURCHASE_ASSESSMENT_VERSION == "v3-05.2"
    assert CARE_PURCHASE_ASSESSMENT_SCHEMA_VERSION == "v3-05.2"
    assert CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION == "v3-05.1"
    assert PURCHASE_CANDIDATE_TRUTH_VERSION == "v3-05.1"
    assert PURCHASE_INTELLIGENCE_FOUNDATION_VERSION == "v3-05.0"
    assert PURCHASE_STRATEGY_REGISTRY_VERSION == "v3-05.0"
    assert PRODUCT_QUALITY_CONTRACT_VERSION == "v3-05.0"
    assert resolve_purchase_strategy("beauty").state == "inactive"
    assert resolve_purchase_strategy("hair").state == "inactive"


def test_required_gap_and_recorded_price_without_recovery():
    body = project_care_purchase_value(_assessment(), candidate_price="1299.00", candidate_currency="INR").as_dict()
    context = body["value_context"]
    assert context["status"] == "financial_context_available"
    assert context["candidate_spend"] == {"status": "recorded", "amount": "1299.00", "currency": "INR"}
    assert context["role_context"]["status"] == "addresses_required_gap"
    assert context["owned_value_recovery"]["status"] == "no_low_use_eligible_owned_same_slot"
    assert context["currency_context"]["status"] == "no_quantified_recovery"
    assert "value_score" not in str(body).lower()


def test_regular_use_role_coverage_is_not_value_recovery():
    assessment = _assessment(price_role="covered")
    assessment["dimensions"]["redundancy"] = {
        "status": "one_eligible_owned_same_slot",
        "eligible_owned_same_slot_count": 1,
        "selected_owned_item_id": "22222222-2222-2222-2222-222222222222",
    }
    body = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR").as_dict()
    assert body["value_context"]["role_context"]["status"] == "required_role_already_covered"
    assert body["value_context"]["owned_value_recovery"]["status"] == "no_low_use_eligible_owned_same_slot"


def test_recovery_statuses_and_same_currency_total():
    assessment = _assessment()
    one = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),)).as_dict()
    assert one["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_estimated"
    assert one["value_context"]["currency_context"]["status"] == "same_currency_context"
    assert one["value_context"]["estimated_recoverable_total"] == {"amount": "420.00", "currency": "INR", "is_estimate": True}

    partial = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(), _recovery("33333333-3333-3333-3333-333333333333", value=None, missing=("purchase_price",)))).as_dict()
    assert partial["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_partially_estimated"
    assert partial["value_context"]["estimated_recoverable_total"] is None

    none = project_care_purchase_value(assessment, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(value=None, missing=("purchase_price",)),)).as_dict()
    assert none["value_context"]["owned_value_recovery"]["status"] == "low_use_recovery_unquantified"


def test_missing_zero_and_currency_boundaries():
    missing = project_care_purchase_value(_assessment(), candidate_price=None, candidate_currency="INR", recovery_rows=(_recovery(value=None, missing=("purchase_price",)),)).as_dict()
    assert missing["value_context"]["status"] == "financial_context_unavailable"
    assert missing["value_context"]["candidate_spend"]["status"] == "missing"
    assert missing["value_context"]["currency_context"]["status"] == "candidate_price_missing"

    partial = project_care_purchase_value(_assessment(), candidate_price=None, candidate_currency="INR", recovery_rows=(_recovery(),)).as_dict()
    assert partial["value_context"]["status"] == "financial_context_partial"

    zero = project_care_purchase_value(_assessment(), candidate_price=0, candidate_currency="INR").as_dict()
    assert zero["value_context"]["candidate_spend"] == {"status": "recorded", "amount": "0.00", "currency": "INR"}

    mixed = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(currency="USD"),)).as_dict()
    assert mixed["value_context"]["currency_context"] == {"status": "mixed_currency_no_conversion", "comparison_available": False}
    assert mixed["value_context"]["estimated_recoverable_total"] is None


def test_fingerprints_are_material_and_metadata_invariant():
    base = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),))
    price = project_care_purchase_value(_assessment(), candidate_price=1399, candidate_currency="INR", recovery_rows=(_recovery(),))
    currency = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="USD", recovery_rows=(_recovery(),))
    changed_recovery = project_care_purchase_value(_assessment(), candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(value=200),))
    assert base.value_fingerprint != price.value_fingerprint
    assert base.value_fingerprint != currency.value_fingerprint
    assert base.value_fingerprint != changed_recovery.value_fingerprint
    assert base.value_context == project_care_purchase_value({**_assessment(), "brand": "changed", "product_url": "https://example.test"}, candidate_price=1299, candidate_currency="INR", recovery_rows=(_recovery(),)).value_context


def test_recovery_presentation_is_not_fingerprint_material():
    first = _recovery()
    second = {**first, "display_name": "My Morning Cleanser", "explanation": "Updated explanatory copy."}
    left = project_care_purchase_value(_assessment(), candidate_price="1299.00", candidate_currency="INR", recovery_rows=(first,)).as_dict()
    right = project_care_purchase_value(_assessment(), candidate_price="1299.00", candidate_currency="INR", recovery_rows=(second,)).as_dict()
    assert left["value_context"]["owned_value_recovery"]["items"][0]["display_name"] != right["value_context"]["owned_value_recovery"]["items"][0]["display_name"]
    assert left["value_context"]["owned_value_recovery"]["items"][0]["explanation"] != right["value_context"]["owned_value_recovery"]["items"][0]["explanation"]
    assert left["value_fingerprint"] == right["value_fingerprint"]


def test_monetary_serialization_is_fixed_point_decimal_text():
    body = project_care_purchase_value(
        _assessment(),
        candidate_price="1299.00",
        candidate_currency="INR",
        recovery_rows=(_recovery(value="420.10"),),
    ).as_dict()
    assert body["value_context"]["candidate_spend"]["amount"] == "1299.00"
    assert body["value_context"]["estimated_recoverable_total"]["amount"] == "420.10"
    assert isinstance(body["value_context"]["candidate_spend"]["amount"], str)


@pytest.mark.parametrize("category", ("perfumes", "", None))
def test_malformed_category_fails_closed(category):
    assessment = _assessment()
    assessment["category"] = category
    with pytest.raises(ValueError):
        project_care_purchase_value(assessment, candidate_price=1, candidate_currency="INR")


@pytest.mark.parametrize("missing_key", ("account_id", "candidate_id", "plan_date", "assessment_fingerprint"))
def test_missing_assessment_identity_fails_closed(missing_key):
    assessment = _assessment()
    assessment[missing_key] = None
    with pytest.raises(ValueError):
        project_care_purchase_value(assessment, candidate_price=1, candidate_currency="INR")


def test_fixed_date_and_source_boundaries_are_explicit():
    source = Path(__file__).parents[1] / "app" / "domains" / "purchase"
    value_text = (source / "care_value.py").read_text(encoding="utf-8").lower()
    service_text = (source / "value_service.py").read_text(encoding="utf-8").lower()
    assert "date.today" not in value_text
    assert "date.today" not in service_text
    for forbidden in ("price_per_ml", "price_per_g", "cost_per_use", "cost_per_application", "expected_uses", "expected_duration", "monthly_cost", "recommendation.roi", "httpx", "requests"):
        assert forbidden not in value_text + service_text
    assert "value_to_recover" in service_text
    assert "is_low_use" in service_text


@pytest.mark.asyncio
async def test_runtime_draft_fails_before_assessment_or_inventory(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    token, account_id = await registered_supabase_user()
    candidate_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await seed_inventory_categories(session)
        session.add(
            ShoppingCandidate(
                id=candidate_id,
                account_id=account_id,
                source="photo_extracted",
                category="beauty",
                display_name="Draft cleanser",
                details={"product_type": "cleanser", "purpose": "cleanse"},
                verification_state="draft",
                extraction_confidence=0.99,
            )
        )
        await session.commit()

    calls = {"assessment": 0, "low_use": 0, "value": 0}

    async def assessment_spy(*args, **kwargs):
        calls["assessment"] += 1
        raise AssertionError("draft must fail before Care assessment")

    def low_use_spy(*args, **kwargs):
        calls["low_use"] += 1
        raise AssertionError("draft must fail before Inventory low-use lookup")

    def value_spy(*args, **kwargs):
        calls["value"] += 1
        raise AssertionError("draft must fail before Value to Recover")

    monkeypatch.setattr(purchase_service, "care_purchase_assessment", assessment_spy)
    monkeypatch.setattr(inventory_service, "is_low_use", low_use_spy)
    monkeypatch.setattr(inventory_service, "value_to_recover", value_spy)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["field"] == "verification_state"
    assert calls == {"assessment": 0, "low_use": 0, "value": 0}


@pytest.mark.asyncio
async def test_runtime_low_use_recovery_uses_canonical_inventory_authority(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    token, account_id = await registered_supabase_user()
    candidate_id = uuid.uuid4()
    item_id = uuid.uuid4()
    factory = get_sessionmaker()
    async with factory() as session:
        await seed_inventory_categories(session)
        session.add(
            ShoppingCandidate(
                id=candidate_id,
                account_id=account_id,
                source="manual",
                category="beauty",
                display_name="Candidate cleanser",
                details={"product_type": "cleanser", "purpose": "cleanse", "active_ingredients": ["Niacinamide"]},
                verification_state="confirmed",
                price=Decimal("1299.00"),
                currency="INR",
            )
        )
        item = InventoryItem(
            id=item_id,
            account_id=account_id,
            category="beauty",
            subcategory="cleanser",
            display_name="Owned cleanser",
            status="active",
            verification_state="confirmed",
            purchase_price=Decimal("1000.00"),
            currency="INR",
            usage_count=0,
            last_used_at=date(2026, 1, 1),
            condition="good",
            created_at=datetime(2026, 1, 1),
        )
        session.add(item)
        await session.flush()
        session.add(
            BeautyProductDetail(
                item_id=item_id,
                product_type="cleanser",
                purpose="cleanse",
                remaining_percent=80,
                ingredients_text="Niacinamide",
            )
        )
        await session.commit()

    assessment = _assessment()
    assessment["account_id"] = str(account_id)
    assessment["candidate_id"] = str(candidate_id)
    assessment["dimensions"]["redundancy"] = {
        "status": "one_eligible_owned_same_slot",
        "eligible_owned_same_slot_count": 1,
        "selected_owned_item_id": str(item_id),
        "eligible_owned_same_slot": [{"owned_item_id": str(item_id), "display_name": "Owned cleanser", "slot": "cleanser"}],
    }

    async def assessment_spy(*args, **kwargs):
        return assessment

    monkeypatch.setattr(purchase_service, "care_purchase_assessment", assessment_spy)
    async with factory() as session:
        owned = await session.get(InventoryItem, item_id)
        details = await inventory_service.details_for(session, owned)
        expected = inventory_service.value_to_recover(owned, details, today=date(2026, 8, 19))
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    row = response.json()["value_context"]["owned_value_recovery"]["items"][0]
    assert row["owned_item_id"] == str(item_id)
    assert row["metric_version"] == expected["metric_version"]
    assert row["currency"] == expected["currency"]
    assert row["missing_inputs"] == expected["missing_inputs"]
    assert row["inputs"] == expected["inputs"]
    assert row["estimated_value"] == format(Decimal(str(expected["estimated_value"])).quantize(Decimal("0.01")), "f")


@pytest.mark.asyncio
async def test_runtime_regular_use_is_assessment_eligible_but_not_recovery(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, (item_id,) = await _seed_runtime_records(
        account_id,
        item_specs=(
            {
                "usage_count": 3,
                "last_used_at": date(2026, 8, 10),
                "display_name": "Regularly used cleanser",
            },
        ),
    )
    assessment = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert assessment.status_code == 200, assessment.text
    eligible = assessment.json()["dimensions"]["redundancy"]["eligible_owned_same_slot"]
    assert str(item_id) in {row["owned_item_id"] for row in eligible}

    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    recovery = response.json()["value_context"]["owned_value_recovery"]
    assert recovery["status"] == "no_low_use_eligible_owned_same_slot"
    assert str(item_id) not in {row["owned_item_id"] for row in recovery["items"]}


@pytest.mark.asyncio
async def test_runtime_blocked_low_use_never_becomes_recovery(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, (item_id,) = await _seed_runtime_records(
        account_id,
        item_specs=(
            {
                "expiry_date": date(2026, 8, 18),
                "display_name": "Expired cleanser",
            },
        ),
    )
    assessment = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert assessment.status_code == 200, assessment.text
    redundancy = assessment.json()["dimensions"]["redundancy"]
    assert str(item_id) in {row["owned_item_id"] for row in redundancy["blocked_owned_same_slot"]}
    assert str(item_id) not in {row["owned_item_id"] for row in redundancy["eligible_owned_same_slot"]}

    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    recovery = response.json()["value_context"]["owned_value_recovery"]
    assert str(item_id) not in {row["owned_item_id"] for row in recovery["items"]}


@pytest.mark.asyncio
async def test_runtime_wrong_slot_is_not_value_recovery(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, (item_id,) = await _seed_runtime_records(
        account_id,
        item_specs=({"slot": "serum", "display_name": "Owned serum"},),
    )
    assessment = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on=2026-08-19",
        headers=auth(token),
    )
    assert assessment.status_code == 200, assessment.text
    redundancy = assessment.json()["dimensions"]["redundancy"]
    assert str(item_id) not in {row["owned_item_id"] for row in redundancy["eligible_owned_same_slot"]}

    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    recovery = response.json()["value_context"]["owned_value_recovery"]
    assert str(item_id) not in {row["owned_item_id"] for row in recovery["items"]}


@pytest.mark.asyncio
async def test_runtime_missing_owned_price_is_unquantified(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, (item_id,) = await _seed_runtime_records(
        account_id,
        item_specs=({"purchase_price": None, "display_name": "Price-less cleanser"},),
    )
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    recovery = response.json()["value_context"]["owned_value_recovery"]
    assert recovery["status"] == "low_use_recovery_unquantified"
    row = next(row for row in recovery["items"] if row["owned_item_id"] == str(item_id))
    assert row["estimated_value"] is None
    assert "purchase_price" in row["missing_inputs"]


@pytest.mark.asyncio
async def test_runtime_explicit_plan_date_reaches_inventory_authorities(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    token, account_id = await registered_supabase_user()
    candidate_id, (item_id,) = await _seed_runtime_records(
        account_id,
        item_specs=(
            {
                "display_name": "Date propagation cleanser",
                "usage_count": 0,
                "last_used_at": date(2026, 1, 1),
                "purchase_price": Decimal("1000.00"),
                "remaining_percent": 80,
            },
        ),
    )
    requested = date(2026, 8, 19)
    assessment = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-assessment?on={requested.isoformat()}",
        headers=auth(token),
    )
    assert assessment.status_code == 200, assessment.text
    eligible = assessment.json()["dimensions"]["redundancy"]["eligible_owned_same_slot"]
    assert str(item_id) in {row["owned_item_id"] for row in eligible}
    low_use_dates: list[date | None] = []
    value_dates: list[date | None] = []
    original_low_use = inventory_service.is_low_use
    original_value = inventory_service.value_to_recover

    def low_use_spy(item, today=None):
        low_use_dates.append(today)
        return original_low_use(item, today=today)

    def value_spy(item, details, today=None):
        value_dates.append(today)
        return original_value(item, details, today=today)

    monkeypatch.setattr(inventory_service, "is_low_use", low_use_spy)
    monkeypatch.setattr(inventory_service, "value_to_recover", value_spy)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on={requested.isoformat()}",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert requested in low_use_dates
    assert value_dates and all(value_date == requested for value_date in value_dates)
    recovery_items = response.json()["value_context"]["owned_value_recovery"]["items"]
    assert str(item_id) in {row["owned_item_id"] for row in recovery_items}


@pytest.mark.asyncio
async def test_runtime_value_is_account_isolated(
    app_client, db_clean, registered_supabase_user
):
    token_a, account_a = await registered_supabase_user()
    token_b, _ = await registered_supabase_user()
    candidate_id, _ = await _seed_runtime_records(account_a)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token_b),
    )
    assert response.status_code == 404
    assert "1299" not in response.text
    assert "Owned" not in response.text


@pytest.mark.asyncio
async def test_runtime_value_gets_are_read_only_and_deterministic(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, _ = await _seed_runtime_records(account_id)
    before_counts = await _runtime_counts(account_id)
    before_updated_at = await _candidate_updated_at(candidate_id)
    path = f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19"
    first = await app_client.get(path, headers=auth(token))
    second = await app_client.get(path, headers=auth(token))
    assert first.status_code == second.status_code == 200, first.text
    assert first.json() == second.json()
    assert first.json()["value_fingerprint"] == second.json()["value_fingerprint"]
    after_counts = await _runtime_counts(account_id)
    assert after_counts == before_counts
    assert await _candidate_updated_at(candidate_id) == before_updated_at
    assert after_counts["value_events"] == before_counts["value_events"]
    assert after_counts["entitlement"] == before_counts["entitlement"]


@pytest.mark.asyncio
async def test_runtime_value_does_not_invoke_ai_or_candidate_extraction(
    app_client, db_clean, registered_supabase_user, monkeypatch
):
    token, account_id = await registered_supabase_user()
    candidate_id, _ = await _seed_runtime_records(account_id)
    calls = {"extraction": 0, "ai": 0}

    async def extraction_spy(*args, **kwargs):
        calls["extraction"] += 1
        raise AssertionError("Care value projection must not extract candidates")

    async def ai_spy(*args, **kwargs):
        calls["ai"] += 1
        raise AssertionError("Care value projection must not call the AI gateway")

    monkeypatch.setattr("app.domains.purchase.extraction.extract_purchase_candidate", extraction_spy)
    monkeypatch.setattr("app.domains.ai_gateway.gateway.run_structured", ai_spy)
    response = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    assert calls == {"extraction": 0, "ai": 0}


@pytest.mark.asyncio
async def test_runtime_metadata_only_candidate_correction_preserves_value_fingerprint(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, _ = await _seed_runtime_records(account_id)
    path = f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19"
    before = await app_client.get(path, headers=auth(token))
    assert before.status_code == 200, before.text
    before_body = before.json()
    corrected = await app_client.post(
        f"/api/v2/shopping/candidates/{candidate_id}/confirm",
        headers=auth(token),
        json={
            "brand": "Changed brand",
            "product_url": "https://example.test/product",
            "details": {
                "product_type": "cleanser",
                "purpose": "gentle cleanse updated",
                "active_ingredients": ["Niacinamide"],
            },
        },
    )
    assert corrected.status_code == 200, corrected.text
    after = await app_client.get(path, headers=auth(token))
    assert after.status_code == 200, after.text
    assert after.json()["value_fingerprint"] == before_body["value_fingerprint"]


@pytest.mark.asyncio
async def test_runtime_care_evaluate_remains_inactive_and_side_effect_free(
    app_client, db_clean, registered_supabase_user
):
    token, account_id = await registered_supabase_user()
    candidate_id, _ = await _seed_runtime_records(account_id)
    value = await app_client.get(
        f"/api/v2/shopping/candidates/{candidate_id}/care-value?on=2026-08-19",
        headers=auth(token),
    )
    assert value.status_code == 200, value.text
    before = await _runtime_counts(account_id)
    for category in ("beauty", "hair"):
        response = await app_client.post(
            "/api/v2/shopping/evaluate",
            headers=auth(token),
            json={
                "source": "manual",
                "item": {"category": category, "display_name": "Inactive Care candidate"},
                "client_mutation_id": f"v3-05-4-inactive-{category}",
            },
        )
        assert response.status_code == 422, response.text
        assert "verdict" not in response.json()
    assert await _runtime_counts(account_id) == before
