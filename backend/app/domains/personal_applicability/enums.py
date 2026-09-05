"""Controlled Step 8B personal-evidence applicability vocabulary."""

from enum import StrEnum


class PersonalApplicabilityCategory(StrEnum):
    """The explicit product category shared with Steps 8A and 7C."""

    PACKAGED_FOOD = "packaged_food"
    SKIN_CARE = "skin_care"
    HAIR_CARE = "hair_care"
    COSMETICS = "cosmetics"


class PersonalApplicabilityOperator(StrEnum):
    """The only V1 exact-match operators."""

    EQUALS_ANY = "equals_any"
    CONTAINS_ANY = "contains_any"


class PersonalApplicabilityStatus(StrEnum):
    """Ingredient-level evidence availability, never a verdict."""

    PERSONAL_EVIDENCE_AVAILABLE = "personal_evidence_available"
    NOT_ENOUGH_INFORMATION = "not_enough_information"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
