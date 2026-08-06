"""§1.9 — shopping-evaluation regression coverage.

The "should I buy this?" flow is the one place the product tells a user not to
spend money, so the arithmetic behind it has to stay checkable and the guidance
has to stay constructive. Everything below runs through the real V2 routes with
a deterministic AI provider; nothing here touches a shop, a price feed or a
payment.

What this protects against
--------------------------
* A verdict arriving without the factors that produced it, which would make the
  advice unauditable by the person acting on it.
* The evaluation silently ignoring what the user already owns — the single
  thing that makes this feature worth having.
* Something the user already owns twice being recommended as a straight Buy.
* A replayed request (flaky mobile network) consuming a second run from the
  beta allowance or creating a second candidate.
* One account reading another's evaluation, or attaching a decision to it.
* Payment, checkout or upgrade language reappearing in this surface.
"""
from __future__ import annotations

import uuid

import pytest
from app.domains.recommendation import roi
from app.domains.recommendation.models import (
    PurchaseDecision,
    PurchaseEvaluation,
    RecommendationEntitlement,
    ShoppingCandidate,
)
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import func, select

from tests.conftest import auth

pytestmark = pytest.mark.asyncio


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


async def _add_item(client, token, **overrides):
    payload = {
        "category": "wardrobe",
        "display_name": "Ivory Linen Shirt",
        "brand": "Fable",
        "subcategory": "shirt",
        "details": {
            "colour": "ivory",
            "fabric": "linen",
            "fit": "relaxed",
            "formality": "smart_casual",
        },
    }
    payload.update(overrides)
    resp = await client.post("/api/v2/inventory/items", headers=auth(token), json=payload)
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _evaluate(client, token, **overrides):
    body = {"source": "manual", "item": dict(LINEN_SHIRT), "currency": "INR"}
    body.update(overrides)
    return await client.post("/api/v2/shopping/evaluate", headers=auth(token), json=body)


# ---------------------------------------------------------------------------
# The published model
# ---------------------------------------------------------------------------

async def test_roi_model_is_published_in_full(app_client, db_clean, registered_supabase_user):
    """A user told to skip something is entitled to see how that was worked
    out without asking anyone."""
    token, _ = await registered_supabase_user()
    resp = await app_client.get("/api/v2/shopping/roi-model", headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == roi.ROI_VERSION
    assert body["thresholds"] == {"buy": roi.BUY_THRESHOLD, "wait": roi.WAIT_THRESHOLD}
    published = {factor["key"]: factor["weight"] for factor in body["factors"]}
    assert published == roi.FACTOR_WEIGHTS
    assert body["overrides"], "the two safety overrides must be stated"


# ---------------------------------------------------------------------------
# Candidate + evaluation
# ---------------------------------------------------------------------------

async def test_manual_candidate_is_stored_and_evaluated(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()

    resp = await _evaluate(app_client, token, price="2400.00")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] in {"buy", "wait", "skip"}
    assert body["candidate"]["display_name"] == "Ivory Linen Shirt"
    assert body["summary"]

    factory = get_sessionmaker()
    async with factory() as session:
        candidates = (await session.execute(
            select(ShoppingCandidate).where(ShoppingCandidate.account_id == uid)
        )).scalars().all()
    assert len(candidates) == 1
    assert candidates[0].source == "manual"
    assert candidates[0].verification_state == "user_declared"
    assert float(candidates[0].price) == 2400.00


async def test_evaluation_returns_every_scored_factor(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The verdict must arrive with its arithmetic attached: each factor's
    value, weight and contribution, and a score that is their weighted mean."""
    token, _ = await registered_supabase_user()

    body = (await _evaluate(app_client, token, price="2400.00")).json()

    model = body["appearance_roi"]
    factors = model["factors"]
    assert factors, "a verdict with no factors is unauditable"
    for factor in factors:
        assert factor["key"] in roi.FACTOR_WEIGHTS
        assert factor["weight"] == roi.FACTOR_WEIGHTS[factor["key"]]
        assert factor["explanation"], f"{factor['key']} must explain itself"
        assert abs(factor["contribution"] - factor["value"] * factor["weight"]) < 1e-3

    total_weight = sum(f["weight"] for f in factors)
    recomputed = sum(f["contribution"] for f in factors) / total_weight
    assert abs(model["score"] - recomputed) < 1e-3
    assert model["version"] == roi.ROI_VERSION


async def test_missing_price_lowers_confidence_not_the_score(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """A factor with no data is dropped and the rest reweighted — a missing
    price must never be guessed at."""
    token, _ = await registered_supabase_user()

    priced = (await _evaluate(app_client, token, price="2400.00")).json()
    unpriced = (await _evaluate(app_client, token)).json()

    priced_keys = {f["key"] for f in priced["appearance_roi"]["factors"]}
    unpriced_keys = {f["key"] for f in unpriced["appearance_roi"]["factors"]}
    assert "price_context" in priced_keys
    assert "price_context" not in unpriced_keys
    assert unpriced["confidence"] < priced["confidence"]
    assert any("price" in note.lower() for note in unpriced["missing_information"])


async def test_evaluation_compares_against_existing_inventory(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The comparison against what the user owns is the point of the feature.
    An identical owned shirt must come back as a named similar product."""
    token, _ = await registered_supabase_user()
    owned = await _add_item(app_client, token)

    body = (await _evaluate(app_client, token, price="2400.00")).json()

    referenced = {
        row["inventory_item_id"]
        for row in body["similar_owned_products"] + body["existing_alternatives"]
    }
    assert owned["id"] in referenced, "the owned shirt must be surfaced"
    duplicate_factor = next(
        f for f in body["appearance_roi"]["factors"] if f["key"] == "duplicate_penalty"
    )
    assert duplicate_factor["value"] < 1.0, "an owned near-match must cost the score"


async def test_owning_the_same_thing_prevents_a_buy_verdict(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """The override the arithmetic is not allowed to talk us out of: a close
    duplicate of something already owned cannot be a straight Buy."""
    token, _ = await registered_supabase_user()
    for _ in range(2):
        await _add_item(app_client, token)

    body = (await _evaluate(app_client, token, price="2400.00")).json()

    assert body["verdict"] != "buy"
    assert body["similar_owned_products"], "the duplicates must be named, not just counted"
    assert body["headline"]


async def test_nothing_owned_means_no_false_duplicate_claim(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()

    body = (await _evaluate(app_client, token, price="2400.00")).json()

    assert body["similar_owned_products"] == []
    duplicate_factor = next(
        f for f in body["appearance_roi"]["factors"] if f["key"] == "duplicate_penalty"
    )
    assert duplicate_factor["value"] == 1.0
    assert "nothing in this category" in duplicate_factor["explanation"].lower()


async def test_unconfirmed_drafts_are_excluded_and_declared(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """A draft item is not evidence of ownership. It must be left out of the
    comparison and the omission must be stated, not hidden."""
    token, uid = await registered_supabase_user()
    item = await _add_item(app_client, token)

    factory = get_sessionmaker()
    async with factory() as session:
        from app.domains.inventory.models import InventoryItem

        row = await session.get(InventoryItem, uuid.UUID(item["id"]))
        row.verification_state = "draft"
        await session.commit()

    body = (await _evaluate(app_client, token, price="2400.00")).json()

    assert body["similar_owned_products"] == []
    assert any("draft" in note.lower() for note in body["missing_information"])


# ---------------------------------------------------------------------------
# Decisions and follow-up
# ---------------------------------------------------------------------------

async def test_decision_is_recorded_against_the_evaluation(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    evaluation = (await _evaluate(app_client, token, price="2400.00")).json()

    resp = await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        headers=auth(token),
        json={"decision": "skipped", "note": "Already have two of these."},
    )

    assert resp.status_code == 200, resp.text
    decision = resp.json()["decision"]
    assert decision["decision"] == "skipped"
    assert decision["note"] == "Already have two of these."

    factory = get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(PurchaseDecision).where(PurchaseDecision.account_id == uid)
        )).scalars().all()
    assert len(rows) == 1
    assert str(rows[0].evaluation_id) == evaluation["id"]


async def test_decision_records_whether_the_advice_was_followed(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """Recording agreement is what lets the model be checked against outcomes
    later. It must be derived, not taken from the client."""
    token, _ = await registered_supabase_user()
    for _ in range(2):
        await _add_item(app_client, token)
    evaluation = (await _evaluate(app_client, token, price="2400.00")).json()
    assert evaluation["verdict"] in {"wait", "skip"}

    followed = (await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        headers=auth(token),
        json={"decision": "skipped"},
    )).json()["decision"]
    assert followed["followed_recommendation"] is True

    ignored = (await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        headers=auth(token),
        json={"decision": "bought"},
    )).json()["decision"]
    assert ignored["followed_recommendation"] is False


async def test_decision_update_does_not_create_a_second_row(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, uid = await registered_supabase_user()
    evaluation = (await _evaluate(app_client, token, price="2400.00")).json()

    for decision in ("waiting", "waiting", "bought"):
        resp = await app_client.post(
            f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
            headers=auth(token),
            json={"decision": decision},
        )
        assert resp.status_code == 200, resp.text

    factory = get_sessionmaker()
    async with factory() as session:
        count = (await session.execute(
            select(func.count(PurchaseDecision.id)).where(
                PurchaseDecision.account_id == uid
            )
        )).scalar_one()
    assert count == 1, "changing your mind must update the decision, not stack rows"


async def test_invalid_decision_value_is_rejected(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    evaluation = (await _evaluate(app_client, token, price="2400.00")).json()

    resp = await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation['id']}/decision",
        headers=auth(token),
        json={"decision": "purchased_via_app"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency and the beta allowance
# ---------------------------------------------------------------------------

async def test_replayed_request_does_not_double_consume_the_allowance(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    """A retry on a flaky connection must cost the user nothing."""
    token, uid = await registered_supabase_user()
    factory = get_sessionmaker()

    first = await _evaluate(app_client, token, price="2400.00", client_mutation_id="retry-1")
    assert first.status_code == 200, first.text
    second = await _evaluate(app_client, token, price="2400.00", client_mutation_id="retry-1")
    assert second.status_code == 200, second.text

    assert second.json()["id"] == first.json()["id"]

    async with factory() as session:
        evaluations = (await session.execute(
            select(func.count(PurchaseEvaluation.id)).where(
                PurchaseEvaluation.account_id == uid
            )
        )).scalar_one()
        candidates = (await session.execute(
            select(func.count(ShoppingCandidate.id)).where(
                ShoppingCandidate.account_id == uid
            )
        )).scalar_one()
        entitlement = (await session.execute(
            select(RecommendationEntitlement).where(
                RecommendationEntitlement.account_id == uid,
                RecommendationEntitlement.feature == "shopping_evaluation",
            )
        )).scalar_one()

    assert evaluations == 1
    assert candidates == 1
    assert entitlement.used == 1


async def test_allowance_is_neutral_and_never_offers_an_upgrade(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    body = (await _evaluate(app_client, token, price="2400.00")).json()

    entitlement = body["entitlement"]
    assert entitlement["feature"] == "shopping_evaluation"
    assert entitlement["source"] == "beta_grant"
    assert entitlement["remaining"] == entitlement["included"] - entitlement["used"]

    serialised = repr(body).lower()
    for banned in (
        "upgrade", "subscription", "paywall", "checkout", "razorpay",
        "premium plan", "money wasted", "bad wardrobe",
    ):
        assert banned not in serialised, f"'{banned}' must not appear in a verdict"


# ---------------------------------------------------------------------------
# Validation and ownership
# ---------------------------------------------------------------------------

async def test_evaluate_rejects_an_unsupported_category(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    item = dict(LINEN_SHIRT, category="furniture")
    resp = await _evaluate(app_client, token, item=item)
    assert resp.status_code == 422


async def test_evaluate_rejects_an_unknown_occasion_key(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    token, _ = await registered_supabase_user()
    resp = await _evaluate(app_client, token, occasion_key="coronation")
    assert resp.status_code == 422


async def test_evaluate_rejects_another_accounts_photo(
    app_client, db_clean, registered_supabase_user, fake_provider, media_root
):
    from tests.conftest import png_bytes

    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    asset_id = (await app_client.post(
        "/api/v2/media/upload",
        headers=auth(owner_token),
        files={"file": ("a.png", png_bytes(), "image/png")},
    )).json()["id"]

    resp = await app_client.post(
        "/api/v2/shopping/evaluate",
        headers=auth(intruder_token),
        json={"source": "screenshot", "media_asset_id": asset_id},
    )
    assert resp.status_code == 404


async def test_evaluation_read_is_scoped_to_the_owner(
    app_client, db_clean, registered_supabase_user, fake_provider
):
    owner_token, _ = await registered_supabase_user()
    intruder_token, _ = await registered_supabase_user()
    evaluation_id = (await _evaluate(app_client, owner_token, price="2400.00")).json()["id"]

    read = await app_client.get(
        f"/api/v2/shopping/evaluations/{evaluation_id}", headers=auth(intruder_token)
    )
    assert read.status_code == 404

    decision = await app_client.post(
        f"/api/v2/shopping/evaluations/{evaluation_id}/decision",
        headers=auth(intruder_token),
        json={"decision": "bought"},
    )
    assert decision.status_code == 404

    # The owner is unaffected.
    assert (await app_client.get(
        f"/api/v2/shopping/evaluations/{evaluation_id}", headers=auth(owner_token)
    )).status_code == 200


async def test_shopping_requires_authentication(app_client, db_clean):
    resp = await app_client.post(
        "/api/v2/shopping/evaluate",
        json={"source": "manual", "item": dict(LINEN_SHIRT)},
    )
    assert resp.status_code == 401
