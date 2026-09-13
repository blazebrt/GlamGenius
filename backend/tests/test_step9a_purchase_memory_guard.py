"""Step 9A append-only decision history and exact guard coverage."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from app.domains.purchase.identity import PURCHASE_IDENTITY_VERSION, identity_for_candidate
from app.domains.recommendation.models import PurchaseDecisionEvent, ShoppingCandidate
from sqlalchemy import select

from tests.conftest import auth
from tests.test_v3_05_7_care_purchase_experience import _seed_db_candidate


def _candidate(**changes):
    values = dict(
        id=uuid.uuid4(), account_id=uuid.uuid4(), source="manual", category="beauty",
        display_name="Gentle Cleanser", brand="Example Labs", subcategory="cleanser",
        details={"product_type": "cleanser", "purpose": "cleanse", "active_ingredients": ["Glycerin"]},
        verification_state="confirmed", price=Decimal("499.00"), currency="INR",
        product_url="https://shop.example.test/p/gentle-cleanser?utm_source=campaign",
    )
    values.update(changes)
    return ShoppingCandidate(**values)


def test_identity_is_exact_conservative_and_price_or_tracking_does_not_change_it():
    first = identity_for_candidate(_candidate())
    second = identity_for_candidate(_candidate(price=Decimal("599.00"), product_url="https://shop.example.test/p/gentle-cleanser?gclid=x"))
    changed = identity_for_candidate(_candidate(details={"product_type": "serum", "purpose": "treat", "active_ingredients": ["Niacinamide"]}))
    insufficient = identity_for_candidate(_candidate(brand=None, product_url=None, details={"product_type": "cleanser"}))
    assert first["version"] == PURCHASE_IDENTITY_VERSION
    assert first["state"] == second["state"] == "exact"
    assert first["fingerprint"] == second["fingerprint"]
    assert changed["fingerprint"] != first["fingerprint"]
    assert insufficient == {"version": PURCHASE_IDENTITY_VERSION, "state": "insufficient", "fingerprint": None}


def test_identity_is_strategy_scoped_and_ignores_fragrance_use_context():
    first = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edp", "season": "winter", "occasion": "evening"},
    )
    changed_use = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edp", "season": "summer", "occasion": "office", "longevity_user_reported": "long"},
    )
    changed_product = _candidate(
        category="perfumes", details={"fragrance_family": "woody", "concentration": "edt"},
    )
    draft_style = _candidate(
        category="wardrobe", verification_state="draft", subcategory="shirt", size="m", fabric="cotton", colour="blue", details={},
    )
    assert identity_for_candidate(first)["fingerprint"] == identity_for_candidate(changed_use)["fingerprint"]
    assert identity_for_candidate(first)["fingerprint"] != identity_for_candidate(changed_product)["fingerprint"]
    assert identity_for_candidate(draft_style)["state"] == "insufficient"


def test_event_lookup_index_covers_current_decision_idempotency_queries():
    indexes = {index.name: tuple(column.name for column in index.columns) for index in PurchaseDecisionEvent.__table__.indexes}
    assert indexes["ix_purchase_decision_events_decision_created"] == ("decision_id", "created_at")


@pytest.mark.asyncio
async def test_history_is_append_only_and_guard_is_account_scoped(app_client, db_clean, registered_supabase_user):
    token, account_id = await registered_supabase_user()
    candidate_id = await _seed_db_candidate(account_id)
    # Add enough trusted facts for the deliberate exact-identity contract.
    from app.shared.database.sql import get_sessionmaker
    factory = get_sessionmaker()
    async with factory() as session:
        candidate = await session.get(ShoppingCandidate, candidate_id)
        candidate.brand = "Example Labs"
        await session.commit()

    for decision in ("waiting", "bought", "bought"):
        response = await app_client.post(
            f"/api/v2/shopping/candidates/{candidate_id}/decision?on=2026-08-20",
            headers=auth(token), json={"decision": decision},
        )
        assert response.status_code == 200, response.text

    history = await app_client.get("/api/v2/shopping/decision-history?limit=1", headers=auth(token))
    assert history.status_code == 200, history.text
    payload = history.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["decision"] == "bought"
    assert payload["next_before"]
    guard = await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard", headers=auth(token))
    assert guard.status_code == 200, guard.text
    assert guard.json()["guard_state"] == "exact_prior_bought"
    assert guard.json()["prior_consideration_count"] == 1
    assert guard.json()["owned_redundancy"] is None
    assert guard.json()["history_coverage"]["state"] == "step_9a_events_only"

    no_event_id = await _seed_db_candidate(account_id)
    async with factory() as session:
        no_event = await session.get(ShoppingCandidate, no_event_id)
        no_event.brand = "Example Labs"
        await session.commit()
    no_event_guard = await app_client.get(f"/api/v2/shopping/candidates/{no_event_id}/purchase-guard", headers=auth(token))
    assert no_event_guard.status_code == 200, no_event_guard.text
    assert no_event_guard.json()["guard_state"] == "no_step9a_prior_event"

    other_token, _ = await registered_supabase_user()
    assert (await app_client.get("/api/v2/shopping/decision-history", headers=auth(other_token))).json()["items"] == []
    assert (await app_client.get(f"/api/v2/shopping/candidates/{candidate_id}/purchase-guard", headers=auth(other_token))).status_code == 404
    async with factory() as session:
        events = (await session.execute(
            select(PurchaseDecisionEvent)
            .where(PurchaseDecisionEvent.account_id == account_id)
            .order_by(PurchaseDecisionEvent.created_at, PurchaseDecisionEvent.id)
        )).scalars().all()
    assert [event.decision for event in events] == ["waiting", "bought"]
