"""§1.15 — monitoring and operational regression coverage.

Crash reporting is the one place where the whole product's data can leave the
building at once, so the scrubber is treated as a security control and tested
like one: by key name, by value shape, and at depth.

Also covered: the health surface an operator pages on, the production storage
guard, feature-flag resolution, and the absence of the vocabulary this product
does not use.

What this protects against
--------------------------
* An access or refresh token reaching a third-party error tracker.
* An email address, a face photo or a memory fact travelling in a stack frame,
  a breadcrumb or a tag.
* Storage object keys — which name an account — leaking through error context.
* Health answering "healthy" when the database is down.
* A deployment starting with local filesystem storage in production.
* Payment or judgemental vocabulary reappearing anywhere in the codebase.
"""
from __future__ import annotations

import pytest
from app.shared.flags import service as flags
from app.shared.observability.sentry_privacy import REDACTED, scrub_event

pytestmark = pytest.mark.asyncio


ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwicm9sZSI6ImF1dGhlbnRpY2F0ZWQifQ"
    ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)


# ---------------------------------------------------------------------------
# Sentry scrubbing
# ---------------------------------------------------------------------------

def test_access_and_refresh_tokens_are_redacted():
    event = {
        "extra": {
            "access_token": ACCESS_TOKEN,
            "refresh_token": "v1.MRjRWEwEsMbLcRw-refresh-value",
        },
        "message": f"auth failed for {ACCESS_TOKEN}",
    }

    scrubbed = scrub_event(event)

    assert scrubbed["extra"]["access_token"] == REDACTED
    assert scrubbed["extra"]["refresh_token"] == REDACTED
    # Even unnamed, a JWT is recognised by shape inside free text.
    assert ACCESS_TOKEN not in scrubbed["message"]
    assert REDACTED in scrubbed["message"]


def test_email_and_phone_are_redacted_wherever_they_appear():
    event = {
        "user": {"email": "priya@example.com"},
        "breadcrumbs": [
            {"message": "sign-in attempt for priya@example.com from +91 98765 43210"}
        ],
    }

    scrubbed = scrub_event(event)

    assert scrubbed["user"]["email"] == REDACTED
    crumb = scrubbed["breadcrumbs"][0]["message"]
    assert "priya@example.com" not in crumb
    assert "98765" not in crumb


def test_storage_object_keys_and_image_bytes_are_redacted():
    """A storage key contains the account id, so it identifies a person."""
    event = {
        "contexts": {
            "media": {
                "storage_key": "accounts/8f14e45f/assets/6b1c.png",
                "image_base64": "A" * 200,
                "photo": b"\x89PNG\r\n\x1a\n",
            }
        }
    }

    scrubbed = scrub_event(event)

    media = scrubbed["contexts"]["media"]
    assert media["image_base64"] == REDACTED
    assert media["photo"] == REDACTED
    # A storage key embeds the account id, so it identifies a person.
    assert media["storage_key"] == REDACTED
    assert "A" * 200 not in str(scrubbed)


def test_provider_secrets_are_redacted():
    event = {
        "extra": {
            "SUPABASE_SERVICE_ROLE_KEY": "sbp_live_service_role_value",
            "gemini_api_key": "AIza-not-a-real-key",
            "authorization": f"Bearer {ACCESS_TOKEN}",
        }
    }

    scrubbed = scrub_event(event)

    assert scrubbed["extra"]["gemini_api_key"] == REDACTED
    assert scrubbed["extra"]["authorization"] == REDACTED
    assert scrubbed["extra"]["SUPABASE_SERVICE_ROLE_KEY"] == REDACTED
    assert "sbp_live_service_role_value" not in str(scrubbed)


def test_memory_facts_and_ingredients_are_redacted():
    """The two most personal free-text fields the product holds."""
    event = {
        "extra": {
            "memory_fact": "Prefers not to wear sleeveless tops.",
            "ingredients": ["retinol", "salicylic acid"],
        }
    }

    scrubbed = scrub_event(event)

    assert scrubbed["extra"]["memory_fact"] == REDACTED
    assert scrubbed["extra"]["ingredients"] == REDACTED


def test_scrubbing_reaches_nested_structures():
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"vars": {"email": "deep@example.com", "safe": "keep me"}}
                        ]
                    }
                }
            ]
        }
    }

    scrubbed = scrub_event(event)

    frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["vars"]["email"] == REDACTED
    assert frame["vars"]["safe"] == "keep me", "the scrubber must not eat everything"


def test_scrubber_leaves_operational_fields_alone():
    """Over-redacting makes the tracker useless; request ids and status codes
    must survive."""
    event = {
        "tags": {"request_id": "9f2c1b7a", "route": "/api/v2/inventory/items"},
        "extra": {"status_code": 500, "latency_ms": 812},
    }

    scrubbed = scrub_event(event)

    assert scrubbed["tags"]["request_id"] == "9f2c1b7a"
    assert scrubbed["tags"]["route"] == "/api/v2/inventory/items"
    assert scrubbed["extra"]["status_code"] == 500


def test_sentry_is_not_initialised_without_a_dsn(monkeypatch):
    """A missing DSN must not fail startup and must make no network call."""
    from app.shared.observability import sentry_bootstrap

    monkeypatch.delenv("SENTRY_BACKEND_DSN", raising=False)
    sentry_bootstrap.init_sentry()  # must simply return


def test_sentry_is_not_initialised_under_pytest(monkeypatch):
    """A test failure must never emit an event to the real project."""
    import sys

    from app.shared.observability import sentry_bootstrap

    monkeypatch.setenv("SENTRY_BACKEND_DSN", "https://key@example.ingest.sentry.io/1")
    called = []
    fake = type("_M", (), {"init": staticmethod(lambda **kw: called.append(kw))})
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)

    sentry_bootstrap.init_sentry()

    assert called == [], "Sentry must not initialise while pytest is running"


# ---------------------------------------------------------------------------
# Health and readiness
# ---------------------------------------------------------------------------

async def test_ready_reports_ready_when_postgres_is_up(app_client, db_clean):
    resp = await app_client.get("/api/v2/ready")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["components"]["postgres"] == "up"
    assert "ai_provider" in body["components"]
    # A readiness payload must never carry credentials.
    assert "key" not in resp.text.lower() or "api_key" not in resp.text.lower()


async def test_ready_reports_not_ready_when_postgres_is_down(app_client, monkeypatch):
    """An operator pages on this. Reporting ready while the database is
    unreachable is worse than reporting nothing."""
    from app.shared.database import sql

    async def _down():
        return False

    monkeypatch.setattr(sql, "ping", _down)

    resp = await app_client.get("/api/v2/ready")

    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"
    assert resp.json()["components"]["postgres"] == "down"


async def test_ready_needs_no_authentication(app_client, db_clean):
    """A readiness probe has no token."""
    resp = await app_client.get("/api/v2/ready")
    assert resp.status_code == 200


async def test_config_publishes_only_client_safe_settings(app_client, db_clean):
    resp = await app_client.get("/api/v2/config")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The anon key is meant to be public; the service-role key is not.
    assert "anon_key" in body["supabase"]
    assert "service_role" not in resp.text
    assert "SUPABASE_SERVICE_ROLE_KEY" not in resp.text
    assert "jwt_secret" not in resp.text.lower()
    # Face photos are never stored, and the config says so to the client.
    assert body["media"]["face_photos_stored"] is False
    # No billing block anywhere in the client contract. ("plan" is excluded
    # deliberately — the weekly *planner* is a product feature.)
    for banned in ("price", "subscription", "billing", "razorpay", "paywall",
                   "entitlement", "upgrade", "checkout"):
        assert banned not in resp.text.lower()


# ---------------------------------------------------------------------------
# Production configuration guards
# ---------------------------------------------------------------------------

def test_production_refuses_local_media_storage(monkeypatch):
    from app.domains.media.storage import factory
    from app.domains.media.storage.base import StorageMisconfigured

    factory.set_storage(None)
    monkeypatch.setattr(factory, "APP_ENV", "production")
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setattr(factory, "MEDIA_ALLOW_LOCAL_IN_PRODUCTION", False)

    with pytest.raises(StorageMisconfigured) as exc:
        factory.get_storage()

    assert "production" in str(exc.value).lower()
    factory.set_storage(None)


def test_unknown_storage_backend_fails_loudly(monkeypatch):
    from app.domains.media.storage import factory
    from app.domains.media.storage.base import StorageMisconfigured

    factory.set_storage(None)
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "minio")

    with pytest.raises(StorageMisconfigured):
        factory.get_storage()

    factory.set_storage(None)


# ---------------------------------------------------------------------------
# Feature-flag resolution
# ---------------------------------------------------------------------------

def test_flag_resolution_is_stable_without_an_env_override(monkeypatch):
    monkeypatch.delenv("V2_FEATURES", raising=False)

    for key in flags.KNOWN_FLAGS:
        assert flags.resolved_default(key) == flags.STABLE_BETA_DEFAULTS[key], key


def test_env_override_wins_over_stable_defaults(monkeypatch):
    monkeypatch.setenv("V2_FEATURES", "v2_scan")

    assert flags.env_override_set() is True
    assert flags.env_enabled("v2_scan") is True
    assert flags.env_enabled("v2_inventory") is False


def test_empty_env_override_disables_everything(monkeypatch):
    """An operator explicitly setting an empty list means "off", not
    "fall back to the defaults"."""
    monkeypatch.setenv("V2_FEATURES", "")

    assert flags.env_override_set() is True
    for key in flags.KNOWN_FLAGS:
        assert flags.env_enabled(key) is False


def test_disabled_essential_flags_are_reported(monkeypatch):
    monkeypatch.setenv("V2_FEATURES", "v2_scan")

    warnings = flags.warn_if_essentials_disabled()

    assert warnings, "disabling an essential flag must not be silent"
    assert all(isinstance(row, str) for row in warnings)


async def test_database_flags_are_readable_through_config(app_client, db_clean):
    from app.bootstrap import run as run_seed
    from app.shared.database.sql import get_sessionmaker

    factory = get_sessionmaker()
    async with factory() as session:
        await run_seed(session)
        await session.commit()

    resp = await app_client.get("/api/v2/config")

    features = resp.json()["features"]
    assert features, "the client must be told which features are on"
    # Nothing unfinished may be switched on by the seeded defaults.
    assert set(features) == set(flags.KNOWN_FLAGS)
    for unfinished in ("v2_virtual_tryon", "v2_packing"):
        assert features[unfinished] is False, f"{unfinished} must stay off"
