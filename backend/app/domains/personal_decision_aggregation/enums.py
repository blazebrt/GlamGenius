"""Controlled Step 8D aggregation vocabulary.

Two enumerations, both deliberately structural. They describe the *shape* of
what Step 8C produced -- which reviewed directions are represented, and how
much of Step 8C's own output carries a reviewed mapping. Neither says anything
about the product, the ingredient, or the person.
"""

from enum import StrEnum


class PersonalSignalSet(StrEnum):
    """Which reviewed directions are represented among distinct rules.

    Set membership, never a tally. Ten distinct SUPPORTING rules alongside one
    distinct CAUTIONARY rule is MIXED, exactly as one-and-one is: the number of
    rules is not the size of an effect, and nothing here picks a winner.

    MIXED means only that both reviewed directions are present. It does not
    mean balanced, cancelled, equivalent, resolved, inconclusive, or that the
    person should hesitate.
    """

    NONE = "none"
    SUPPORTING_ONLY = "supporting_only"
    CAUTIONARY_ONLY = "cautionary_only"
    MIXED = "mixed"


class PersonalSemanticMappingCoverage(StrEnum):
    """How much of Step 8C's own claim output carries a reviewed mapping.

    This is coverage over Step 8C claim projections and nothing else.
    COMPLETE_SEMANTIC_MAPPING is the value most likely to be misread: it means
    every projection Step 8C actually produced has a reviewed mapping. It does
    not mean the formula was complete, that every ingredient identity
    resolved, that enough published or personal evidence exists, that the
    product is suitable, or that any product-level statement is permitted.

    Those questions are answered from the preserved upstream Step 8C object,
    by a later governed policy layer that does not exist yet.
    """

    NO_CLAIM_PROJECTIONS = "no_claim_projections"
    NO_MAPPED_SEMANTICS = "no_mapped_semantics"
    PARTIAL_SEMANTIC_MAPPING = "partial_semantic_mapping"
    COMPLETE_SEMANTIC_MAPPING = "complete_semantic_mapping"
