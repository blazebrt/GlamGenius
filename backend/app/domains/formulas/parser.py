"""Splitting a printed ingredient list into the entries it actually prints.

This module does one mechanical job: find the **top-level entries** of a printed
ingredient list. It is not natural-language understanding, it does not know what
any of those entries mean, and it never invents one.

Its whole design is a refusal to guess, because every guess available here is
one that silently changes what a formula says.

**Only a top-level comma separates entries.** Comma is the ordinary ingredient
list separator, and it is the only character explicit enough to act on. The
others were each considered and rejected, because each of them occurs *inside*
real INCI names:

* ``/`` — ``Acrylates/C10-30 Alkyl Acrylate Crosspolymer``, ``CI 77491/CI 77492``,
  ``Aqua/Water/Eau``. Splitting on it shatters one ingredient into fragments
  that name nothing.
* ``-`` — ``PEG-40 Hydrogenated Castor Oil``, ``Sodium C14-16 Olefin Sulfonate``,
  ``Vitamin-E``.
* ``;`` — rare as a separator, and a list that uses it is unusual enough to be
  worth a human look rather than a guess.
* newline — a wrapped line inside one long name is indistinguishable from a
  line break between two, and choosing wrong merges or splits an ingredient.
* ``&``, ``+``, the word "and" — all appear inside supplied trade names.

A list that uses one of those as its real separator therefore parses as a single
entry, and that entry does not resolve. That is the intended failure: one
unresolved entry is recoverable, and a name invented by splitting is not.

**A comma inside balanced grouping is not a separator.** ``Parfum (Fragrance,
Aroma)`` is one printed ingredient. Parentheses, square and curly brackets are
tracked, nested, and the text inside them is preserved exactly — never stripped,
never expanded, never read. ``Water (Aqua/Eau)`` is one entry, not three: which
identities a parenthetical names is a question for a reviewed identity claim,
not for a parser guessing at what an abbreviation meant.

**Malformed grouping fails closed for the whole list.** Not for the entry that
went wrong, and not for the tail after it: the whole list. A parser that
returned the well-formed prefix would hand a caller an ingredient analysis that
looks complete and is missing everything after the first unclosed bracket —
which is far worse than saying it could not read the list.

**Empty entries are malformed, never dropped.** ``Water,,Glycerin`` is not
"water and glycerin". Silently deleting the empty entry would renumber
everything after it, so a position reported downstream would refer to a
different ingredient than the one printed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: The longest printed list this layer will read. Deliberately the same bound
#: the product-label transcription schema already puts on ``ingredients_text``
#: (``app/domains/product/extraction.py``), so a string that layer accepted can
#: always be parsed here. It is restated rather than imported: importing it
#: would couple this deterministic engine to the scan pipeline, which Step 7B
#: must not touch. A test asserts the two numbers still agree.
MAX_INGREDIENTS_TEXT_LENGTH = 4000

#: The most entries one formula may contain. Deliberately equal to Step 7A's
#: ``MAX_BATCH_NAMES`` so a parsed formula always fits one batch resolution — a
#: longer list is refused outright rather than resolved in pieces. A test
#: asserts the two constants still agree.
MAX_FORMULA_TOKENS = 128

#: The only character that separates one printed entry from the next.
TOP_LEVEL_DELIMITER = ","

#: Grouping pairs whose contents are protected from the delimiter. Curly braces
#: are included because a transcription may carry them; they are protected on
#: exactly the same terms and are equally never interpreted.
_GROUPING_PAIRS: dict[str, str] = {"(": ")", "[": "]", "{": "}"}
_CLOSERS: frozenset[str] = frozenset(_GROUPING_PAIRS.values())


class ParseStatus(StrEnum):
    """How reading a printed list turned out.

    Every non-``PARSED`` value means *no entries are returned at all*. There is
    no partial success: a caller either has the whole list or knows it does not.
    """

    #: The list was read whole. Entries are returned in printed order.
    PARSED = "parsed"
    #: Nothing to read — absent, empty, or only whitespace.
    EMPTY = "empty"
    #: Unbalanced grouping, or an entry with no text in it.
    MALFORMED = "malformed"
    #: Longer than :data:`MAX_INGREDIENTS_TEXT_LENGTH`.
    TOO_LONG = "too_long"
    #: More than :data:`MAX_FORMULA_TOKENS` entries.
    TOO_MANY_ITEMS = "too_many_items"


@dataclass(frozen=True)
class FormulaToken:
    """One printed entry, exactly as it was printed.

    ``raw_name`` keeps its casing, punctuation, percentages, slashes, hyphens
    and any bracketed text. Only surrounding whitespace is removed. Canonical
    normalisation happens once, inside Step 7A, and is not repeated here — a
    second normalizer is a second set of rules to drift apart.
    """

    #: 1-based printed order. **Order only.** Not a concentration, not a
    #: ranking, not importance — see ``docs/architecture/FORMULA_RESOLUTION.md``.
    position: int
    raw_name: str


@dataclass(frozen=True)
class FormulaParse:
    """The outcome of reading one printed list."""

    status: ParseStatus
    tokens: tuple[FormulaToken, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is ParseStatus.PARSED


def _failed(status: ParseStatus) -> FormulaParse:
    """Every failure returns no tokens. There is no partial parse."""
    return FormulaParse(status=status, tokens=())


def _split_top_level(text: str) -> list[str] | None:
    """Split on top-level commas, or ``None`` when grouping is unbalanced.

    One pass, tracking a stack of open groupings. A comma is a separator only at
    depth zero. A closer that does not match the innermost opener — and a closer
    with nothing open at all — is malformed immediately rather than at the end,
    so ``Water (Aqua], Glycerin`` fails on the ``]`` rather than being read as
    one long entry.
    """
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []

    for character in text:
        if character in _GROUPING_PAIRS:
            stack.append(_GROUPING_PAIRS[character])
            current.append(character)
        elif character in _CLOSERS:
            # A closer with nothing open, or the wrong closer for what is open.
            if not stack or stack[-1] != character:
                return None
            stack.pop()
            current.append(character)
        elif character == TOP_LEVEL_DELIMITER and not stack:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)

    if stack:
        # Something was opened and never closed.
        return None
    parts.append("".join(current))
    return parts


def parse_formula(ingredients_text: object) -> FormulaParse:
    """Read a printed ingredient list into ordered entries.

    Pure and deterministic: same string, same result. No I/O, no clock, no
    randomness, no database, no model, no network.

    The order of the checks matters. Length is tested against the raw string
    before anything else, so an oversized input is refused rather than walked;
    emptiness is tested after stripping, so a whitespace-only string is
    ``EMPTY`` rather than an entry containing nothing; and the token ceiling is
    tested after splitting but before any entry is examined, so an over-long
    list is refused whole rather than partly validated.
    """
    if not isinstance(ingredients_text, str):
        return _failed(ParseStatus.EMPTY)
    if len(ingredients_text) > MAX_INGREDIENTS_TEXT_LENGTH:
        # Refused, never truncated: cutting the string would drop ingredients
        # and the result would look like a complete formula.
        return _failed(ParseStatus.TOO_LONG)
    if not ingredients_text.strip():
        return _failed(ParseStatus.EMPTY)

    parts = _split_top_level(ingredients_text)
    if parts is None:
        return _failed(ParseStatus.MALFORMED)

    if len(parts) > MAX_FORMULA_TOKENS:
        # Refused whole. Resolving the first 128 would silently answer about a
        # different formula than the one printed.
        return _failed(ParseStatus.TOO_MANY_ITEMS)

    tokens: list[FormulaToken] = []
    for index, part in enumerate(parts, start=1):
        # ``str.strip()`` with no argument removes any Unicode whitespace from
        # both ends and touches nothing internal.
        raw_name = part.strip()
        if not raw_name:
            # An entry with no text. Dropping it would renumber every position
            # after it, so the whole list is malformed instead.
            return _failed(ParseStatus.MALFORMED)
        tokens.append(FormulaToken(position=index, raw_name=raw_name))

    return FormulaParse(status=ParseStatus.PARSED, tokens=tuple(tokens))


__all__ = [
    "MAX_FORMULA_TOKENS",
    "MAX_INGREDIENTS_TEXT_LENGTH",
    "TOP_LEVEL_DELIMITER",
    "FormulaParse",
    "FormulaToken",
    "ParseStatus",
    "parse_formula",
]
