"""Safe, feature-aware deployment configuration readiness reporting.

This command deliberately reports only configuration *states*.  It never
prints values, DSNs, tokens, credentials, or URLs supplied by a deployer.
Production validity remains owned by :func:`validate_production_configuration`;
this is an explanation layer for the same startup contract.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from app import config

PRODUCTION_ENVS = {"production", "staging"}
PLACEHOLDER_MARKERS = ("placeholder", "your_", "change_me", "example.com", "fake")


def _is_placeholder(value: str) -> bool:
    return any(marker in value.casefold() for marker in PLACEHOLDER_MARKERS)


def _value_state(value: str, *, placeholder: bool = True) -> str:
    if not value:
        return "missing"
    if placeholder and _is_placeholder(value):
        return "placeholder"
    return "configured"


def _boolean_state(value: bool, *, required_value: bool = True) -> str:
    return "configured" if value is required_value else "invalid"


def _core_requirements() -> dict[str, str]:
    postgres_is_default = "POSTGRES_URL" not in os.environ
    return {
        "SUPABASE_URL": _value_state(config.SUPABASE_URL),
        "SUPABASE_ANON_KEY": _value_state(config.SUPABASE_ANON_KEY),
        "SUPABASE_SERVICE_ROLE_KEY": _value_state(config.SUPABASE_SERVICE_ROLE_KEY),
        "SUPABASE_JWKS_URL": _value_state(config.SUPABASE_JWKS_URL),
        "SUPABASE_JWT_ISSUER": _value_state(config.SUPABASE_JWT_ISSUER),
        "POSTGRES_URL": "development_default" if postgres_is_default else _value_state(config.POSTGRES_URL, placeholder=False),
        "SUPABASE_STORAGE_BUCKET": _value_state(config.SUPABASE_STORAGE_BUCKET, placeholder=False),
        "GEMINI_API_KEY": _value_state(config.GEMINI_API_KEY),
        "SENTRY_BACKEND_DSN": _value_state(os.environ.get("SENTRY_BACKEND_DSN", ""), placeholder=False),
        "INVITE_REQUIRED": _boolean_state(config.INVITE_REQUIRED),
        "REQUIRE_ANALYSIS_CONSENT": _boolean_state(config.REQUIRE_ANALYSIS_CONSENT),
        "CONSENT_VERSION": _value_state(config.CONSENT_VERSION, placeholder=False),
        "MEDIA_STORAGE_BACKEND": "configured" if config.MEDIA_STORAGE_BACKEND == "supabase" else "invalid",
        "PRIVACY_POLICY_URL": _value_state(config.PRIVACY_POLICY_URL),
        "SUPPORT_URL": _value_state(config.SUPPORT_URL),
        "ALLOWED_ORIGINS": (
            "development_default"
            if config.ALLOWED_ORIGINS_IS_DEFAULT
            else "missing" if not config.ALLOWED_ORIGINS else "configured"
        ),
    }


def _google_calendar() -> dict[str, str]:
    if not config.GOOGLE_CALENDAR_ENABLED:
        return {"status": "disabled_optional"}
    requirements = {
        "GOOGLE_CALENDAR_CLIENT_ID": _value_state(config.GOOGLE_CALENDAR_CLIENT_ID),
        "GOOGLE_CALENDAR_CLIENT_SECRET": _value_state(config.GOOGLE_CALENDAR_CLIENT_SECRET),
        "GOOGLE_CALENDAR_REDIRECT_URI": _value_state(config.GOOGLE_CALENDAR_REDIRECT_URI),
        "GOOGLE_CALENDAR_APP_RETURN_URI": (
            "configured"
            if config.GOOGLE_CALENDAR_APP_RETURN_URI
            and "?" not in config.GOOGLE_CALENDAR_APP_RETURN_URI
            and "#" not in config.GOOGLE_CALENDAR_APP_RETURN_URI
            else "invalid"
        ),
        "GOOGLE_CALENDAR_CREDENTIAL_STORE": (
            "configured" if config.GOOGLE_CALENDAR_CREDENTIAL_STORE == "supabase_vault" else "invalid"
        ),
    }
    requirements["status"] = "ready" if all(
        value == "configured" for key, value in requirements.items() if key != "status"
    ) else "not_ready"
    return requirements


def _live_environment() -> dict[str, str]:
    if not config.LIVE_ENVIRONMENT_PROVIDER and config.OPEN_METEO_MODE == "disabled":
        return {"status": "disabled_optional", "mode": "disabled"}
    if config.LIVE_ENVIRONMENT_PROVIDER != "open_meteo":
        return {"status": "invalid", "mode": config.OPEN_METEO_MODE or "disabled"}
    if config.OPEN_METEO_MODE == "evaluation":
        return {
            "status": "invalid" if config.APP_ENV in PRODUCTION_ENVS else "evaluation",
            "mode": "evaluation",
        }
    if config.OPEN_METEO_MODE == "commercial":
        return {
            "status": "ready" if config.OPEN_METEO_API_KEY else "not_ready",
            "mode": "commercial",
            "OPEN_METEO_API_KEY": _value_state(config.OPEN_METEO_API_KEY),
        }
    return {"status": "invalid", "mode": config.OPEN_METEO_MODE or "disabled"}


def readiness_report() -> dict[str, Any]:
    """Return a safe report for the current process configuration.

    Only variable names and status categories are included.  The canonical
    production validator decides the final production outcome, avoiding a
    second, weaker validation contract.
    """
    core = _core_requirements()
    calendar = _google_calendar()
    live_environment = _live_environment()
    validation_error: str | None = None
    if config.APP_ENV in PRODUCTION_ENVS:
        try:
            config.validate_production_configuration()
        except RuntimeError:
            # The individual report states identify what must be configured;
            # do not surface values embedded in an operator-supplied setting.
            validation_error = "production_configuration_invalid"

    ready = (
        config.APP_ENV not in PRODUCTION_ENVS
        or (
            validation_error is None
            and calendar["status"] in {"ready", "disabled_optional"}
            and live_environment["status"] in {"ready", "disabled_optional"}
        )
    )
    return {
        "status": "development" if config.APP_ENV not in PRODUCTION_ENVS else ("ready" if ready else "not_ready"),
        "environment": config.APP_ENV,
        "required": core,
        "optional_integrations": {
            "google_calendar": calendar,
            "open_meteo": live_environment,
            "native_push": {
                "status": "requires_runtime_scheduler",
                "scheduler": "external_hourly",
                "note": "The application cannot verify host scheduler configuration.",
            },
        },
        "production_validation": "valid" if validation_error is None else "invalid",
    }


def _render(report: dict[str, Any]) -> str:
    lines = [f"Release readiness: {report['status']} ({report['environment']})", "", "Required configuration:"]
    lines.extend(f"  {key}: {value}" for key, value in report["required"].items())
    lines.append("")
    lines.append("Optional integrations:")
    for name, details in report["optional_integrations"].items():
        lines.append(f"  {name}: {details['status']}")
        for key, value in details.items():
            if key not in {"status", "note"}:
                lines.append(f"    {key}: {value}")
    lines.append("")
    lines.append(f"Production validation: {report['production_validation']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Report safe GlamGenius release configuration readiness.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable status only")
    args = parser.parse_args(argv)
    report = readiness_report()
    sys.stdout.write((json.dumps(report, sort_keys=True) if args.json else _render(report)) + "\n")
    if report["status"] == "not_ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
