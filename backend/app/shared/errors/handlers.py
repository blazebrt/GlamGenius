"""Global exception handlers.

Registered on the existing FastAPI app so *both* V1 and V2 routes get a
consistent, structured, logged failure — instead of FastAPI's bare 500 with no
code and nothing to correlate against a log line.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import AppError
from app.shared.observability.request_id import get_request_id

logger = logging.getLogger(__name__)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    detail = exc.to_detail()
    detail["request_id"] = get_request_id()
    # Expected failures are information, not incidents. Log at INFO so the
    # error stream stays meaningful.
    logger.info(
        "handled_error code=%s status=%s path=%s request_id=%s",
        exc.code.value,
        exc.status_code,
        request.url.path,
        detail["request_id"],
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id()
    # Full traceback to the log, never to the caller — a stack trace can leak
    # file paths, query fragments and configuration.
    logger.exception(
        "unhandled_error path=%s request_id=%s type=%s",
        request.url.path,
        request_id,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": (
                    "Something went wrong on our side. Please try again."
                ),
                "retryable": True,
                "request_id": request_id,
            }
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
