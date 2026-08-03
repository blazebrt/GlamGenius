"""Logging setup that stamps every line with the current request id."""
from __future__ import annotations

import logging

from app.shared.observability.request_id import get_request_id

FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
