"""RR-01 release readiness and provider catalogue.

Two things are proved here. First, the provider catalogue tells the truth about
which environment sources are actually available. Second, the readiness report
explains what production still needs without ever printing a secret, and never
disagrees with the validation that actually gates startup.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from app import config as app_config
from app import release_readiness
from app.domains.planning import providers as providers_module
from app.release_readiness import Status, evaluate, main, render

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Values that must never appear in output. Deliberately distinctive.
SECRET_ANON = "anon-secret-value-must-not-appear"
SECRET_SERVICE_ROLE = "service-role-secret-must-not-appear"
SECRET_GEMINI = "gemini-secret-must-not-appear"
SECRET_GOOGLE = "google-client-secret-must-not-appear"
SECRET_OPEN_METEO = "open-meteo-key-must-not-appear"
SECRET_DSN_KEY = "dsnpublickeymustnotappear"
SECRET_PG_PASSWORD = "pg-password-must-not-appear"


def _live_environment(monkeypatch, *, provider: str, mode: str) -> None:
    """The catalogue reads names bound at import, so patch them there."""
    monkeypatch.setattr(providers_module, "LIVE_ENVIRONMENT_PROVIDER", provider)
    monkeypatch.setattr(providers_module, "OPEN_METEO_MODE", mode)


def _available(catalogue: dict, family: str) -> dict[str, bool]:
    return {row["key"]: row["available"] for row in catalogue[family]}


# --- Provider catalogue -------------------------------------------------------


def test_disabled_live_environment_advertises_only_manual_sources(monkeypatch):
    _live_environment(monkeypatch, provider="", mode="disabled")
    catalogue = providers_module.catalogue()
    assert _available(catalogue, "weather") == {"manual": True, "open_meteo": False}
    assert _available(catalogue, "air_quality") == {"manual": True, "open_meteo": False}


def test_evaluation_mode_advertises_both_weather_and_air_quality(monkeypatch):
    _live_environment(monkeypatch, provider="open_meteo", mode="evaluation")
    catalogue = providers_module.catalogue()
    assert _available(catalogue, "weather")["open_meteo"] is True
    assert _available(catalogue, "air_quality")["open_meteo"] is True


def test_commercial_mode_advertises_both_weather_and_air_quality(monkeypatch):
    """The catalogue reports the configured boundary; the key is validation's job."""
    _live_environment(monkeypatch, provider="open_meteo", mode="commercial")
    catalogue = providers_module.catalogue()
    assert _available(catalogue, "weather")["open_meteo"] is True
    assert _available(catalogue, "air_quality")["open_meteo"] is True


def test_a_different_live_provider_never_advertises_open_meteo(monkeypatch):
    _live_environment(monkeypatch, provider="some_other_provider", mode="commercial")
    catalogue = providers_module.catalogue()
    assert _available(catalogue, "weather")["open_meteo"] is False
    assert _available(catalogue, "air_quality")["open_meteo"] is False


@pytest.mark.parametrize(
    ("provider", "mode"),
    [("", "disabled"), ("open_meteo", "evaluation"), ("open_meteo", "commercial"), ("", "commercial")],
)
def test_weather_and_air_quality_always_agree(monkeypatch, provider, mode):
    """Both families come from one provider on one boundary, so they cannot differ."""
    _live_environment(monkeypatch, provider=provider, mode=mode)
    catalogue = providers_module.catalogue()
    assert _available(catalogue, "weather")["open_meteo"] == _available(catalogue, "air_quality")["open_meteo"]


# --- Readiness: development ---------------------------------------------------


def _development(monkeypatch) -> None:
    monkeypatch.setattr(app_config, "APP_ENV", "development")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "disabled")
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", False)


def test_development_is_ready_despite_optional_integrations_being_off(monkeypatch):
    _development(monkeypatch)
    report = evaluate()
    assert report.status == "ready"
    assert report.blocking == ()
    assert report.optional_integrations["google_calendar"] == Status.DISABLED_OPTIONAL.value
    assert report.optional_integrations["live_environment"] == Status.DISABLED_OPTIONAL.value
    assert any("do not fail this check" in note for note in report.notes)


def test_development_still_lists_what_production_will_need(monkeypatch):
    """Being ready for development must not hide the production gaps."""
    _development(monkeypatch)
    monkeypatch.setattr(app_config, "SUPABASE_URL", "")
    report = evaluate()
    assert report.required["SUPABASE_URL"] == Status.MISSING.value
    assert report.status == "ready", "a development process is not blocked by them"


# --- Readiness: production ----------------------------------------------------


def _complete_production(monkeypatch) -> None:
    """A fully configured production environment, with fake but well-formed values."""
    monkeypatch.setattr(app_config, "APP_ENV", "production")
    monkeypatch.setattr(app_config, "SUPABASE_URL", "https://realproject.supabase.co")
    monkeypatch.setattr(app_config, "SUPABASE_ANON_KEY", SECRET_ANON)
    monkeypatch.setattr(app_config, "SUPABASE_SERVICE_ROLE_KEY", SECRET_SERVICE_ROLE)
    monkeypatch.setattr(app_config, "SUPABASE_JWT_ISSUER", "https://realproject.supabase.co/auth/v1")
    monkeypatch.setattr(
        app_config, "SUPABASE_JWKS_URL",
        "https://realproject.supabase.co/auth/v1/.well-known/jwks.json",
    )
    monkeypatch.setattr(app_config, "SUPABASE_STORAGE_BUCKET", "glamgenius-media")
    monkeypatch.setattr(
        app_config, "POSTGRES_URL",
        f"postgresql+asyncpg://appuser:{SECRET_PG_PASSWORD}@db.realproject.supabase.co:5432/glamgenius",
    )
    monkeypatch.setattr(app_config, "GEMINI_API_KEY", SECRET_GEMINI)
    monkeypatch.setenv("SENTRY_BACKEND_DSN", f"https://{SECRET_DSN_KEY}@o1.ingest.sentry.io/2")
    monkeypatch.setattr(app_config, "MEDIA_STORAGE_BACKEND", "supabase")
    monkeypatch.setattr(app_config, "ALLOWED_ORIGINS", ["https://app.glamgenius.in"])
    monkeypatch.setattr(app_config, "ALLOWED_ORIGINS_IS_DEFAULT", False)
    monkeypatch.setattr(app_config, "PRIVACY_POLICY_URL", "https://glamgenius.in/privacy")
    monkeypatch.setattr(app_config, "SUPPORT_URL", "https://glamgenius.in/support")
    monkeypatch.setattr(app_config, "INVITE_REQUIRED", True)
    monkeypatch.setattr(app_config, "REQUIRE_ANALYSIS_CONSENT", True)
    monkeypatch.setattr(app_config, "CONSENT_VERSION", "2026-01-01")
    monkeypatch.setattr(app_config, "SUPABASE_ADMIN_USER_IDS", set())
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", False)
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "disabled")
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "")


def test_complete_production_configuration_is_ready(monkeypatch):
    _complete_production(monkeypatch)
    report = evaluate()
    assert report.status == "ready", report.blocking
    assert report.blocking == ()
    assert all(
        status == Status.CONFIGURED.value for status in report.required.values()
    ), report.required


def test_incomplete_production_configuration_is_not_ready(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "SUPABASE_URL", "")
    monkeypatch.setattr(app_config, "GEMINI_API_KEY", "")
    report = evaluate()
    assert report.status == "not_ready"
    assert report.required["SUPABASE_URL"] == Status.MISSING.value
    assert report.required["GEMINI_API_KEY"] == Status.MISSING.value
    assert report.blocking, "the authoritative validation must supply a reason"


def test_placeholder_privacy_and_support_urls_block_production(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "PRIVACY_POLICY_URL", "https://glamgenius.placeholder/privacy")
    monkeypatch.setattr(app_config, "SUPPORT_URL", "https://glamgenius.placeholder/support")
    report = evaluate()
    assert report.required["PRIVACY_POLICY_URL"] == Status.PLACEHOLDER.value
    assert report.required["SUPPORT_URL"] == Status.PLACEHOLDER.value
    assert report.status == "not_ready"


@pytest.mark.parametrize(
    ("attribute", "placeholder"),
    [
        ("SUPABASE_ANON_KEY", "your_anon_key_here"),
        ("SUPABASE_SERVICE_ROLE_KEY", "your_service_role_key_here"),
        ("GEMINI_API_KEY", "your_gemini_api_key_here"),
    ],
)
def test_an_unedited_env_example_value_blocks_production(monkeypatch, attribute, placeholder):
    """A single unedited key must not pass as ready.

    These are the literal values shipped in ``env.example``. Startup validation
    screens the Supabase keys for "placeholder" and "fake" only, so it accepts
    all three — which is exactly why the readiness verdict has to consider the
    reported key states rather than deferring to validation alone. Reporting a
    key as PLACEHOLDER while still exiting 0 would be a green light on the one
    question this command exists to answer.
    """
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, attribute, placeholder)

    report = evaluate()

    assert report.required[attribute] == Status.PLACEHOLDER.value
    assert report.status == "not_ready"
    assert any(attribute in reason for reason in report.blocking), report.blocking


def test_a_blocking_reason_names_the_key_but_never_the_value(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "SUPABASE_SERVICE_ROLE_KEY", "your_service_role_key_here")

    report = evaluate()
    rendered = render(report)

    assert "SUPABASE_SERVICE_ROLE_KEY" in rendered
    assert "your_service_role_key_here" not in rendered
    assert "your_service_role_key_here" not in json.dumps(report.as_dict())


def test_an_enabled_but_incomplete_optional_feature_blocks_production(monkeypatch):
    """Turning a feature on and half-configuring it is not ready."""
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_SECRET", "")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_REDIRECT_URI", "https://api.glamgenius.in/cb")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_APP_RETURN_URI", "glamgenius://calendar-result")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CREDENTIAL_STORE", "supabase_vault")

    report = evaluate()

    assert report.status == "not_ready"
    assert report.blocking


def test_development_default_origins_block_production(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "ALLOWED_ORIGINS_IS_DEFAULT", True)
    report = evaluate()
    assert report.required["ALLOWED_ORIGINS"] == Status.DEVELOPMENT_DEFAULT.value
    assert report.status == "not_ready"


def test_localhost_origins_block_production(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "ALLOWED_ORIGINS", ["http://localhost:8081"])
    report = evaluate()
    assert report.required["ALLOWED_ORIGINS"] == Status.DEVELOPMENT_DEFAULT.value
    assert report.status == "not_ready"


def test_local_database_is_reported_as_a_development_default(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(
        app_config, "POSTGRES_URL", "postgresql+asyncpg://u:p@localhost:5432/glamgenius",
    )
    report = evaluate()
    assert report.required["POSTGRES_URL"] == Status.DEVELOPMENT_DEFAULT.value
    assert report.status == "not_ready"


# --- Readiness: Google Calendar ----------------------------------------------


def test_google_calendar_disabled_is_optional_and_does_not_block(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", False)
    report = evaluate()
    assert report.optional_integrations["google_calendar"] == Status.DISABLED_OPTIONAL.value
    assert "GOOGLE_CALENDAR_CLIENT_ID" not in report.optional_integrations
    assert report.status == "ready", report.blocking


def test_google_calendar_enabled_but_incomplete_is_not_ready(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_ID", "")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_SECRET", "")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_REDIRECT_URI", "")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CREDENTIAL_STORE", "disabled")
    report = evaluate()
    assert report.optional_integrations["google_calendar"] == "incomplete"
    assert report.optional_integrations["GOOGLE_CALENDAR_CLIENT_ID"] == Status.MISSING.value
    assert report.optional_integrations["GOOGLE_CALENDAR_CREDENTIAL_STORE"] == Status.INVALID.value
    assert report.status == "not_ready"


def test_google_calendar_fully_configured_is_ready(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_ID", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_SECRET", SECRET_GOOGLE)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_REDIRECT_URI", "https://api.glamgenius.in/api/v2/integrations/calendar/google/callback")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_APP_RETURN_URI", "glamgenius://calendar-result")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CREDENTIAL_STORE", "supabase_vault")
    report = evaluate()
    assert report.optional_integrations["google_calendar"] == "ready"
    assert report.status == "ready", report.blocking


def test_a_return_uri_with_a_query_is_invalid(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_SECRET", SECRET_GOOGLE)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_REDIRECT_URI", "https://api.glamgenius.in/cb")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_APP_RETURN_URI", "glamgenius://calendar-result?x=1")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CREDENTIAL_STORE", "supabase_vault")
    report = evaluate()
    assert report.optional_integrations["GOOGLE_CALENDAR_APP_RETURN_URI"] == Status.INVALID.value
    assert report.status == "not_ready"


# --- Readiness: live environment ---------------------------------------------


def test_live_environment_disabled_is_optional(monkeypatch):
    _complete_production(monkeypatch)
    report = evaluate()
    assert report.optional_integrations["live_environment"] == Status.DISABLED_OPTIONAL.value
    assert report.status == "ready", report.blocking


def test_evaluation_mode_is_invalid_for_production(monkeypatch):
    """Repository policy forbids evaluation licensing in staging or production."""
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "evaluation")
    report = evaluate()
    assert report.optional_integrations["live_environment"] == "invalid"
    assert report.status == "not_ready"


def test_evaluation_mode_is_acceptable_in_development(monkeypatch):
    _development(monkeypatch)
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "evaluation")
    report = evaluate()
    assert report.optional_integrations["live_environment"] == "evaluation"
    assert report.status == "ready"


def test_commercial_mode_without_a_key_is_not_ready(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(app_config, "OPEN_METEO_API_KEY", "")
    report = evaluate()
    assert report.optional_integrations["live_environment"] == "incomplete"
    assert report.optional_integrations["OPEN_METEO_API_KEY"] == Status.MISSING.value
    assert report.status == "not_ready"


def test_commercial_mode_with_a_key_is_ready(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(app_config, "OPEN_METEO_API_KEY", SECRET_OPEN_METEO)
    report = evaluate()
    assert report.optional_integrations["live_environment"] == "commercial"
    assert report.status == "ready", report.blocking


# --- Readiness never leaks a secret ------------------------------------------


def test_no_secret_value_ever_reaches_the_report_or_its_rendering(monkeypatch):
    """The whole point of a key-and-status report is that it is safe to paste."""
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_ENABLED", True)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_ID", "client-id")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CLIENT_SECRET", SECRET_GOOGLE)
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_REDIRECT_URI", "https://api.glamgenius.in/cb")
    monkeypatch.setattr(app_config, "GOOGLE_CALENDAR_CREDENTIAL_STORE", "supabase_vault")
    monkeypatch.setattr(app_config, "LIVE_ENVIRONMENT_PROVIDER", "open_meteo")
    monkeypatch.setattr(app_config, "OPEN_METEO_MODE", "commercial")
    monkeypatch.setattr(app_config, "OPEN_METEO_API_KEY", SECRET_OPEN_METEO)

    report = evaluate()
    haystack = "\n".join([
        render(report),
        repr(report.as_dict()),
        "\n".join(report.blocking),
    ])
    for secret in (
        SECRET_ANON, SECRET_SERVICE_ROLE, SECRET_GEMINI, SECRET_GOOGLE,
        SECRET_OPEN_METEO, SECRET_DSN_KEY, SECRET_PG_PASSWORD,
    ):
        assert secret not in haystack, "a secret value reached the readiness output"
    # The key names themselves must be present, or the report says nothing useful.
    assert "GEMINI_API_KEY" in haystack and "SUPABASE_SERVICE_ROLE_KEY" in haystack


def test_a_missing_secret_is_reported_by_name_only(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GEMINI_API_KEY", "")
    rendered = render(evaluate())
    assert "GEMINI_API_KEY" in rendered
    assert Status.MISSING.value in rendered


# --- Readiness shares semantics with production validation -------------------


def test_readiness_never_contradicts_production_validation(monkeypatch):
    """The command explains the gate; it must not become a way around it."""
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "INVITE_REQUIRED", False)

    with pytest.raises(RuntimeError):
        app_config.validate_production_configuration()
    report = evaluate()
    assert report.status == "not_ready"
    assert report.required["INVITE_REQUIRED"] == Status.INVALID.value


def test_consent_requirements_follow_the_same_rule(monkeypatch):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "REQUIRE_ANALYSIS_CONSENT", False)
    report = evaluate()
    assert report.required["REQUIRE_ANALYSIS_CONSENT"] == Status.INVALID.value
    assert report.status == "not_ready"


# --- The notification worker is never claimed to be scheduled ----------------


def test_the_scheduler_is_never_reported_as_configured(monkeypatch):
    """Nothing in the repository can see the host's crontab."""
    _complete_production(monkeypatch)
    report = evaluate()
    assert report.optional_integrations["notification_worker"] == Status.REQUIRES_HOST_SCHEDULER.value
    assert any("once per hour" in note for note in report.notes)
    rendered = render(report)
    assert "requires_host_scheduler" in rendered


# --- CLI ----------------------------------------------------------------------


def test_cli_exits_zero_when_ready(monkeypatch, capsys):
    _complete_production(monkeypatch)
    assert main([]) == 0
    assert "status      : ready" in capsys.readouterr().out


def test_cli_exits_one_when_not_ready(monkeypatch, capsys):
    _complete_production(monkeypatch)
    monkeypatch.setattr(app_config, "GEMINI_API_KEY", "")
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "status      : not_ready" in out
    assert "GEMINI_API_KEY" in out


def test_cli_emits_machine_readable_json(monkeypatch, capsys):
    import json

    _development(monkeypatch)
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ready"
    assert "required" in payload and "optional_integrations" in payload


def test_the_module_is_runnable_as_a_command():
    """`python -m app.release_readiness` must actually work as documented."""
    result = subprocess.run(
        [sys.executable, "-m", "app.release_readiness"],
        cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode in (0, 1), result.stderr
    assert "GlamGenius release readiness" in result.stdout
    assert "Required for production:" in result.stdout


def test_public_surface_is_explicit():
    for name in ("evaluate", "render", "main", "Status", "ReadinessReport"):
        assert name in release_readiness.__all__
