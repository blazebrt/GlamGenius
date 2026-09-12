"""Step 8 public beta surface and Render-origin fallback contract."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _production_environment() -> dict[str, str]:
    """A complete synthetic production environment with no real secrets."""
    return {
        "APP_ENV": "production",
        "SUPABASE_URL": "https://valid.supabase.co",
        "SUPABASE_ANON_KEY": "valid-anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "valid-service-key",
        "SUPABASE_JWT_ISSUER": "https://valid.supabase.co/auth/v1",
        "SUPABASE_JWKS_URL": "https://valid.supabase.co/auth/v1/.well-known/jwks.json",
        "POSTGRES_URL": "postgresql://app:password@db.valid.supabase.co:5432/postgres",
        "OFF_DATABASE_URL": "postgresql://off:password@off.valid.supabase.co:5432/postgres",
        "SUPABASE_STORAGE_BUCKET": "glamgenius-media",
        "GEMINI_API_KEY": "synthetic-gemini-key",
        "SENTRY_BACKEND_DSN": "https://public@sentry.invalid/1",
        "INTERNAL_SCHEDULER_TOKEN": "not-a-real-token-not-a-real-token-not-a-real-token",
        "INVITE_REQUIRED": "true",
        "REQUIRE_ANALYSIS_CONSENT": "true",
        "CONSENT_VERSION": "2026-01-01",
        "MEDIA_STORAGE_BACKEND": "supabase",
    }


def _config_snapshot(extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = _production_environment()
    if extra:
        env.update(extra)
    script = """
import json
from app import config
from app.release_readiness import evaluate
config.validate_production_configuration()
print(json.dumps({
    'base': config.PUBLIC_BASE_URL,
    'base_source': config.PUBLIC_BASE_URL_SOURCE,
    'privacy': config.PRIVACY_POLICY_URL,
    'support': config.SUPPORT_URL,
    'origins': config.ALLOWED_ORIGINS,
    'origins_default': config.ALLOWED_ORIGINS_IS_DEFAULT,
    'origins_derived': config.ALLOWED_ORIGINS_IS_DERIVED,
    'readiness': evaluate().required,
}))
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_render_url_derives_one_complete_production_public_surface():
    result = _config_snapshot({
        "RENDER": "true",
        "RENDER_EXTERNAL_URL": "https://glamgenius-api.onrender.com/",
    })
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["base"] == "https://glamgenius-api.onrender.com"
    assert snapshot["base_source"] == "render"
    assert snapshot["privacy"] == "https://glamgenius-api.onrender.com/privacy"
    assert snapshot["support"] == "https://glamgenius-api.onrender.com/support"
    assert snapshot["origins"] == ["https://glamgenius-api.onrender.com"]
    assert snapshot["origins_default"] is False
    assert snapshot["origins_derived"] is True
    assert snapshot["readiness"]["ALLOWED_ORIGINS"] == "configured"
    assert snapshot["readiness"]["PRIVACY_POLICY_URL"] == "configured"
    assert snapshot["readiness"]["SUPPORT_URL"] == "configured"


def test_explicit_operator_values_override_render_defaults():
    result = _config_snapshot({
        "RENDER": "true",
        "RENDER_EXTERNAL_URL": "https://glamgenius-api.onrender.com",
        "PUBLIC_BASE_URL": "https://explicit-override-under-test.onrender.com/",
        "PRIVACY_POLICY_URL": "https://legal-override-under-test.onrender.com/privacy",
        "SUPPORT_URL": "https://help-override-under-test.onrender.com/support",
        "ALLOWED_ORIGINS": "https://app-override-under-test.onrender.com",
    })
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["base"] == "https://explicit-override-under-test.onrender.com"
    assert snapshot["base_source"] == "explicit"
    assert snapshot["privacy"] == "https://legal-override-under-test.onrender.com/privacy"
    assert snapshot["support"] == "https://help-override-under-test.onrender.com/support"
    assert snapshot["origins"] == ["https://app-override-under-test.onrender.com"]
    assert snapshot["origins_derived"] is False


def test_render_url_is_ignored_without_renders_own_platform_marker():
    result = _config_snapshot({
        "RENDER_EXTERNAL_URL": "https://glamgenius-api.onrender.com",
    })
    assert result.returncode != 0
    assert "PRIVACY_POLICY_URL" in result.stderr


@pytest.mark.parametrize("bad_url", [
    "http://glamgenius-api.onrender.com",
    "https://localhost",
    "https://127.0.0.1",
    "https://*.onrender.com",
    "https://glamgenius-api.onrender.com/path",
    "https://user:password@glamgenius-api.onrender.com",
    "https://glamgenius-api.onrender.com?query=1",
    "https://glamgenius-api.onrender.com#fragment",
])
def test_malformed_render_urls_fail_closed_in_production(bad_url: str):
    result = _config_snapshot({"RENDER": "true", "RENDER_EXTERNAL_URL": bad_url})
    assert result.returncode != 0
    assert "invalid public production configuration" in result.stderr


def test_host_and_forwarded_host_cannot_influence_configuration():
    result = _config_snapshot({
        "RENDER": "true",
        "RENDER_EXTERNAL_URL": "https://glamgenius-api.onrender.com",
        "HOST": "attacker.invalid",
        "HTTP_HOST": "attacker.invalid",
        "HTTP_X_FORWARDED_HOST": "attacker.invalid",
        "HTTP_ORIGIN": "https://attacker.invalid",
    })
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["base"] == "https://glamgenius-api.onrender.com"
    assert snapshot["origins"] == ["https://glamgenius-api.onrender.com"]
    assert "attacker.invalid" not in result.stdout


pytestmark = pytest.mark.asyncio


async def test_public_pages_are_unauthenticated_static_information(app_client: AsyncClient):
    for path in ("/privacy", "/support"):
        response = await app_client.get(path)
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'; img-src 'none'; "
            "connect-src 'none'; script-src 'none'"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        body = response.text.lower()
        for forbidden in ("<form", "<script", "mailto:", "support@", "<input"):
            assert forbidden not in body


async def test_privacy_and_support_point_to_governed_customer_controls(app_client: AsyncClient):
    privacy = (await app_client.get("/privacy")).text.lower()
    support = (await app_client.get("/support")).text.lower()
    assert "profile and memory controls" in privacy
    assert "account-deletion workflow" in privacy
    assert "profile and memory controls" in support
    assert "account deletion from profile" in support
    assert 'href="/privacy"' in support
    assert 'href="/support"' in privacy


async def test_public_pages_do_not_change_customer_api_authentication(app_client: AsyncClient):
    response = await app_client.get("/api/v2/me")
    assert response.status_code == 401
