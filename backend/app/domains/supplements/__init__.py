"""Owned-supplement utility: package facts, overlap and safety boundaries."""

from app.domains.supplements.engine import (
    SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION,
    SUPPLEMENT_UTILITY_VERSION,
    build_utility,
    normalize_component,
)

__all__ = [
    "SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION",
    "SUPPLEMENT_UTILITY_VERSION",
    "build_utility",
    "normalize_component",
]
