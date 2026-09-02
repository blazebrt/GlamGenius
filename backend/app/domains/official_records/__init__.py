"""Authoritative, auditable government-record intelligence."""

from .matching import match_recall
from .source import (
    AUTHORITY_FSSAI_FOSCOS,
    RECORD_TYPE_FOOD_RECALL,
    SOURCE_ADAPTER_VERSION,
    SOURCE_URL,
    parse_recall_xlsx,
)

__all__ = ["AUTHORITY_FSSAI_FOSCOS", "RECORD_TYPE_FOOD_RECALL", "SOURCE_ADAPTER_VERSION", "SOURCE_URL", "match_recall", "parse_recall_xlsx"]
