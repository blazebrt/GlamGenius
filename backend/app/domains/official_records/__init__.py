"""Authoritative, auditable government-record intelligence."""

from .matching import match_recall, resolve_matches
from .source import (
    AUTHORITY_FSSAI_FOSCOS,
    RECORD_TYPE_FOOD_RECALL,
    SOURCE_ADAPTER_VERSION,
    SOURCE_ERROR_CODES,
    SOURCE_URL,
    SourceError,
    normalise_batch,
    normalise_identity_text,
    parse_recall_xlsx,
)

__all__ = [
    "AUTHORITY_FSSAI_FOSCOS", "RECORD_TYPE_FOOD_RECALL", "SOURCE_ADAPTER_VERSION", "SOURCE_ERROR_CODES",
    "SOURCE_URL", "SourceError", "match_recall", "normalise_batch", "normalise_identity_text", "parse_recall_xlsx",
    "resolve_matches",
]
