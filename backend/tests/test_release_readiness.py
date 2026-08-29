"""RR-01 release-readiness regression coverage without real external services."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.domains.planning import providers

BACKEND_DIR = Path(__file__).parent.parent
PYTHON = sys.executable

PRODUCTION_ENV = {
    "APP_ENV": "production",
    "SUPABASE_URL": "https://valid.supabase.co",
    "SUPABASE_ANON_KEY": "valid-anon",
    "SUPABASE_SERVICE_ROLE_KEY": "valid-service",
    "SUPABASE_JWKS_URL": "https://valid.supabase.co/auth/v1/.well-known/jwks.json",
    "SUPABASE_JWT_ISSUER": "https://valid.supabase.co/auth/v1",
    "POSTGRES_URL": "postgresql://user:pass@db.valid.example:5432/glamgenius",
    "SUPABASE_STORAGE_BUCKET": "glamgenius-media",
    "GEMINI_API_KEY": "configured-gemini-key",
    "SENTRY_BACKEND_DSN": "https://key@example.ingest.sentry.io/1",
    "INVITE_REQUIRED": "true",
    "REQUIRE_ANALYSIS_CONSENT": "true",
    "CONSENT_VERSION": "2026-01-01",
    "MEDIA_STORAGE_BACKEND": "supabase",
    "ALLOWED_ORIGINS": "https://app.glamgenius.example",
    "PRIVACY_POLICY_URL": "https://legal.glamgenius.example/privacy",
    "SUPPORT_URL": "https://support.glamgenius.example",
    "GOOGLE_CALENDAR_ENABLED": "false",
    "LIVE_ENVIRONMENT_PROVIDER": "",
    "OPEN_METEO_MODE": "disabled",
}


def run_readiness(overrides: dict[str, str | None] | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    env = dict(PRODUCTION_ENV)
    for key, value in (overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(
        [PYTHON, "-m", "app.release_readiness", "--json"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, json.loads(result.stdout)


@pytest.mark.parametrize(
    ("live_provider", "mode", "expected_weather", "expected_air"),
    [
        ("", "disabled", False, False),
        ("open_meteo", "evaluation", True, True),
        ("open_meteo", "commercial", True, True),
        ("manual", "commercial", False, False),
    ],
)
def test_provider_catalogue_keeps_weather_and_air_quality_availability_consistent(
    monkeypatch, live_provider, mode, expected_weather, expected_air
):
    monkeypatch.setattr(providers, "LIVE_ENVIRONMENT_PROVIDER", live_provider)
    monkeypatch.setattr(providers, "OPEN_METEO_MODE", mode)
    catalogue = providers.catalogue()
    weather = {item["key"]: item["available"] for item in catalogue["weather"]}
    air_quality = {item["key"]: item["available"] for item in catalogue["air_quality"]}
    assert weather["manual"] is True
    assert air_quality["manual"] is True
    assert weather["open_meteo"] is expected_weather
    assert air_quality["open_meteo"] is expected_air


def test_development_readiness_is_informational_and_exits_zero():
    result = subprocess.run(
        [PYTHON, "-m", "app.release_readiness", "--json"],
        cwd=BACKEND_DIR,
        env={"APP_ENV": "development"},
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["status"] == "development"
    assert report["optional_integrations"]["google_calendar"]["status"] == "disabled_optional"


def test_production_core_missing_config_is_not_ready():
    result, report = run_readiness({"SUPABASE_URL": None})
    assert result.returncode == 1
    assert report["status"] == "not_ready"
    assert report["required"]["SUPABASE_URL"] == "missing"


@pytest.mark.parametrize("key", ["PRIVACY_POLICY_URL", "SUPPORT_URL"])
def test_placeholder_policy_urls_are_not_ready(key):
    result, report = run_readiness({key: "https://glamgenius.placeholder/value"})
    assert result.returncode == 1
    assert report["required"][key] == "placeholder"


def test_default_cors_is_not_ready_in_production():
    result, report = run_readiness({"ALLOWED_ORIGINS": None})
    assert result.returncode == 1
    assert report["required"]["ALLOWED_ORIGINS"] == "development_default"


def test_google_calendar_disabled_is_optional():
    result, report = run_readiness()
    assert result.returncode == 0
    assert report["optional_integrations"]["google_calendar"] == {"status": "disabled_optional"}


def test_google_calendar_enabled_requires_complete_vault_configuration():
    result, report = run_readiness({"GOOGLE_CALENDAR_ENABLED": "true"})
    assert result.returncode == 1
    calendar = report["optional_integrations"]["google_calendar"]
    assert calendar["status"] == "not_ready"
    assert calendar["GOOGLE_CALENDAR_CLIENT_SECRET"] == "missing"


def test_google_calendar_complete_configuration_is_ready():
    result, report = run_readiness(
        {
            "GOOGLE_CALENDAR_ENABLED": "true",
            "GOOGLE_CALENDAR_CLIENT_ID": "client-id",
            "GOOGLE_CALENDAR_CLIENT_SECRET": "client-secret",
            "GOOGLE_CALENDAR_REDIRECT_URI": "https://api.glamgenius.example/calendar/callback",
            "GOOGLE_CALENDAR_APP_RETURN_URI": "glamgenius://calendar-result",
            "GOOGLE_CALENDAR_CREDENTIAL_STORE": "supabase_vault",
        }
    )
    assert result.returncode == 0
    assert report["optional_integrations"]["google_calendar"]["status"] == "ready"


def test_open_meteo_modes_follow_production_policy():
    disabled_result, disabled = run_readiness()
    evaluation_result, evaluation = run_readiness(
        {"LIVE_ENVIRONMENT_PROVIDER": "open_meteo", "OPEN_METEO_MODE": "evaluation"}
    )
    missing_result, missing = run_readiness(
        {"LIVE_ENVIRONMENT_PROVIDER": "open_meteo", "OPEN_METEO_MODE": "commercial"}
    )
    complete_result, complete = run_readiness(
        {
            "LIVE_ENVIRONMENT_PROVIDER": "open_meteo",
            "OPEN_METEO_MODE": "commercial",
            "OPEN_METEO_API_KEY": "commercial-key",
        }
    )
    assert disabled_result.returncode == 0
    assert disabled["optional_integrations"]["open_meteo"]["status"] == "disabled_optional"
    assert evaluation_result.returncode == 1
    assert evaluation["optional_integrations"]["open_meteo"]["status"] == "invalid"
    assert missing_result.returncode == 1
    assert missing["optional_integrations"]["open_meteo"]["OPEN_METEO_API_KEY"] == "missing"
    assert complete_result.returncode == 0
    assert complete["optional_integrations"]["open_meteo"]["status"] == "ready"


def test_readiness_cli_never_emits_secret_values():
    secret = "secret-value-must-not-appear"
    result, report = run_readiness(
        {
            "SUPABASE_ANON_KEY": secret,
            "SUPABASE_SERVICE_ROLE_KEY": secret,
            "GEMINI_API_KEY": secret,
            "SENTRY_BACKEND_DSN": f"https://{secret}@example.ingest.sentry.io/1",
        }
    )
    assert result.returncode == 0
    assert secret not in result.stdout
    assert report["required"]["GEMINI_API_KEY"] == "configured"
