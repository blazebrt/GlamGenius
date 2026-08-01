"""Config, feature flags, structured errors and billing consistency."""
from __future__ import annotations

from tests.conftest import auth


# --- Config -----------------------------------------------------------------


async def test_config_tells_the_app_billing_is_off(app_client):
    body = (await app_client.get("/api/v2/config")).json()

    assert body["billing"]["subscriptions_available"] is False
    assert body["billing"]["invite_only"] is True
    assert body["billing"]["beta_message"]


async def test_config_states_the_face_photo_storage_position(app_client):
    body = (await app_client.get("/api/v2/config")).json()

    assert body["media"]["face_photos_stored"] is False
    assert body["media"]["max_bytes"] > 0
    assert "image/jpeg" in body["media"]["allowed_types"]


async def test_v1_public_config_also_reports_billing_availability(app_client):
    """The signed-out welcome screen reads this one."""
    body = (await app_client.get("/api/config/public")).json()
    assert body["subscriptions_available"] is False


async def test_config_needs_no_authentication(app_client):
    assert (await app_client.get("/api/v2/config")).status_code == 200


# --- Billing ----------------------------------------------------------------


async def test_creating_an_order_is_refused_at_the_first_step(app_client, make_user):
    """It used to create an order and only refuse at confirm, which is what
    produced 'Payment failed' after inviting someone to pay."""
    _, token = await make_user()

    response = await app_client.post(
        "/api/subscription/create-order",
        json={"plan": "plus_monthly"},
        headers=auth(token),
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "SUBSCRIPTIONS_UNAVAILABLE"
    # The message must not sound like a failed payment.
    assert "failed" not in detail["message"].lower()
    assert "beta" in detail["message"].lower()


async def test_no_order_is_written_when_billing_is_unavailable(app_client, make_user):
    from database import db

    user, token = await make_user()
    await app_client.post(
        "/api/subscription/create-order",
        json={"plan": "plus_yearly"},
        headers=auth(token),
    )

    assert await db.subscription_orders.count_documents({"user_id": user["id"]}) == 0


async def test_confirming_a_subscription_is_refused(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/subscription/confirm?order_id=sub_anything", headers=auth(token)
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SUBSCRIPTIONS_UNAVAILABLE"


async def test_subscription_status_reports_availability_from_config(
    app_client, make_user
):
    _, token = await make_user()
    body = (
        await app_client.get("/api/subscription/status", headers=auth(token))
    ).json()

    assert body["subscriptions_available"] is False


# --- Feature flags ----------------------------------------------------------


async def test_config_lists_every_known_flag(app_client):
    features = (await app_client.get("/api/v2/config")).json()["features"]

    for key in ("v2_media", "v2_privacy", "v2_consent", "v2_ai_gateway"):
        assert key in features


async def test_a_disabled_flag_hides_its_routes(app_client, make_user, media_root):
    """A switched-off module should look absent, not forbidden."""
    from app.shared.database import sql
    from app.shared.flags import service as flags

    _, token = await make_user()

    factory = sql.get_sessionmaker()
    async with factory() as session:
        await flags.set_flag(session, "v2_media", False)
        await session.commit()

    try:
        response = await app_client.get(
            "/api/v2/media/00000000-0000-0000-0000-000000000000", headers=auth(token)
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"
    finally:
        async with factory() as session:
            await flags.set_flag(session, "v2_media", True)
            await session.commit()


async def test_database_flag_overrides_the_environment_default(app_client):
    from app.shared.database import sql
    from app.shared.flags import service as flags

    factory = sql.get_sessionmaker()
    async with factory() as session:
        assert await flags.is_enabled(session, "v2_media") is True
        await flags.set_flag(session, "v2_media", False)
        await session.commit()

    async with factory() as session:
        assert await flags.is_enabled(session, "v2_media") is False
        await flags.set_flag(session, "v2_media", True)
        await session.commit()


async def test_unknown_flag_defaults_to_off(app_client):
    from app.shared.database import sql
    from app.shared.flags import service as flags

    factory = sql.get_sessionmaker()
    async with factory() as session:
        assert await flags.is_enabled(session, "not_a_real_flag") is False


# --- Structured errors ------------------------------------------------------


async def test_errors_carry_a_code_and_a_request_id(app_client, make_user):
    _, token = await make_user()
    response = await app_client.post(
        "/api/subscription/create-order",
        json={"plan": "plus_monthly"},
        headers=auth(token),
    )

    detail = response.json()["detail"]
    assert set(("code", "message", "retryable", "request_id")) <= set(detail)
    assert response.headers["X-Request-Id"]
    assert detail["request_id"] == response.headers["X-Request-Id"]


async def test_an_inbound_request_id_is_echoed_back(app_client):
    response = await app_client.get(
        "/api/v2/config", headers={"X-Request-Id": "abc123trace"}
    )
    assert response.headers["X-Request-Id"] == "abc123trace"


async def test_a_hostile_request_id_is_replaced(app_client):
    response = await app_client.get(
        "/api/v2/config", headers={"X-Request-Id": "x" * 500}
    )
    assert response.headers["X-Request-Id"] != "x" * 500
    assert len(response.headers["X-Request-Id"]) <= 64
