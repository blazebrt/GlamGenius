"""Controlled Step 7C projection vocabulary."""

from enum import StrEnum


class InterpretationCategory(StrEnum):
    """The explicit product context supplied by the caller."""

    PACKAGED_FOOD = "packaged_food"
    SKIN_CARE = "skin_care"
    HAIR_CARE = "hair_care"
    COSMETICS = "cosmetics"


class ProjectedIdentityStatus(StrEnum):
    """A strict pass-through view of the identity answer produced upstream.

    This vocabulary is not another identity authority. Step 7C copies it from
    the formula projection and fails if the upstream layer introduces a state
    this reader has not deliberately implemented.
    """

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class InterpretationStatus(StrEnum):
    EVIDENCE_AVAILABLE = "evidence_available"
    NOT_ENOUGH_INFORMATION = "not_enough_information"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
