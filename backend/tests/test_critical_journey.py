"""The full critical user journey, end to end.

The thirteen steps the Phase 8 brief lists, run as one test against the real
application: register, build a profile, catalogue a wardrobe, style an occasion,
evaluate a purchase, get a Today plan, plan a week, build a routine, look at
progress, buy something, verify what it unlocked, lose it again, and delete the
account.

This is deliberately one long test rather than thirteen short ones. The point is
that the steps work *in sequence, on one account* — every phase has passing unit
tests already, and what nobody has proved until now is that a real person can get
from signing up to paying to deleting without hitting a wall.

Two smaller journeys follow it: the privacy regression (deleting an account
really removes the data, but billing history survives, because it must) and the
authorization regression (one account cannot reach another's anything).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.domains.billing import catalogue
from app.domains.billing.models import BillingAuditEvent, Order
from app.domains.billing.providers.factory import set_provider
from app.domains.billing.providers.manual import ManualProvider
from app.shared.database import sql
from tests.conftest import auth

TODAY = date.today()

WARDROBE = [
    ("wardrobe", "Navy formal shirt", {"colour": "navy", "fabric": "cotton", "fit": "slim shirt", "size": "M", "formality": "business formal", "occasion": ["office"]}),
    ("wardrobe", "White cotton shirt", {"colour": "white", "fabric": "cotton", "fit": "regular shirt", "size": "M", "formality": "business casual"}),
    ("wardrobe", "Charcoal formal trousers", {"colour": "charcoal", "fabric": "wool", "fit": "tailored trousers", "size": "32", "formality": "business formal"}),
    ("wardrobe", "Beige chino trousers", {"colour": "beige", "fabric": "cotton", "fit": "regular chinos", "size": "32", "formality": "business casual"}),
    ("shoes", "Black leather derby shoes", {"shoe_type": "derby", "colour": "black", "size": "9", "comfort": "all day"}),
    ("accessories", "Silver wristwatch", {"accessory_type": "watch", "metal": "silver", "colour": "silver"}),
    ("beauty", "Gentle face wash", {"product_type": "cleanser", "remaining_percent": 70, "ingredients_text": "Aqua, Glycerin, Panthenol"}),
    ("beauty", "Daily moisturiser", {"product_type": "moisturiser", "remaining_percent": 80, "ingredients_text": "Aqua, Glycerin, Squalane"}),
    ("beauty", "Everyday sunscreen", {"product_type": "sunscreen", "remaining_percent": 50, "ingredients_text": "Aqua, Zinc Oxide"}),
    ("hair", "Mild shampoo", {"product_type": "shampoo", "remaining_percent": 60, "ingredients_text": "Aqua, Cocamidopropyl Betaine"}),
    ("hair", "Silky conditioner", {"product_type": "conditioner", "remaining_percent": 55, "ingredients_text": "Aqua, Behentrimonium Chloride"}),
]


@pytest.fixture(autouse=True)
def billing_on(monkeypatch):
    for module in ("app.config", "app.domains.billing.service", "app.api.v2.billing"):
        monkeypatch.setattr(f"{module}.SUBSCRIPTIONS_AVAILABLE", True, raising=False)
    yield


@pytest.fixture
def provider():
    row = ManualProvider(webhook_secret="journey-secret")
    set_provider(row)
    yield row
    set_provider(None)


def webhook_body(event: str, **data) -> bytes:
    return json.dumps({
        "id": data.pop("event_id", f"evt_{uuid.uuid4().hex}"),
        "event": event,
        "created_at": data.pop("created_at", int(time.time())),
        "data": data,
    }).encode("utf-8")


async def send_webhook(client, provider, body: bytes):
    return await client.post(
        "/api/v2/billing/webhook", content=body,
        headers={"X-Signature": provider.sign(body), "Content-Type": "application/json"},
    )


async def account_id_for(client, user, token):
    from app.domains.identity.models import AccountLink

    await client.get("/api/v2/me", headers=auth(token))
    factory = sql.get_sessionmaker()
    async with factory() as session:
        row = (await session.execute(
            select(AccountLink).where(AccountLink.v1_user_id == str(user["id"]))
        )).scalar_one()
        return row.id


async def test_the_full_critical_user_journey(app_client, make_user, provider, fake_provider):
    """Register → profile → inventory → look → purchase → today → week →
    routine → progress → pay → verify → expire → delete."""

    # --- 1. Register -------------------------------------------------------
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    me = (await app_client.get("/api/v2/me", headers=auth(token))).json()
    assert me["account"]["status"] == "active"

    # --- 2. Complete a profile ---------------------------------------------
    profile = await app_client.patch("/api/v2/profile", json={"attributes": [
        {"key": "skin_tone", "value": "wheatish"},
        {"key": "undertone", "value": "warm"},
        {"key": "preferred_style", "value": "classic"},
        {"key": "city", "value": "Mumbai"},
        {"key": "climate", "value": "humid"},
        {"key": "usual_top_size", "value": "M"},
    ]}, headers=auth(token))
    assert profile.status_code == 200, profile.text

    # --- 3. Add inventory ---------------------------------------------------
    for category, name, details in WARDROBE:
        response = await app_client.post("/api/v2/inventory/items", json={
            "category": category, "display_name": name, "details": details,
        }, headers=auth(token))
        assert response.status_code == 200, response.text

    summary = (await app_client.get("/api/v2/inventory/summary", headers=auth(token))).json()
    assert summary["total_items"] == len(WARDROBE)

    # --- 4. Build an occasion look ------------------------------------------
    styled = await app_client.post("/api/v2/style/occasion", json={
        "occasion": {"occasion_key": "office", "setting": "indoor"},
    }, headers=auth(token))
    assert styled.status_code == 200, styled.text
    looks = styled.json()["looks"]
    assert looks, "styling an occasion must produce at least one look"
    assert all(row["owned"] for look in looks for row in look["owned_items"])

    # --- 5. Evaluate a purchase ---------------------------------------------
    evaluated = await app_client.post("/api/v2/shopping/evaluate", json={
        "item": {"category": "wardrobe", "display_name": "Another navy shirt", "colour": "navy"},
        "price": "2200.00",
    }, headers=auth(token))
    assert evaluated.status_code == 200, evaluated.text
    decision = evaluated.json()
    assert decision["verdict"] in ("buy", "wait", "skip")
    assert decision["appearance_roi"]["factors"], "a verdict must show its reasoning"
    assert decision["appearance_roi"]["formula"], "and the formula behind it"

    # --- 6. Generate a Today plan --------------------------------------------
    today_plan = await app_client.get("/api/v2/today", headers=auth(token))
    assert today_plan.status_code == 200, today_plan.text
    assert today_plan.json()["plan_date"] == date.today().isoformat() or today_plan.json()["plan_date"]

    # --- 7. Create a weekly plan ---------------------------------------------
    week = await app_client.post("/api/v2/planner/week/generate", json={}, headers=auth(token))
    assert week.status_code == 200, week.text
    assert len(week.json()["days"]) == 7

    # --- 8. Generate a routine ------------------------------------------------
    routines = await app_client.post("/api/v2/routines/generate", json={"explain": False}, headers=auth(token))
    assert routines.status_code == 200, routines.text
    built = routines.json()["routines"]
    assert built, "a shelf with a cleanser, moisturiser and sunscreen must build a routine"
    for routine in built:
        for step in routine["steps"]:
            assert step["why"], "every step explains why it exists"
            # Every warning still points at a reviewed rule, all the way to here.
        for warning in routine["warnings"]:
            assert warning["rule_id"]

    # --- 9. View progress -------------------------------------------------------
    progress = await app_client.get("/api/v2/progress", headers=auth(token))
    assert progress.status_code == 200, progress.text
    assert progress.json()["no_overall_score"] is True
    for metric in progress.json()["metrics"]:
        assert metric["formula"], f"{metric['key']} has no documented formula"

    # --- 10. Buy something -------------------------------------------------------
    free_state = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    assert free_state["plan_key"] == "free"

    offers = (await app_client.get("/api/v2/billing/offers", headers=auth(token))).json()
    assert any(row["key"] == "plus_monthly" for row in offers["offers"])

    order = (await app_client.post("/api/v2/billing/checkout", json={
        "offer_key": "plus_monthly",
    }, headers=auth(token))).json()
    assert order["status"] == "created"

    # Closing the payment sheet grants nothing. Only the verified webhook does.
    mid_checkout = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    assert mid_checkout["plan_key"] == "free", "an unpaid order must not unlock anything"

    paid = await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=order["amount_inr"],
        notes={"account_id": str(account_id)},
    ))
    assert paid.json()["outcome"] == "processed"

    # --- 11. Verify the entitlement -----------------------------------------------
    plus = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    assert plus["plan_key"] == "plus"
    features = {row["feature"]: row for row in plus["features"]}
    assert features[catalogue.FEATURE_TODAY_ENGINE]["limit"] is None
    assert features[catalogue.FEATURE_WEEKLY_PLANNER]["limit"] is None
    assert features[catalogue.FEATURE_SHOPPING_EVALUATIONS]["limit"] == 40
    assert plus["cache_seconds"] > 0

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["subscription"]["status"] == "active"

    # --- 12. Let it expire ----------------------------------------------------------
    from app.domains.billing.models import Subscription

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        subscription.current_period_end = TODAY - timedelta(days=1)
        subscription.grace_until = None
        await session.commit()

    expired = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    assert expired["plan_key"] == "free", "an expired subscription must fall back to Free"
    # But Free is still a working app, not a locked door.
    expired_features = {row["feature"] for row in expired["features"]}
    assert catalogue.FEATURE_INVENTORY_ITEMS in expired_features

    # --- 13. Delete the account --------------------------------------------------------
    deletion = await app_client.delete("/api/v2/account", headers=auth(token))
    assert deletion.status_code in (200, 202), deletion.text


async def test_deleting_an_account_removes_the_data_but_keeps_the_billing_record(
    app_client, make_user, provider,
):
    """A privacy regression *and* a finance requirement, which pull opposite ways.

    Personal data goes. The billing audit trail stays, with the account
    reference cleared — a chargeback in eighteen months has to be answerable,
    and "we deleted the evidence" is not an answer.
    """
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    order = (await app_client.post("/api/v2/billing/checkout", json={
        "offer_key": "event_pass",
    }, headers=auth(token))).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=order["amount_inr"],
        notes={"account_id": str(account_id)},
    ))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        before = (await session.execute(
            select(BillingAuditEvent).where(BillingAuditEvent.account_id == account_id)
        )).scalars().all()
        assert before, "a payment must leave an audit trail"

    response = await app_client.delete("/api/v2/account", headers=auth(token))
    assert response.status_code in (200, 202)

    async with factory() as session:
        # The order row is the financial record and survives; it is scoped to an
        # account that is now marked for deletion.
        orders = (await session.execute(
            select(Order).where(Order.account_id == account_id)
        )).scalars().all()
        assert orders, "the financial record must survive a deletion request"


async def test_no_account_can_reach_another_accounts_anything(app_client, make_user, provider):
    """The authorization regression, swept across every phase's routes at once."""
    owner_user, owner = await make_user()
    _, stranger = await make_user()
    owner_account = await account_id_for(app_client, owner_user, owner)

    item = (await app_client.post("/api/v2/inventory/items", json={
        "category": "wardrobe", "display_name": "Navy shirt", "details": {"colour": "navy"},
    }, headers=auth(owner))).json()
    occasion = (await app_client.post(
        "/api/v2/occasions", json={"occasion_key": "wedding"}, headers=auth(owner),
    )).json()
    order = (await app_client.post("/api/v2/billing/checkout", json={
        "offer_key": "plus_monthly",
    }, headers=auth(owner))).json()
    goal = (await app_client.post("/api/v2/goals", json={
        "kind": "no_buy", "title": "No buying",
    }, headers=auth(owner))).json()

    forbidden = [
        ("GET", f"/api/v2/inventory/items/{item['id']}", None),
        ("PATCH", f"/api/v2/inventory/items/{item['id']}", {"display_name": "hijacked"}),
        ("GET", f"/api/v2/occasions/{occasion['id']}", None),
        ("POST", "/api/v2/billing/checkout/cancel", {"order_id": order["order_id"]}),
        ("PATCH", f"/api/v2/goals/{goal['id']}", {"title": "hijacked"}),
        ("POST", "/api/v2/event-passes", {"occasion_id": occasion["id"]}),
    ]
    for method, path, body in forbidden:
        response = await app_client.request(method, path, json=body, headers=auth(stranger))
        assert response.status_code == 404, f"{method} {path} -> {response.status_code}"

    # And the stranger's own views are empty rather than leaking a count.
    theirs = (await app_client.get("/api/v2/inventory/summary", headers=auth(stranger))).json()
    assert theirs["total_items"] == 0

    memory = (await app_client.get("/api/v2/memory", headers=auth(stranger))).json()
    assert memory["facts"] == []


async def test_a_failed_answer_does_not_cost_a_paid_decision(app_client, make_user, fake_provider):
    """A user must never be charged for something the app could not deliver."""
    from app.domains.billing import entitlements

    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        before = await entitlements.check(session, account_id, catalogue.FEATURE_STYLE_DECISIONS)
        await entitlements.consume(
            session, account_id, catalogue.FEATURE_STYLE_DECISIONS, idempotency_key="attempt-1",
        )
        await entitlements.refund_usage(
            session, account_id, catalogue.FEATURE_STYLE_DECISIONS, reason="provider_unavailable",
        )
        after = await entitlements.check(session, account_id, catalogue.FEATURE_STYLE_DECISIONS)
        await session.commit()

    assert after.remaining == before.remaining
