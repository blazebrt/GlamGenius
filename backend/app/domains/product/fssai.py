"""The FSSAI licence number printed on Indian packaged food.

Fourteen digits, usually near "Lic. No." or "FSSAI". Reading it is worth doing
because it identifies the manufacturer to a public register, which is a fact
about the pack rather than a judgement about the food.

This module only finds and validates the number. It does not look it up, and it
does not infer anything about the product from it.
"""
from __future__ import annotations

import re

LICENCE_LENGTH = 14

# The label wording varies a lot: "FSSAI Lic. No.", "Lic No:", "License no",
# "FSSAI:" and plain "FSSAI" above a bare number are all common.
_LABELLED = re.compile(
    r"(?:fssai|f\.?s\.?s\.?a\.?i\.?)?\s*(?:lic(?:ence|ense)?\.?\s*(?:no\.?|number)?|reg\.?\s*no\.?)?"
    rf"\s*[:\-]?\s*((?:\d[\s\-]?){{{LICENCE_LENGTH}}})",
    re.IGNORECASE,
)
_BARE_14 = re.compile(rf"(?<!\d)(\d{{{LICENCE_LENGTH}}})(?!\d)")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def is_valid_licence(value: str | None) -> bool:
    """Fourteen digits, and not obviously a placeholder.

    FSSAI publishes no checksum, so this is a shape check. Claiming more
    certainty than the format allows would be inventing a validation.
    """
    digits = _digits(value or "")
    if len(digits) != LICENCE_LENGTH:
        return False
    # 00000000000000 and friends are placeholders, not licences.
    return len(set(digits)) != 1


def find_licence(text: str | None) -> str | None:
    """Pull the licence number out of transcribed label text, or return None.

    Prefers a number that appears next to licence wording. Falls back to a bare
    fourteen-digit run only when the label has one and only one, because a pack
    carries other long numbers — batch codes, phone numbers, barcodes.
    """
    if not text:
        return None

    for match in _LABELLED.finditer(text):
        candidate = _digits(match.group(1))
        if is_valid_licence(candidate):
            # Only trust it when licence wording is genuinely nearby.
            window = text[max(0, match.start() - 24):match.end()].lower()
            if "fssai" in window or "lic" in window or "reg" in window:
                return candidate

    bare = [m.group(1) for m in _BARE_14.finditer(text) if is_valid_licence(m.group(1))]
    unique = sorted(set(bare))
    if len(unique) == 1:
        return unique[0]
    return None
