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

**A comma inside a chemical locant is not a separator either.**
``1,3-Butanediol`` prints a comma that no bracket protects, and treating it as a
delimiter destroys the ingredient: the token never reaches the identity layer,
so no reviewed claim can rescue it, and the meaningless fragments it leaves
behind (``1``, ``3-Butanediol``) would begin resolving the moment anybody
published an identity under those names. :func:`_is_locant_comma` therefore
recognises the *punctuation shape* of a locant sequence, described in full
there. It reads structure, never chemistry: no dictionary, no database, no
Step 7A lookup, no guessing at what the name means.

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
    #: The punctuation admits *both* a locant continuation and an ingredient
    #: boundary, and V1 refuses to choose. A parsing failure, and deliberately
    #: not the same thing as ``ResolutionStatus.AMBIGUOUS``: that one answers
    #: "which entity does this established token denote", this one answers
    #: "where are the printed boundaries at all". Nothing is sent to the
    #: identity resolver, so the registry can never decide the question by
    #: whether one reading happens to resolve.
    AMBIGUOUS_BOUNDARY = "ambiguous_boundary"
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


#: Characters that may act as a locant in place of a number. Heteroatom
#: locants — ``N,N-Dimethyl…``, ``O,O-…``, ``S,S-…``, ``P,P-…`` — are the only
#: letters standard nomenclature uses this way, so the set stops there.
#:
#: ``C`` is absent because carbon positions are numbered, not lettered; adding
#: it would widen the grammar to a form nomenclature does not use, for no gain.
#: It is *not* what keeps ``CI 77491,CI 77492`` two ingredients — the trailing
#: hyphen clause does that, and still does with ``C`` added (verified). Keeping
#: this set minimal is defence in depth, not the defence itself.
_HETEROATOM_LOCANTS: frozenset[str] = frozenset("NOSP")

#: ASCII digits only, listed rather than using ``str.isdigit()`` so a Unicode
#: digit from some other script cannot widen the grammar unnoticed.
_ASCII_DIGITS: frozenset[str] = frozenset("0123456789")

#: Prime marks that may follow a locant: ``2,2'-…``, ``N,N'-…``. The typewriter
#: apostrophe and the typographic prime, and nothing else.
_PRIMES: frozenset[str] = frozenset("'\u2032")

#: What may sit immediately before a locant run. A locant starts a name or a
#: name fragment, so it follows the start of the entry, whitespace, an opening
#: bracket, a hyphen (``Benzene-1,2,4-…``) or the previous comma of the same
#: run. Notably **not** a letter or digit: that is what keeps the ``3`` in
#: ``Vitamin B3,2,6-Di-t-Butyl-4-Methylphenol`` from reading as a locant, so
#: that list stays two ingredients.
_LOCANT_LEAD: frozenset[str] = frozenset("([{-,")


def _locant_atom_backwards(text: str, end: int) -> int | None:
    """Read one locant atom leftwards, ending just before ``end``.

    Returns the atom's start index, or ``None`` when what precedes ``end`` is
    not an atom. An atom is a run of ASCII digits or a single heteroatom
    letter, either optionally followed by primes.
    """
    index = end
    while index > 0 and text[index - 1] in _PRIMES:
        index -= 1
    if index == 0:
        return None
    if text[index - 1] in _ASCII_DIGITS:
        while index > 0 and text[index - 1] in _ASCII_DIGITS:
            index -= 1
        return index
    if text[index - 1] in _HETEROATOM_LOCANTS:
        return index - 1
    return None


def _locant_atom_forwards(text: str, start: int) -> int | None:
    """Read one locant atom rightwards from ``start``.

    Returns the index just past it, or ``None``. Deliberately does not skip
    whitespace: a space inside a locant run means this is an ordinary list
    (``Aqua, 1, 2, Glycerin``), not a chemical name.
    """
    index = start
    if index >= len(text):
        return None
    if text[index] in _ASCII_DIGITS:
        while index < len(text) and text[index] in _ASCII_DIGITS:
            index += 1
    elif text[index] in _HETEROATOM_LOCANTS:
        index += 1
    else:
        return None
    while index < len(text) and text[index] in _PRIMES:
        index += 1
    return index


class CommaRole(StrEnum):
    """What a top-level comma is doing, as far as punctuation can establish.

    Three answers, because two were not enough. Forcing an undecidable comma
    into either ``LOCANT`` or ``DELIMITER`` hands the question to the identity
    registry: whichever reading happens to match a published name later becomes
    the "right" one, so growing the registry silently re-interprets old labels.
    ``AMBIGUOUS`` refuses instead.
    """

    LOCANT = "locant"
    DELIMITER = "delimiter"
    AMBIGUOUS = "ambiguous"


def classify_comma(text: str, position: int, *, entry_prefix: str) -> CommaRole:
    """Decide what the comma at ``position`` is doing, from punctuation alone.

    The locant shape, and it is the whole of it::

        locant_run  := atom ( ',' atom )+ '-' name_char
        atom        := ( digit+ | heteroatom ) prime*
        digit       := 0-9
        heteroatom  := N | O | S | P
        prime       := ' | ′
        name_char   := any alphanumeric

    A comma that does not sit inside that shape is a ``DELIMITER``. One that
    does is a ``LOCANT`` **only when the run's own start is accounted for**;
    otherwise it is ``AMBIGUOUS``.

    What accounts for a run's start, and why each case is safe:

    * **It begins the current entry**, leading whitespace aside. In
      ``Water, 1,3-Butanediol, Glycerin`` the entry under construction is just
      ``" 1"`` when the locant comma arrives, so there is no competing reading:
      an entry cannot be a delimiter for itself.
    * **It is attached by a hyphen**, as in ``Benzene-1,2,4-Tricarboxylic
      Acid``. Explicit chemical punctuation binds it to the name before it.
    * **It continues a run already accepted**, the second comma of ``1,1,1-``.

    What makes a run *unaccounted for* is ordinary internal whitespace with
    substantive text before it in the same entry::

        Acid Red 1,N-Methylpyrrolidone
                 ^ locant-shaped, but "Acid Red" is already here

    Read one way this is a colour index followed by a solvent; read the other it
    is a single name with an ``N`` locant. **Both are entirely plausible, and no
    amount of punctuation analysis separates them** — the only thing that could
    is a dictionary, which this parser must not have.

    So it returns ``AMBIGUOUS``, and the caller emits nothing at all.

    That is the invariant, and the earlier attempt got it wrong twice in
    opposite directions. Splitting such a comma manufactures fragments that a
    future canonical ``1`` would resolve; merging it manufactures a
    concatenation that a future canonical ``Acid Red 1,N-Methylpyrrolidone``
    would resolve. Either way the *registry* ends up deciding where the printed
    boundaries were, years after the label was written. Neither reading may be
    emitted: **when punctuation alone cannot defensibly place a boundary, no
    name is sent to identity resolution.**

    Lexical throughout — ``text``, an index and the entry text so far. No
    dictionary, no database, no Step 7A call, no network.
    """
    start = _locant_atom_backwards(text, position)
    if start is None:
        return CommaRole.DELIMITER

    # The forward shape decides whether this is locant-shaped at all. Checked
    # first, so a comma that simply separates two names is a plain DELIMITER
    # and never reported as ambiguous.
    index = position
    while True:
        after_atom = _locant_atom_forwards(text, index + 1)
        if after_atom is None:
            return CommaRole.DELIMITER
        index = after_atom
        if index < len(text) and text[index] == ",":
            continue
        break
    if not (index + 1 < len(text) and text[index] == "-" and text[index + 1].isalnum()):
        return CommaRole.DELIMITER

    # Locant-shaped. Now: is the run's start accounted for?
    #
    # ``entry_prefix`` is the entry as accumulated so far, up to but excluding
    # this comma, so the atom sits at its tail. Everything before that atom is
    # what has to justify the run.
    atom_length = position - start
    before_atom = entry_prefix[: len(entry_prefix) - atom_length] if atom_length else entry_prefix

    if not before_atom.strip():
        # The run starts the entry (leading whitespace only).
        return CommaRole.LOCANT

    preceding = before_atom[-1]
    if preceding in _LOCANT_LEAD:
        # Bound by a hyphen, or continuing a run already accepted.
        return CommaRole.LOCANT
    if preceding.isspace():
        # Substantive text, then a space, then a locant-shaped run. Undecidable.
        return CommaRole.AMBIGUOUS
    # Glued to the previous character — ``Vitamin B3,2,6-…``. The ``3`` is part
    # of ``B3``, not a free-standing locant, so the comma separates two names.
    return CommaRole.DELIMITER


def _split_top_level(text: str) -> tuple[ParseStatus, list[str]]:
    """Split on top-level commas, reporting how it went.

    One pass, tracking a stack of open groupings. A comma is a candidate
    separator only at depth zero, and :func:`classify_comma` then decides
    whether it separates anything.

    Three outcomes:

    * ``PARSED`` with the entries.
    * ``MALFORMED`` when grouping is unbalanced. A closer that does not match
      the innermost opener — and a closer with nothing open — fails immediately
      rather than at the end, so ``Water (Aqua], Glycerin`` fails on the ``]``
      instead of being read as one long entry.
    * ``AMBIGUOUS_BOUNDARY`` when any comma is undecidable. One is enough, and
      it stops the scan: an entry list containing a boundary nobody can place
      is not a partial answer, it is a wrong one.
    """
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []

    for index, character in enumerate(text):
        if character in _GROUPING_PAIRS:
            stack.append(_GROUPING_PAIRS[character])
            current.append(character)
        elif character in _CLOSERS:
            # A closer with nothing open, or the wrong closer for what is open.
            if not stack or stack[-1] != character:
                return ParseStatus.MALFORMED, []
            stack.pop()
            current.append(character)
        elif character == TOP_LEVEL_DELIMITER and not stack:
            role = classify_comma(text, index, entry_prefix="".join(current))
            if role is CommaRole.AMBIGUOUS:
                return ParseStatus.AMBIGUOUS_BOUNDARY, []
            if role is CommaRole.LOCANT:
                # Part of the name being read, not a boundary between two.
                # Kept verbatim: the comma is never stripped or rewritten.
                current.append(character)
            else:
                parts.append("".join(current))
                current = []
        else:
            current.append(character)

    if stack:
        # Something was opened and never closed.
        return ParseStatus.MALFORMED, []
    parts.append("".join(current))
    return ParseStatus.PARSED, parts


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

    status, parts = _split_top_level(ingredients_text)
    if status is not ParseStatus.PARSED:
        # MALFORMED or AMBIGUOUS_BOUNDARY. Either way, no entries at all: a
        # list whose boundaries are not established cannot be partly answered.
        return _failed(status)

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
    "CommaRole",
    "FormulaParse",
    "FormulaToken",
    "ParseStatus",
    "classify_comma",
    "parse_formula",
]
