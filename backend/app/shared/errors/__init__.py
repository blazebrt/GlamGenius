"""Structured application errors."""
from app.shared.errors.codes import ErrorCode
from app.shared.errors.exceptions import (
    AnalysisUnavailableError,
    AppError,
    ConsentRequiredError,
    FeatureUnavailableError,
    MediaTooLargeError,
    NotFoundError,
    UnsupportedMediaTypeError,
    ValidationFailedError,
)

__all__ = [
    "ErrorCode",
    "AppError",
    "AnalysisUnavailableError",
    "ConsentRequiredError",
    "FeatureUnavailableError",
    "MediaTooLargeError",
    "NotFoundError",
    "UnsupportedMediaTypeError",
    "ValidationFailedError",
]
