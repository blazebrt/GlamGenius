"""Typed errors that carry everything the app needs to react well.

These serialise into the ``{"detail": {"code": ..., "message": ...}}`` shape the
V1 API already uses and the frontend already understands (``errorMessage()`` in
``src/services/api.ts``). Keeping the shape means no screen has to change to keep
working.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.shared.errors.codes import AIFailureType, ErrorCode


class AppError(Exception):
    """Base class for every deliberate, user-facing failure."""

    status_code: int = 400
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[ErrorCode] = None,
        retryable: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.extra: Dict[str, Any] = extra or {}

    def to_detail(self) -> Dict[str, Any]:
        detail: Dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        detail.update(self.extra)
        return detail


class AnalysisUnavailableError(AppError):
    """AI analysis did not produce a trustworthy result.

    This is the error that replaces the old fabricated fallback. It never
    carries invented analysis content, and ``allowance_consumed`` is always
    False — a failed analysis must not cost the user one of their checks.
    """

    status_code = 503
    code = ErrorCode.ANALYSIS_UNAVAILABLE
    retryable = True

    def __init__(
        self,
        message: str = "We could not analyse this image reliably.",
        *,
        failure_type: AIFailureType = AIFailureType.PROVIDER_ERROR,
        guidance: Optional[List[str]] = None,
        retryable: bool = True,
        ai_run_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            extra={
                "allowance_consumed": False,
                "failure_type": failure_type.value,
                "guidance": guidance or DEFAULT_PHOTO_GUIDANCE,
                "ai_run_id": ai_run_id,
            },
        )
        self.failure_type = failure_type


# Specific, actionable, and free of blame. Shown verbatim in the app.
DEFAULT_PHOTO_GUIDANCE: List[str] = [
    "Face a window or other soft light — avoid a bright light directly behind you",
    "Hold the camera at arm's length, roughly at eye level",
    "Keep the whole area you want checked inside the frame and in focus",
    "Remove sunglasses, and pull hair back if you are checking your face",
]

PROVIDER_DOWN_GUIDANCE: List[str] = [
    "This is a problem on our side, not with your photo",
    "Your check has not been used — try again in a few minutes",
]


class ConsentRequiredError(AppError):
    status_code = 403
    code = ErrorCode.CONSENT_REQUIRED
    retryable = False

    def __init__(
        self,
        message: str = (
            "Please agree to photo analysis before we look at a photo."
        ),
        *,
        consent_type: str = "photo_analysis",
        consent_version: str = "",
    ) -> None:
        super().__init__(
            message,
            extra={
                "consent_type": consent_type,
                "consent_version": consent_version,
            },
        )


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND
    retryable = False

    def __init__(self, message: str = "Not found.") -> None:
        super().__init__(message)


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    retryable = False

    def __init__(self, message: str, *, allowed: Optional[List[str]] = None) -> None:
        super().__init__(message, extra={"allowed_types": allowed or []})


class MediaTooLargeError(AppError):
    status_code = 413
    code = ErrorCode.MEDIA_TOO_LARGE
    retryable = False

    def __init__(self, message: str, *, max_bytes: int = 0) -> None:
        super().__init__(message, extra={"max_bytes": max_bytes})


class SubscriptionsUnavailableError(AppError):
    status_code = 403
    code = ErrorCode.SUBSCRIPTIONS_UNAVAILABLE
    retryable = False

    def __init__(
        self,
        message: str = (
            "GlamGenius is in a private beta, so there is nothing to pay for yet. "
            "Your invite already gives you full access."
        ),
    ) -> None:
        super().__init__(message)


class FeatureUnavailableError(AppError):
    status_code = 404
    code = ErrorCode.FEATURE_UNAVAILABLE
    retryable = False

    def __init__(self, feature: str) -> None:
        super().__init__(
            "This part of GlamGenius is not switched on yet.",
            extra={"feature": feature},
        )


class ValidationFailedError(AppError):
    status_code = 422
    code = ErrorCode.VALIDATION_FAILED
    retryable = False

    def __init__(self, message: str, *, field: Optional[str] = None) -> None:
        super().__init__(message, extra={"field": field} if field else None)
