"""Sentry bootstrap.

Initialising Sentry is optional. A missing DSN must not fail startup,
must not install any middleware, and must not perform any network call.
This is enforced by the ``if not dsn: return`` guard at the top of
``init_sentry``.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_BACKEND_DSN", "").strip()
    if not dsn:
        # Optional integration. No SDK client, no hooks, no network call.
        return

    # Never initialise Sentry when the process is running under pytest.
    # A test failure would otherwise emit an event to the real project
    # and pollute the dashboard. `init_sentry` runs at import time, before
    # any test has started, so `PYTEST_CURRENT_TEST` may not yet be in the
    # environment — instead detect the pytest module or an explicit
    # TESTING=1 flag.
    import sys
    if (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("TESTING") == "1"
    ):
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        # Requirements not installed — fail closed, log once, do not crash.
        logger.warning(
            "SENTRY_BACKEND_DSN is set but sentry-sdk is not installed. "
            "Install 'sentry-sdk[fastapi]' or unset the DSN."
        )
        return

    from .sentry_privacy import scrub_event

    environment = os.environ.get("SENTRY_ENVIRONMENT", "development")

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # PII is not sent by default; the scrubber is a second layer on top.
        send_default_pii=False,
        # 5xx responses become events, along with unhandled exceptions.
        integrations=[
            StarletteIntegration(failed_request_status_codes=set(range(500, 600))),
            FastApiIntegration(failed_request_status_codes=set(range(500, 600))),
        ],
        before_send=scrub_event,
        # Deliberately do NOT enable performance / tracing / profiling /
        # replay / logs. Fix 8 ships crash-only monitoring; performance is
        # a later decision.
    )
    logger.info("Sentry initialised (environment=%s)", environment)
