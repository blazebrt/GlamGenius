"""How sure we are, and whether anybody has checked.

These two vocabularies belong to the evidence domain because every knowledge
set needs them and they must mean the same thing in each one. Confidence is
about the strength of the finding. Verification is about whether a human has
opened the source and confirmed the number — a different question, and the one
the constitution's knowledge-verification rule turns on.
"""
from __future__ import annotations

from enum import StrEnum


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Verification(StrEnum):
    """Has a person opened the source and confirmed the number?"""

    UNVERIFIED = "unverified"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
