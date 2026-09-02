"""Structured shopper observations. Never reviews, ratings, or a social feed.

Deliberately its own domain. Community sits beside the other epistemic layers
and outranks none of them: a label fact, a scientific grade, an official record
and a shopper observation are four different kinds of claim, and this package
must never be imported into grading, evidence, or official records.
"""

from .observations import (
    OBSERVATION_CODES,
    PACK_CONDITION_OBSERVATIONS,
    PRODUCT_DATA_OBSERVATIONS,
    SCOPE_BATCH,
    SCOPE_PRODUCT,
    is_batch_scoped,
    normalise_batch,
    observation_scope,
)
from .policy import (
    ACTIVE_WINDOW_DAYS,
    COMMUNITY_POLICY_VERSION,
    MIN_PUBLIC_REPORTERS,
    MIN_UNIQUE_PHOTOS,
)

__all__ = [
    "ACTIVE_WINDOW_DAYS", "COMMUNITY_POLICY_VERSION", "MIN_PUBLIC_REPORTERS", "MIN_UNIQUE_PHOTOS",
    "OBSERVATION_CODES", "PACK_CONDITION_OBSERVATIONS", "PRODUCT_DATA_OBSERVATIONS",
    "SCOPE_BATCH", "SCOPE_PRODUCT", "is_batch_scoped", "normalise_batch", "observation_scope",
]
