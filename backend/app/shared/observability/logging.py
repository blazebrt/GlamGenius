"""Logging setup that stamps every line with the current request id."""
from __future__ import annotations

import logging
import re

from app.shared.observability.request_id import get_request_id

FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class OAuthRedactionFilter(logging.Filter):
    """Redact OAuth callback/token material at the logging boundary."""

    _query = re.compile(r"([?&](?:code|state|access_token|refresh_token|client_secret)=)[^&#\s]+", re.I)
    _header = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._header.sub(r"\1[REDACTED]", self._query.sub(r"\1[REDACTED]", record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: ("[REDACTED]" if re.search(r"code|state|token|secret", str(key), re.I) else value) for key, value in record.args.items()}
            else:
                record.args = tuple("[REDACTED]" if re.search(r"code|state|token|secret", str(value), re.I) else value for value in record.args)
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
