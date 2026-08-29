"""Production readiness reporting (RR-01).

One place that answers "what still has to be configured before this can go to
production", so the answer is not scattered across ``config.py``, ``env.example``
and the operations docs.

This module **explains**; it does not decide. The decision stays with
:func:`app.config.validate_production_configuration`, which is what actually
refuses to start a misconfigured staging or production process. The overall
verdict here is taken from that function so the two can never disagree: if it
raises, this reports ``not_ready`` and repeats its reason verbatim.

Nothing here ever reads or prints a secret's value. Every entry is a
configuration **key name** plus a safe status word, so the report is safe to
paste into a ticket, a log, or a deployment transcript.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app import config


class Status(StrEnum):
    """What we can say about one configuration key without reading its value."""

    CONFIGURED = "configured"
    MISSING = "missing"
    PLACEHOLDER = "placeholder"
    DEVELOPMENT_DEFAULT = "development_default"
    INVALID = "invalid"
    #: The feature is switched off on purpose, so its keys are not required.
    DISABLED_OPTIONAL = "disabled_optional"
    #: Correct in the repository, but it depends on something outside it.
    REQUIRES_HOST_SCHEDULER = "requires_host_scheduler"


#: Statuses that mean a required key is not fit for production.
BLOCKING_STATUSES = frozenset({
    Status.MISSING, Status.PLACEHOLDER, Status.DEVELOPMENT_DEFAULT, Status.INVALID,
})

#: Substrings that mark a value as an unedited example rather than a real one.
#: These cover every placeholder form shipped in ``env.example`` — leaving one
#: of them in place is the most likely single-key configuration mistake, so the
#: check has to name it rather than call it configured.
PLACEHOLDER_MARKERS = (
    "placeholder", "example.com", "changeme", "change_me", "todo", "your_", "your-project", "fake",
)


@dataclass(frozen=True)
class ReadinessReport:
    app_env: str
    status: str
    required: dict[str, str]
    optional_integrations: dict[str, str]
    #: Verbatim reasons from the authoritative production validation.
    blocking: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_env": self.app_env,
            "status": self.status,
            "required": dict(self.required),
            "optional_integrations": dict(self.optional_integrations),
            "blocking": list(self.blocking),
            "notes": list(self.notes),
        }


def _present(value: str | None) -> bool:
    return bool(value and value.strip())


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _secret_status(value: str | None) -> Status:
    """Status for a value we must never echo."""
    if not _present(value):
        return Status.MISSING
    if _looks_like_placeholder(value or ""):
        return Status.PLACEHOLDER
    return Status.CONFIGURED


def _url_status(value: str | None, *, require_https: bool = False) -> Status:
    if not _present(value):
        return Status.MISSING
    assert value is not None
    if _looks_like_placeholder(value):
        return Status.PLACEHOLDER
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return Status.INVALID
    if require_https and parsed.scheme != "https":
        return Status.INVALID
    return Status.CONFIGURED


def _postgres_status() -> Status:
    url = config.POSTGRES_URL
    if not _present(url):
        return Status.MISSING
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return Status.INVALID
    if _looks_like_placeholder(host):
        return Status.PLACEHOLDER
    # A local database is right for development and wrong for production. The
    # caller decides which of those it is; this only reports what it sees.
    if host in ("localhost", "127.0.0.1", "::1"):
        return Status.DEVELOPMENT_DEFAULT
    return Status.CONFIGURED


def _origins_status() -> Status:
    if config.ALLOWED_ORIGINS_IS_DEFAULT:
        return Status.DEVELOPMENT_DEFAULT
    if not config.ALLOWED_ORIGINS:
        return Status.MISSING
    if "*" in config.ALLOWED_ORIGINS:
        return Status.INVALID
    for origin in config.ALLOWED_ORIGINS:
        parsed = urllib.parse.urlparse(origin)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Status.INVALID
        if host in ("localhost", "127.0.0.1"):
            return Status.DEVELOPMENT_DEFAULT
    return Status.CONFIGURED


def _sentry_status() -> Status:
    dsn = os.environ.get("SENTRY_BACKEND_DSN", "").strip()
    if not dsn:
        return Status.MISSING
    if _looks_like_placeholder(dsn):
        return Status.PLACEHOLDER
    parsed = urllib.parse.urlparse(dsn)
    # A DSN carries a key in its username. Structure is checked; nothing is shown.
    if not parsed.scheme or not parsed.netloc or not parsed.username:
        return Status.INVALID
    return Status.CONFIGURED


def _media_status() -> Status:
    backend = config.MEDIA_STORAGE_BACKEND.lower()
    if backend == "supabase":
        return Status.CONFIGURED
    if backend == "local":
        return Status.DEVELOPMENT_DEFAULT
    return Status.INVALID


def _core_requirements() -> dict[str, Status]:
    """Keys production needs regardless of which optional integrations are on."""
    return {
        "SUPABASE_URL": _url_status(config.SUPABASE_URL, require_https=True),
        "SUPABASE_ANON_KEY": _secret_status(config.SUPABASE_ANON_KEY),
        "SUPABASE_SERVICE_ROLE_KEY": _secret_status(config.SUPABASE_SERVICE_ROLE_KEY),
        "SUPABASE_JWT_ISSUER": _url_status(config.SUPABASE_JWT_ISSUER),
        "SUPABASE_JWKS_URL": _url_status(config.SUPABASE_JWKS_URL),
        "SUPABASE_STORAGE_BUCKET": (
            Status.CONFIGURED if _present(config.SUPABASE_STORAGE_BUCKET) else Status.MISSING
        ),
        "POSTGRES_URL": _postgres_status(),
        "GEMINI_API_KEY": _secret_status(config.GEMINI_API_KEY),
        "SENTRY_BACKEND_DSN": _sentry_status(),
        "MEDIA_STORAGE_BACKEND": _media_status(),
        "ALLOWED_ORIGINS": _origins_status(),
        "PRIVACY_POLICY_URL": _url_status(config.PRIVACY_POLICY_URL),
        "SUPPORT_URL": _url_status(config.SUPPORT_URL),
        "INVITE_REQUIRED": (
            Status.CONFIGURED if config.INVITE_REQUIRED else Status.INVALID
        ),
        "REQUIRE_ANALYSIS_CONSENT": (
            Status.CONFIGURED if config.REQUIRE_ANALYSIS_CONSENT else Status.INVALID
        ),
        "CONSENT_VERSION": (
            Status.CONFIGURED if _present(config.CONSENT_VERSION) else Status.MISSING
        ),
    }


def _google_calendar_state() -> tuple[str, dict[str, Status]]:
    """Google Calendar is optional: switched off, it requires nothing."""
    if not config.GOOGLE_CALENDAR_ENABLED:
        return Status.DISABLED_OPTIONAL.value, {}
    keys = {
        "GOOGLE_CALENDAR_CLIENT_ID": _secret_status(config.GOOGLE_CALENDAR_CLIENT_ID),
        "GOOGLE_CALENDAR_CLIENT_SECRET": _secret_status(config.GOOGLE_CALENDAR_CLIENT_SECRET),
        "GOOGLE_CALENDAR_REDIRECT_URI": _url_status(config.GOOGLE_CALENDAR_REDIRECT_URI),
        "GOOGLE_CALENDAR_APP_RETURN_URI": (
            Status.CONFIGURED
            if _present(config.GOOGLE_CALENDAR_APP_RETURN_URI)
            and "?" not in config.GOOGLE_CALENDAR_APP_RETURN_URI
            and "#" not in config.GOOGLE_CALENDAR_APP_RETURN_URI
            else Status.INVALID
        ),
        "GOOGLE_CALENDAR_CREDENTIAL_STORE": (
            Status.CONFIGURED
            if config.GOOGLE_CALENDAR_CREDENTIAL_STORE == "supabase_vault"
            else Status.INVALID
        ),
    }
    unmet = any(status in BLOCKING_STATUSES for status in keys.values())
    return ("incomplete" if unmet else "ready"), keys


def _live_environment_state() -> tuple[str, dict[str, Status]]:
    """Open-Meteo weather and air quality, off unless deliberately enabled."""
    mode = config.OPEN_METEO_MODE
    if mode not in ("disabled", "evaluation", "commercial"):
        return "invalid", {"OPEN_METEO_MODE": Status.INVALID}
    if config.LIVE_ENVIRONMENT_PROVIDER and config.LIVE_ENVIRONMENT_PROVIDER != "open_meteo":
        return "invalid", {"LIVE_ENVIRONMENT_PROVIDER": Status.INVALID}
    if mode == "disabled" or config.LIVE_ENVIRONMENT_PROVIDER != "open_meteo":
        return Status.DISABLED_OPTIONAL.value, {}
    if mode == "evaluation":
        # Permitted in development only; production validation refuses it.
        if config.APP_ENV in ("production", "staging"):
            return "invalid", {"OPEN_METEO_MODE": Status.INVALID}
        return "evaluation", {"OPEN_METEO_MODE": Status.CONFIGURED}
    key_status = _secret_status(config.OPEN_METEO_API_KEY)
    return (
        "commercial" if key_status is Status.CONFIGURED else "incomplete",
        {"OPEN_METEO_API_KEY": key_status},
    )


def evaluate() -> ReadinessReport:
    """Build the readiness report for the current process configuration."""
    app_env = config.APP_ENV
    is_production_like = app_env in ("production", "staging")

    required = {key: status.value for key, status in _core_requirements().items()}
    google_state, google_keys = _google_calendar_state()
    live_state, live_keys = _live_environment_state()

    optional: dict[str, str] = {
        "google_calendar": google_state,
        "live_environment": live_state,
        # The repository cannot see the host's crontab. Claiming otherwise
        # would be the one lie that matters most on launch day.
        "notification_worker": Status.REQUIRES_HOST_SCHEDULER.value,
    }
    for key, status in {**google_keys, **live_keys}.items():
        optional[key] = status.value

    notes: list[str] = []
    if not is_production_like:
        notes.append(
            f"APP_ENV={app_env!r}: production requirements are reported for information "
            "only and do not fail this check."
        )
    notes.append(
        "Run 'python -m app.workers.notifications' once per hour from the host "
        "scheduler. Nothing in this repository can verify that it is scheduled."
    )
    if google_state == Status.DISABLED_OPTIONAL.value:
        notes.append("Google Calendar is off; its credentials are not required.")
    if live_state == Status.DISABLED_OPTIONAL.value:
        notes.append("Live weather and air quality are off; Open-Meteo is not required.")

    # The authoritative verdict. Its messages name configuration keys and never
    # carry values, so repeating them verbatim is safe.
    blocking: list[str] = []
    try:
        config.validate_production_configuration()
    except RuntimeError as exc:
        blocking.append(str(exc))

    if is_production_like:
        # Startup validation is the authority on what may boot, but it is not a
        # superset of this report: it screens the Supabase keys for "placeholder"
        # and "fake" only, so an unedited "your_anon_key_here" passes it. Without
        # this, the report could print PLACEHOLDER against a key and still call
        # the deployment ready — a green light on the one question this command
        # exists to answer. Report key names only; never the offending value.
        for key in sorted(required):
            if Status(required[key]) in BLOCKING_STATUSES:
                blocking.append(f"{key} is {required[key]} and must be set before production.")
        for state, label in ((google_state, "Google Calendar"), (live_state, "Live environment")):
            if state in ("incomplete", "invalid"):
                blocking.append(f"{label} is enabled but its configuration is {state}.")

    if blocking:
        status = "not_ready"
    elif is_production_like:
        status = "ready"
    else:
        # Development is ready for what it is. Production gaps are listed above
        # so they can be closed before anyone tries to deploy.
        status = "ready"

    return ReadinessReport(
        app_env=app_env,
        status=status,
        required=required,
        optional_integrations=optional,
        blocking=tuple(blocking),
        notes=tuple(notes),
    )


def render(report: ReadinessReport) -> str:
    """A human-readable report. Key names and statuses only."""
    lines = [
        "GlamGenius release readiness",
        f"  environment : {report.app_env}",
        f"  status      : {report.status}",
        "",
        "Required for production:",
    ]
    for key in sorted(report.required):
        lines.append(f"  {key:<32} {report.required[key]}")
    lines.append("")
    lines.append("Optional integrations:")
    for key in sorted(report.optional_integrations):
        lines.append(f"  {key:<32} {report.optional_integrations[key]}")
    if report.blocking:
        lines.append("")
        lines.append("Blocking — must be resolved before production:")
        lines.extend(f"  - {reason}" for reason in report.blocking)
    if report.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in report.notes)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when ready for the configured feature set, 1 when not."""
    argv = sys.argv[1:] if argv is None else argv
    report = evaluate()
    if "--json" in argv:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))  # noqa: T201 - CLI output
    else:
        print(render(report))  # noqa: T201 - CLI output
    return 0 if report.ready else 1


__all__ = [
    "BLOCKING_STATUSES",
    "ReadinessReport",
    "Status",
    "evaluate",
    "main",
    "render",
]


if __name__ == "__main__":
    raise SystemExit(main())
