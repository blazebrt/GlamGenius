"""Regression coverage for failures observed on the first live Step 8 deploy."""
from __future__ import annotations

import logging

from app.shared.observability.logging import OAuthRedactionFilter
from uvicorn.logging import AccessFormatter


def test_uvicorn_access_redaction_preserves_formatter_contract() -> None:
    """Redaction must not destroy the five arguments AccessFormatter requires."""
    code = "STEP8_LIVE_CODE_SENTINEL"
    state = "STEP8_LIVE_STATE_SENTINEL"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:43210",
            "GET",
            f"/oauth/callback?code={code}&state={state}",
            "1.1",
            200,
        ),
        None,
    )

    redactor = OAuthRedactionFilter()
    assert redactor.filter(record) is True
    # configure_logging attaches the same filter at the logger and handler
    # boundaries. A second pass must remain safe.
    assert redactor.filter(record) is True

    assert isinstance(record.args, tuple)
    assert len(record.args) == 5
    assert code not in str(record.args)
    assert state not in str(record.args)

    rendered = AccessFormatter("%(message)s").format(record)
    assert code not in rendered
    assert state not in rendered
    assert "[REDACTED]" in rendered
