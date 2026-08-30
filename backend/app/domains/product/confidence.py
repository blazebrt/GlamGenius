"""How much a product record can be trusted.

Every result carries one of these. There is no unlabelled state and no default
that quietly means "probably fine": a record with nothing behind it is
``not_enough_information``, which is an answer the product is willing to give.
"""
from __future__ import annotations

from enum import StrEnum


class ProductConfidence(StrEnum):
    #: A person on the team opened the pack or the source and confirmed it.
    VERIFIED = "verified"
    #: Transcribed by customers and corroborated by more than one of them.
    COMMUNITY = "community"
    #: Present, from one source or one transcription, nobody has checked it.
    UNVERIFIED = "unverified"
    #: We have a barcode and little or nothing else. Said plainly, not hidden.
    NOT_ENOUGH_INFORMATION = "not_enough_information"


CONFIDENCE_LEVELS: tuple[str, ...] = tuple(c.value for c in ProductConfidence)

#: What each level means, in the words a person reads. Kept beside the levels
#: so the two cannot drift (LEGAL_RULES.md: state, do not characterise).
CONFIDENCE_TEXT: dict[str, str] = {
    ProductConfidence.VERIFIED.value: "Checked by us against the pack.",
    ProductConfidence.COMMUNITY.value: "Transcribed by several people who own this.",
    ProductConfidence.UNVERIFIED.value: "From one source, not checked yet.",
    ProductConfidence.NOT_ENOUGH_INFORMATION.value: "Not enough information about this one yet.",
}

#: How many independent confirmations promote a record to community level.
COMMUNITY_THRESHOLD = 2
