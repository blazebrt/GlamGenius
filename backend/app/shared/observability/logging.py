"""Logging setup that stamps every line with the current request id."""
from __future__ import annotations

import logging
import re
import traceback

from app.shared.observability.request_id import get_request_id

FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class OAuthRedactionFilter(logging.Filter):
    """Redact OAuth callback/token material at the logging boundary."""

    _query = re.compile(r"([?&](?:code|state|access_token|refresh_token|client_secret)=)[^&#\s]+", re.I)
    _kv = re.compile(r"((?:[\"']?\b(?:code|state|access_token|refresh_token|client_secret)\b[\"']?)\s*[:=]\s*[\"']?)[^\s,}&\"']+", re.I)
    _header = re.compile(r"((?:authorization\s*[:=]\s*)?Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        # Materialize parameterized messages before redacting. This covers
        # dicts/tuples passed as args and prevents a later formatter or handler
        # from reconstructing the secret from the original arguments.
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — logging must never break a request
            message = str(record.msg)
        message = self._header.sub(r"\1[REDACTED]", message)
        message = self._query.sub(r"\1[REDACTED]", message)
        message = self._kv.sub(r"\1[REDACTED]", message)
        record.msg = message
        record.args = ()
        if record.exc_info:
            # Formatting an exception later would otherwise bypass this
            # filter. Preserve a redacted traceback as text and drop the raw
            # exception tuple before any handler sees it.
            trace = "".join(traceback.format_exception(*record.exc_info))
            trace = self._header.sub(r"\1[REDACTED]", self._query.sub(r"\1[REDACTED]", self._kv.sub(r"\1[REDACTED]", trace)))
            record.msg = f"{record.msg}\n{trace}"
            record.exc_info = None
        if record.exc_text:
            record.exc_text = self._kv.sub(r"\1[REDACTED]", self._query.sub(r"\1[REDACTED]", record.exc_text))
        if record.stack_info:
            record.stack_info = self._kv.sub(r"\1[REDACTED]", self._query.sub(r"\1[REDACTED]", record.stack_info))
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    request_filter = RequestIdFilter()
    oauth_filter = OAuthRedactionFilter()
    handler.addFilter(request_filter)
    handler.addFilter(oauth_filter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Uvicorn's access/error loggers normally own their handlers and do not
    # propagate to the root logger. Attach the same redactor there so a
    # callback query string or Authorization header is safe whichever logger
    # emits it.
    for logger_name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.addFilter(oauth_filter)
        for existing_handler in logger.handlers:
            existing_handler.addFilter(oauth_filter)
