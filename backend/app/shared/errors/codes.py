"""Every error code the API can return.

The app switches on these strings, so they are part of the public contract.
Adding one is fine; changing or removing one is a breaking change.
"""
from enum import Enum


class ErrorCode(str, Enum):
    # --- AI ---
    ANALYSIS_UNAVAILABLE = "ANALYSIS_UNAVAILABLE"

    # --- Access and identity ---
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"

    # --- Media ---
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    STORAGE_MISCONFIGURED = "STORAGE_MISCONFIGURED"

    # --- Product state ---
    FEATURE_UNAVAILABLE = "FEATURE_UNAVAILABLE"

    # --- Input ---
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFLICT = "CONFLICT"

    # --- Catch-all ---
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AIFailureType(str, Enum):
    """Why an AI run did not produce a usable, validated result.

    Recorded on every failed ``ai_runs`` row so provider problems can be told
    apart from our own schema drift without reading logs.
    """

    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    EMPTY_RESPONSE = "empty_response"
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    IMAGE_REJECTED = "image_rejected"
