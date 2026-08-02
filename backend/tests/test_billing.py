"""Phase 8 billing, entitlement and release contracts.

The promises this phase has to keep are all about money, so they are tested as
behaviour rather than asserted:

* a forged webhook grants nothing,
* a webhook delivered five times grants once,
* an out-of-order event cannot walk the state machine backwards,
* a refund takes back exactly what that payment bought and nothing else,
* and the paywall sells outcomes, not model usage.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.domains.billing import catalogue, entitlements, operations
from app.domains.billing.models import (
    Entitlement, EntitlementUsage, Order, PaymentEvent, Refund, Subscription,
)
from app.domains.billing.providers import base as provider_base
from app.domains.billing.providers.factory import set_provider
from app.domains.billing.providers.manual import ManualProvider
from app.domains.billing.providers.razorpay import RazorpayProvider
from app.shared.database import sql
from tests.conftest import auth

TODAY = date.today()


@pytest.fixture(autouse=True)
def billing_on(monkeypatch):
    """Billing is off by default in this product. Turn it on for these tests.

    Patched everywhere it was imported by value, because ``from x import Y``
    binds a copy and patching only the source module would leave the routes
    reading the old value.
    """
    for module in (
        "app.config",
        "app.domains.billing.service",
        "app.api.v2.billing",
    ):
        monkeypatch.setattr(f"{module}.SUBSCRIPTIONS_AVAILABLE", True, raising=False)
    yield


@pytest.fixture
def provider():
    row = ManualProvider(webhook_secret="test-webhook-secret")
    set_provider(row)
    yield row
    set_provider(None)


def webhook_body(event: str, **data) -> bytes:
    payload = {
        "id": data.pop("event_id", f"evt_{uuid.uuid4().hex[:12]}"),
        "event": event,
        "created_at": data.pop("created_at", int(time.time())),
        "data": data,
    }
    return json.dumps(payload).encode("utf-8")


async def send_webhook(client, provider, body: bytes, *, signature: str | None = None):
    return await client.post(
        "/api/v2/billing/webhook",
        content=body,
        headers={
            "X-Signature": signature if signature is not None else provider.sign(body),
            "Content-Type": "application/json",
        },
    )


async def checkout(client, token, offer_key="plus_monthly", **body):
    return await client.post(
        "/api/v2/billing/checkout", json={"offer_key": offer_key, **body}, headers=auth(token),
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


def all_text(payload) -> str:
    if isinstance(payload, str):
        return payload + " "
    if isinstance(payload, dict):
        return "".join(all_text(value) for value in payload.values())
    if isinstance(payload, list):
        return "".join(all_text(value) for value in payload)
    return ""


# --- Authorization ----------------------------------------------------------------


async def test_every_billing_route_except_the_webhook_requires_authentication(app_client):
    calls = [
        ("GET", "/api/v2/billing/offers", None),
        ("POST", "/api/v2/billing/checkout", {"offer_key": "plus_monthly"}),
        ("GET", "/api/v2/billing/status", None),
        ("GET", "/api/v2/entitlements", None),
        ("POST", "/api/v2/event-passes", {"occasion_id": str(uuid.uuid4())}),
        ("GET", "/api/v2/account/usage", None),
        ("POST", "/api/v2/support", {"category": "billing", "subject": "x", "message": "y"}),
        ("POST", "/api/v2/stylist-review", {"subject_type": "look", "question": "x"}),
    ]
    for method, path, body in calls:
        response = await app_client.request(method, path, json=body)
        assert response.status_code in (401, 403), f"{method} {path} -> {response.status_code}"


async def test_a_client_cannot_name_the_price_it_pays(app_client, make_user):
    """There is no amount field. A client that could name a price could name zero."""
    _, token = await make_user()
    response = await app_client.post(
        "/api/v2/billing/checkout",
        json={"offer_key": "plus_monthly", "amount_inr": 1},
        headers=auth(token),
    )
    assert response.status_code == 422


async def test_a_client_cannot_grant_itself_a_plan(app_client, make_user):
    _, token = await make_user()
    for body in (
        {"offer_key": "plus_monthly", "plan_key": "plus"},
        {"offer_key": "plus_monthly", "account_id": str(uuid.uuid4())},
        {"offer_key": "plus_monthly", "status": "paid"},
    ):
        response = await app_client.post("/api/v2/billing/checkout", json=body, headers=auth(token))
        assert response.status_code == 422, body


async def test_an_unknown_offer_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await checkout(app_client, token, offer_key="free_forever_please")
    assert response.status_code == 422


async def test_one_users_order_cannot_be_cancelled_by_another(app_client, make_user, provider):
    _, owner = await make_user()
    _, stranger = await make_user()
    order = (await checkout(app_client, owner)).json()
    response = await app_client.post(
        "/api/v2/billing/checkout/cancel",
        json={"order_id": order["order_id"]}, headers=auth(stranger),
    )
    assert response.status_code == 404


async def test_an_event_pass_cannot_be_attached_to_someone_elses_occasion(app_client, make_user, provider):
    _, owner = await make_user()
    _, stranger = await make_user()
    occasion = (await app_client.post(
        "/api/v2/occasions", json={"occasion_key": "wedding"}, headers=auth(owner),
    )).json()
    response = await app_client.post(
        "/api/v2/event-passes", json={"occasion_id": occasion["id"]}, headers=auth(stranger),
    )
    assert response.status_code == 404


# --- Signature verification ----------------------------------------------------------


async def test_a_forged_webhook_grants_nothing(app_client, make_user, provider):
    """The single most important test in this file."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    body = webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    )
    response = await send_webhook(app_client, provider, body, signature="not-a-real-signature")
    assert response.status_code == 422

    factory = sql.get_sessionmaker()
    async with factory() as session:
        # Nothing written at all — not even an audit row an attacker chose.
        events = (await session.execute(select(PaymentEvent))).scalars().all()
        assert all(row.provider_order_id != order["provider_order_id"] for row in events)
        granted = (await session.execute(
            select(Entitlement).where(
                Entitlement.account_id == account_id, Entitlement.plan_key == "plus",
            )
        )).scalars().all()
        assert granted == []


async def test_a_webhook_with_no_signature_is_refused(app_client, provider):
    body = webhook_body("payment.succeeded", order_id="x", amount_inr=1)
    response = await app_client.post(
        "/api/v2/billing/webhook", content=body, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


async def test_a_tampered_body_fails_verification(app_client, make_user, provider):
    """Signing one body and sending another must not work."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    honest = webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    )
    signature = provider.sign(honest)
    tampered = honest.replace(b'"amount_inr": 399', b'"amount_inr": 1')

    response = await send_webhook(app_client, provider, tampered, signature=signature)
    assert response.status_code == 422


def test_razorpay_signature_verification_matches_the_documented_algorithm():
    """HMAC-SHA256 of the raw body, hex digest, keyed with the webhook secret."""
    import hashlib
    import hmac

    secret = "razorpay-webhook-secret"
    row = RazorpayProvider(key_id="rzp_test_x", key_secret="s", webhook_secret=secret)
    body = b'{"event":"payment.captured","payload":{}}'
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert row.verify_webhook(raw_body=body, signature=expected) == {
        "event": "payment.captured", "payload": {},
    }
    with pytest.raises(provider_base.SignatureInvalidError):
        row.verify_webhook(raw_body=body, signature="0" * 64)
    with pytest.raises(provider_base.SignatureInvalidError):
        row.verify_webhook(raw_body=body + b" ", signature=expected)


def test_razorpay_checkout_callback_signature_is_order_pipe_payment():
    import hashlib
    import hmac

    row = RazorpayProvider(key_id="rzp_test_x", key_secret="key-secret", webhook_secret="w")
    expected = hmac.new(
        b"key-secret", b"order_abc|pay_def", hashlib.sha256,
    ).hexdigest()
    assert row.verify_payment_signature(
        order_id="order_abc", payment_id="pay_def", signature=expected,
    ) is True
    assert row.verify_payment_signature(
        order_id="order_abc", payment_id="pay_def", signature="0" * 64,
    ) is False


def test_razorpay_maps_its_event_names_onto_our_vocabulary():
    row = RazorpayProvider(key_id="k", key_secret="s", webhook_secret="w")
    event = row.parse_event({
        "event": "subscription.charged",
        "created_at": 1234567890,
        "payload": {
            "subscription": {"entity": {"id": "sub_1"}},
            "payment": {"entity": {"id": "pay_1", "amount": 39900, "order_id": "order_1"}},
        },
    })
    assert event.kind == provider_base.EVENT_SUBSCRIPTION_RENEWED
    # Paise to rupees. Getting this wrong by 100 is the classic bug.
    assert event.amount_inr == 399
    assert event.provider_subscription_id == "sub_1"


def test_an_unrecognised_razorpay_event_is_not_guessed_at():
    row = RazorpayProvider(key_id="k", key_secret="s", webhook_secret="w")
    event = row.parse_event({"event": "invoice.expired", "payload": {}})
    assert event.kind == provider_base.EVENT_UNKNOWN


def test_a_razorpay_provider_without_keys_reports_itself_unconfigured():
    assert RazorpayProvider(key_id="", key_secret="", webhook_secret="").is_configured() is False


# --- Duplicate, delayed and out-of-order --------------------------------------------------


async def test_the_same_webhook_five_times_grants_once(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    event_id = f"evt_{uuid.uuid4().hex}"
    body = webhook_body(
        "payment.succeeded", event_id=event_id,
        order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    )
    outcomes = []
    for _ in range(5):
        response = await send_webhook(app_client, provider, body)
        assert response.status_code == 200, response.text
        outcomes.append(response.json()["outcome"])

    assert outcomes[0] == "processed"
    assert all(row == "duplicate" for row in outcomes[1:])

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscriptions = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalars().all()
        assert len(subscriptions) == 1
        rows = (await session.execute(
            select(Entitlement).where(
                Entitlement.account_id == account_id,
                Entitlement.feature == catalogue.FEATURE_STYLE_DECISIONS,
                Entitlement.plan_key == "plus",
            )
        )).scalars().all()
        assert len(rows) == 1


async def test_a_delayed_webhook_still_grants(app_client, make_user, provider):
    """Nothing reads the clock to decide what a payment means."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    long_ago = int(time.time()) - 7200
    body = webhook_body(
        "payment.succeeded", created_at=long_ago,
        order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    )
    response = await send_webhook(app_client, provider, body)
    assert response.json()["outcome"] == "processed"

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "plus"


async def test_an_out_of_order_event_cannot_walk_the_state_backwards(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    now = int(time.time())
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", created_at=now, order_id=order["provider_order_id"],
        amount_inr=399, notes={"account_id": str(account_id)},
    ))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        sub_ooo = f"sub_ooo_{uuid.uuid4().hex[:8]}"
        subscription.provider_subscription_id = sub_ooo
        await session.commit()

    # A cancellation stamped *earlier* than the activation already applied.
    response = await send_webhook(app_client, provider, webhook_body(
        "subscription.cancelled", created_at=now - 3600, subscription_id=sub_ooo,
        notes={"account_id": str(account_id)},
    ))
    assert response.json()["outcome"] == "out_of_order"

    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        assert subscription.status == "active"


async def test_an_unmatched_payment_is_recorded_rather_than_discarded(app_client, provider):
    """Exactly what support needs to see when something has gone wrong."""
    body = webhook_body("payment.succeeded", order_id="order_nobody_has", amount_inr=399)
    response = await send_webhook(app_client, provider, body)
    assert response.json()["outcome"] == "unmatched"

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(PaymentEvent).where(PaymentEvent.provider_order_id == "order_nobody_has")
        )).scalars().all()
        assert rows and rows[0].signature_verified is True


async def test_an_amount_mismatch_is_not_reconciled_silently(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    response = await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=1,
        notes={"account_id": str(account_id)},
    ))
    assert response.json()["outcome"] == "unmatched"

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "free"


async def test_a_failed_payment_grants_nothing(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    await send_webhook(app_client, provider, webhook_body(
        "payment.failed", order_id=order["provider_order_id"],
        notes={"account_id": str(account_id)},
    ))
    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "free"


# --- Subscription life ---------------------------------------------------------------------


async def test_a_renewal_extends_the_period(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()
    now = int(time.time())

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", created_at=now, order_id=order["provider_order_id"],
        amount_inr=399, notes={"account_id": str(account_id)},
    ))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        sub_renew = f"sub_renew_{uuid.uuid4().hex[:8]}"
        subscription.provider_subscription_id = sub_renew
        subscription.current_period_end = TODAY + timedelta(days=1)
        await session.commit()

    response = await send_webhook(app_client, provider, webhook_body(
        "subscription.renewed", created_at=now + 60, subscription_id=sub_renew,
        notes={"account_id": str(account_id)},
    ))
    assert response.json()["outcome"] == "processed"

    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        assert subscription.current_period_end > TODAY + timedelta(days=1)
        assert subscription.status == "active"


async def test_a_failed_renewal_keeps_access_during_the_grace_period(app_client, make_user, provider):
    """Cutting somebody off the instant a card is declined loses a customer who would have paid."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()
    now = int(time.time())

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", created_at=now, order_id=order["provider_order_id"],
        amount_inr=399, notes={"account_id": str(account_id)},
    ))
    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        sub_grace = f"sub_grace_{uuid.uuid4().hex[:8]}"
        subscription.provider_subscription_id = sub_grace
        await session.commit()

    await send_webhook(app_client, provider, webhook_body(
        "subscription.payment_failed", created_at=now + 60, subscription_id=sub_grace,
        notes={"account_id": str(account_id)},
    ))

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["subscription"]["status"] == "grace"
    assert status["plan_key"] == "plus", "access continues while the payment is retried"


async def test_cancelling_keeps_access_until_the_period_ends(app_client, make_user, provider):
    """Somebody who has paid for this month has paid for this month."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()
    now = int(time.time())

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", created_at=now, order_id=order["provider_order_id"],
        amount_inr=399, notes={"account_id": str(account_id)},
    ))
    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        sub_cancel = f"sub_cancel_{uuid.uuid4().hex[:8]}"
        subscription.provider_subscription_id = sub_cancel
        await session.commit()

    await send_webhook(app_client, provider, webhook_body(
        "subscription.cancelled", created_at=now + 60, subscription_id=sub_cancel,
        notes={"account_id": str(account_id)},
    ))

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "plus"
    assert status["subscription"]["cancel_at_period_end"] is True


async def test_an_expired_subscription_loses_access(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))

    factory = sql.get_sessionmaker()
    async with factory() as session:
        subscription = (await session.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        )).scalar_one()
        subscription.current_period_end = TODAY - timedelta(days=1)
        subscription.grace_until = None
        await session.commit()

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "free"
    assert status["subscription"] is None


async def test_expiry_is_enforced_at_read_time_not_only_by_a_job(app_client, make_user, provider):
    """A job that never runs must not leave somebody with access they no longer have."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        await entitlements.grant_plan(
            session, account_id, "plus", source="plan", source_id=uuid.uuid4(),
            starts_on=TODAY - timedelta(days=40), expires_on=TODAY - timedelta(days=1),
        )
        await session.commit()
        active = await entitlements.active_for(session, account_id)
        assert all(row.plan_key != "plus" for row in active)


# --- Refunds ---------------------------------------------------------------------------------


async def test_a_refund_revokes_what_that_payment_bought(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))
    assert (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()["plan_key"] == "plus"

    response = await send_webhook(app_client, provider, webhook_body(
        "refund.processed", order_id=order["provider_order_id"], refund_id=f"rfnd_{uuid.uuid4().hex[:10]}",
        amount_inr=399, notes={"account_id": str(account_id)},
    ))
    assert response.json()["outcome"] == "processed"

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "free"


async def test_a_refund_does_not_touch_a_different_purchase(app_client, make_user, provider):
    """Refunding a pass must not cancel a subscription the same person pays for."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    subscription_order = (await checkout(app_client, token, offer_key="plus_monthly")).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=subscription_order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))

    pass_order = (await checkout(app_client, token, offer_key="event_pass")).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=pass_order["provider_order_id"], amount_inr=499,
        notes={"account_id": str(account_id)},
    ))

    await send_webhook(app_client, provider, webhook_body(
        "refund.processed", order_id=pass_order["provider_order_id"], refund_id=f"rfnd_{uuid.uuid4().hex[:10]}",
        amount_inr=499, notes={"account_id": str(account_id)},
    ))

    status = (await app_client.get("/api/v2/billing/status", headers=auth(token))).json()
    assert status["plan_key"] == "plus", "the subscription survives a refund of the pass"


async def test_the_same_refund_twice_is_processed_once(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))

    refund_id = f"rfnd_{uuid.uuid4().hex[:10]}"
    first = await send_webhook(app_client, provider, webhook_body(
        "refund.processed", event_id=f"evt_{uuid.uuid4().hex[:10]}",
        order_id=order["provider_order_id"],
        refund_id=refund_id, amount_inr=399, notes={"account_id": str(account_id)},
    ))
    second = await send_webhook(app_client, provider, webhook_body(
        "refund.processed", event_id=f"evt_{uuid.uuid4().hex[:10]}",
        order_id=order["provider_order_id"],
        refund_id=refund_id, amount_inr=399, notes={"account_id": str(account_id)},
    ))
    assert first.json()["outcome"] == "processed"
    assert second.json()["outcome"] == "duplicate"

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(Refund).where(Refund.account_id == account_id)
        )).scalars().all()
        assert len(rows) == 1


# --- Event pass -------------------------------------------------------------------------------


async def test_an_event_pass_grants_its_features_for_a_fixed_window(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    occasion = (await app_client.post(
        "/api/v2/occasions", json={"occasion_key": "wedding"}, headers=auth(token),
    )).json()

    order = (await app_client.post(
        "/api/v2/event-passes", json={"occasion_id": occasion["id"]}, headers=auth(token),
    )).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=499,
        notes={"account_id": str(account_id)},
    ))

    body = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    features = {row["feature"]: row for row in body["features"]}
    assert features[catalogue.FEATURE_EVENT_LOOKS]["limit"] == 3
    assert features[catalogue.FEATURE_EVENT_REVISIONS]["limit"] == 6
    assert features[catalogue.FEATURE_EVENT_LOOKS]["expires_on"] is not None


# --- Entitlements and usage --------------------------------------------------------------------


async def test_a_new_account_gets_working_free_entitlements(app_client, make_user):
    """Free is a real plan. Somebody who never pays has a working app."""
    _, token = await make_user()
    body = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    features = {row["feature"] for row in body["features"]}
    assert body["plan_key"] == "free"
    assert catalogue.FEATURE_INVENTORY_ITEMS in features
    assert catalogue.FEATURE_STYLE_DECISIONS in features


async def test_usage_is_capped_at_the_free_limit(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    limit = catalogue.FREE.limits[catalogue.FEATURE_SHOPPING_EVALUATIONS]

    factory = sql.get_sessionmaker()
    async with factory() as session:
        for index in range(limit):
            decision = await entitlements.consume(
                session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS,
                idempotency_key=f"call-{index}",
            )
            assert decision.allowed is True
        blocked = await entitlements.consume(
            session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS, idempotency_key="one-too-many",
        )
        assert blocked.allowed is False
        assert "used all" in blocked.reason
        await session.commit()


async def test_a_retried_request_is_not_charged_twice(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        for _ in range(4):
            await entitlements.consume(
                session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS,
                idempotency_key="same-request",
            )
        await session.commit()
        rows = (await session.execute(
            select(EntitlementUsage).where(
                EntitlementUsage.account_id == account_id,
                EntitlementUsage.idempotency_key == "same-request",
            )
        )).scalars().all()
        assert len(rows) == 1


async def test_failed_work_gives_the_allowance_back(app_client, make_user):
    """A failed answer must not cost somebody one of their decisions."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)

    factory = sql.get_sessionmaker()
    async with factory() as session:
        await entitlements.consume(
            session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS, idempotency_key="will-fail",
        )
        before = await entitlements.check(session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS)
        await entitlements.refund_usage(
            session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS, reason="provider_error",
        )
        after = await entitlements.check(session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS)
        assert after.remaining == before.remaining + 1
        await session.commit()


async def test_a_paid_plan_beats_the_free_limit(app_client, make_user, provider):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()
    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))

    body = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    features = {row["feature"]: row for row in body["features"]}
    assert features[catalogue.FEATURE_SHOPPING_EVALUATIONS]["limit"] == 40
    assert features[catalogue.FEATURE_TODAY_ENGINE]["limit"] is None


async def test_the_usage_route_itemises_what_was_spent_and_credited(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    factory = sql.get_sessionmaker()
    async with factory() as session:
        await entitlements.consume(
            session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS, idempotency_key="a",
        )
        await entitlements.refund_usage(
            session, account_id, catalogue.FEATURE_SHOPPING_EVALUATIONS,
        )
        await session.commit()

    body = (await app_client.get("/api/v2/account/usage", headers=auth(token))).json()
    assert any(row["credit"] for row in body["history"])
    assert any(not row["credit"] for row in body["history"])


# --- The offline cache ---------------------------------------------------------------------------


async def test_the_entitlement_snapshot_is_cacheable_but_expires(app_client, make_user):
    """A cached entitlement that never expires is a refunded plan that keeps working."""
    _, token = await make_user()
    body = (await app_client.get("/api/v2/entitlements", headers=auth(token))).json()
    assert body["cache_seconds"] > 0
    assert body["cache_seconds"] <= 3600
    assert body["valid_until"]
    assert "checks again" in body["offline_note"]


# --- The kill switch --------------------------------------------------------------------------------


async def test_checkout_is_refused_at_the_first_step_when_billing_is_off(app_client, make_user, monkeypatch):
    """A Phase 1 fix that still holds: never invite somebody to pay and then refuse."""
    for module in ("app.config", "app.domains.billing.service", "app.api.v2.billing"):
        monkeypatch.setattr(f"{module}.SUBSCRIPTIONS_AVAILABLE", False, raising=False)

    _, token = await make_user()
    response = await checkout(app_client, token)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUBSCRIPTIONS_UNAVAILABLE"


async def test_offers_stay_readable_when_billing_is_off(app_client, make_user, monkeypatch):
    for module in ("app.config", "app.domains.billing.service", "app.api.v2.billing"):
        monkeypatch.setattr(f"{module}.SUBSCRIPTIONS_AVAILABLE", False, raising=False)

    _, token = await make_user()
    body = (await app_client.get("/api/v2/billing/offers", headers=auth(token))).json()
    assert body["billing_available"] is False
    assert body["offers"]
    assert "nothing to pay for yet" in body["message"]


# --- The paywall sells outcomes ------------------------------------------------------------------------


def test_no_plan_sells_model_usage():
    """The acceptance criterion, tested rather than trusted."""
    catalogue.validate_catalogue()
    for plan in catalogue.PLANS:
        text = " ".join(
            [plan.tagline] + [f"{row.headline} {row.detail}" for row in plan.benefits]
        ).lower()
        for word in catalogue.FORBIDDEN_BENEFIT_WORDS:
            assert word not in text, f"{plan.key} sells {word!r}"


def test_a_plan_selling_unlimited_ai_is_rejected():
    original = catalogue.PLANS
    bad = catalogue.Plan(
        key="bad", label="Bad", tagline="Unlimited AI for everyone",
        limits={}, benefits=[catalogue.Benefit("Unlimited AI", "More tokens.")],
    )
    catalogue.PLANS = original + (bad,)
    try:
        with pytest.raises(AssertionError, match="monetises outcomes"):
            catalogue.validate_catalogue()
    finally:
        catalogue.PLANS = original


def test_the_paywall_leads_with_the_outcomes_the_brief_asks_for():
    headlines = " ".join(row.headline.lower() for row in catalogue.PLUS.benefits)
    for expected in (
        "plan every week", "use your complete wardrobe", "shopping decisions",
        "important events", "routines using what you own", "long-term memory",
    ):
        assert expected in headlines, expected


async def test_no_billing_response_advertises_ai_volume(app_client, make_user, provider):
    _, token = await make_user()
    for path in ("/api/v2/billing/offers", "/api/v2/billing/status", "/api/v2/entitlements"):
        body = (await app_client.get(path, headers=auth(token))).json()
        text = all_text(body).lower()
        for banned in ("unlimited ai", "unlimited scans", "more tokens", "ai calls"):
            assert banned not in text, f"{path} advertises {banned!r}"


def test_prices_come_from_configuration_not_from_literals():
    """A price is a business decision. It must never need a deploy."""
    import inspect

    source = inspect.getsource(catalogue)
    body = source.split("def offers()")[1]
    for offer in catalogue.offers():
        assert str(offer.amount_inr) not in body, (
            f"{offer.key} has a hard-coded price in catalogue.py"
        )


# --- Experiments -------------------------------------------------------------------------------------


async def test_an_experiment_assignment_is_stable(app_client, make_user):
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.billing.models import Experiment

    factory = sql.get_sessionmaker()
    async with factory() as session:
        key = f"paywall_headline_{uuid.uuid4().hex[:8]}"
        session.add(Experiment(
            key=key, label="Paywall headline",
            surface=operations.SURFACE_PAYWALL_COPY, variants=["a", "b"], status="running",
        ))
        await session.commit()
        first = await operations.assign(session, account_id, key)
        await session.commit()
        second = await operations.assign(session, account_id, key)
        assert first == second
        assert first in ("a", "b")


async def test_an_experiment_on_a_safety_surface_is_refused(app_client, make_user):
    """Experimenting on a protection means giving somebody the weaker version."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    from app.domains.billing.models import Experiment

    factory = sql.get_sessionmaker()
    async with factory() as session:
        key = f"skip_the_disclaimer_{uuid.uuid4().hex[:8]}"
        session.add(Experiment(
            key=key, label="Drop the medical disclaimer",
            surface="medical_disclaimer", variants=["on", "off"], status="running",
        ))
        await session.commit()
        assert await operations.assign(session, account_id, key) is None


# --- Cost tracking ------------------------------------------------------------------------------------


async def test_ai_cost_is_measurable_per_feature(app_client, make_user, fake_provider):
    """"We spent $40" is not actionable. "Shopping costs 8x styling" is."""
    from app.domains.ai_gateway.models import AIRun

    factory = sql.get_sessionmaker()
    async with factory() as session:
        for feature, cost in (("style_occasion_explanation", 0.02), ("shopping_candidate_extract", 0.16)):
            session.add(AIRun(
                feature=feature, provider="fake", status="succeeded", model="fake",
                input_tokens=1000, output_tokens=500, estimated_cost_usd=cost,
                prompt_version="v1", schema_version="v1",
            ))
        await session.commit()
        await operations.roll_up_costs(session)
        await session.commit()
        dashboard = await operations.cost_dashboard(session)

    features = {row["feature"]: row for row in dashboard["features"]}
    assert "shopping_candidate_extract" in features
    assert features["shopping_candidate_extract"]["cost_per_run_usd"] is not None
    assert dashboard["total_estimated_cost_usd"] > 0


# --- Support and professional seams ---------------------------------------------------------------------


async def test_a_support_case_carries_the_billing_context(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post("/api/v2/support", json={
        "category": "billing", "subject": "Question about my plan",
        "message": "What am I on?",
    }, headers=auth(token))).json()
    assert body["status"] == "open"

    from app.domains.billing.models import SupportCase

    factory = sql.get_sessionmaker()
    async with factory() as session:
        case = await session.get(SupportCase, uuid.UUID(body["id"]))
        assert case.billing_snapshot.get("plan_key") == "free"


async def test_a_double_charge_report_is_treated_as_urgent(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post("/api/v2/support", json={
        "category": "billing", "subject": "Charged twice",
        "message": "I think I was charged twice for Plus.",
    }, headers=auth(token))).json()
    assert body["severity"] == "high"


async def test_a_stylist_review_is_queued_and_does_not_pretend_to_be_human(app_client, make_user):
    _, token = await make_user()
    body = (await app_client.post("/api/v2/stylist-review", json={
        "subject_type": "look", "question": "Does this work for a Mumbai wedding?",
    }, headers=auth(token))).json()
    assert body["status"] == "queued"
    assert "Nobody has looked at it yet" in body["message"]


async def test_virtual_tryon_refuses_rather_than_faking_an_image(app_client, make_user):
    _, token = await make_user()
    status = (await app_client.get("/api/v2/tryon/status", headers=auth(token))).json()
    assert status["configured"] is False

    response = await app_client.post("/api/v2/tryon/jobs", json={
        "source_media_id": str(uuid.uuid4()), "item_ids": [],
    }, headers=auth(token))
    assert response.status_code == 404
    assert "made-up picture" in response.json()["detail"]["message"]


# --- Audit ---------------------------------------------------------------------------------------------------


async def test_every_grant_and_revocation_leaves_an_audit_trail(app_client, make_user, provider):
    """The record that answers a chargeback in eighteen months."""
    user, token = await make_user()
    account_id = await account_id_for(app_client, user, token)
    order = (await checkout(app_client, token)).json()

    await send_webhook(app_client, provider, webhook_body(
        "payment.succeeded", order_id=order["provider_order_id"], amount_inr=399,
        notes={"account_id": str(account_id)},
    ))
    await send_webhook(app_client, provider, webhook_body(
        "refund.processed", order_id=order["provider_order_id"], refund_id=f"rfnd_{uuid.uuid4().hex[:10]}",
        amount_inr=399, notes={"account_id": str(account_id)},
    ))

    from app.domains.billing.models import BillingAuditEvent

    factory = sql.get_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(
            select(BillingAuditEvent).where(BillingAuditEvent.account_id == account_id)
        )).scalars().all()
        actions = {row.action for row in rows}
        assert "checkout_created" in actions
        assert "entitlements_granted" in actions
        assert "entitlements_revoked" in actions
