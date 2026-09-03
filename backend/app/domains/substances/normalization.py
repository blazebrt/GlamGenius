"""The one canonical name normalizer, and everything it deliberately will not do.

Its entire job is to remove **typographic** variation — the difference between
how two people typed the same string. It must never remove a **scientific**
distinction, because two different molecules that normalise to one key are
indistinguishable to everything downstream, and the resolver would then answer
RESOLVED with the wrong substance.

What it does, in this order and no other:

1. Unicode NFKC — so a compatibility form and its canonical form agree.
2. Strip leading and trailing whitespace.
3. Collapse runs of internal Unicode whitespace to a single space.
4. Casefold.

What it will never do, and why each one is a real temptation:

* **Strip punctuation.** Chemical names are punctuation. ``1,3-butanediol`` and
  ``1,4-butanediol`` differ by one character inside a hyphen-and-comma pattern;
  erasing punctuation merges them. Brackets, primes, commas and hyphens all
  carry meaning here.
* **Singularise or pluralise.** ``ceramides`` is a family; ``Ceramide NP`` is a
  molecule. A stemmer that maps one to the other asserts the exact
  member-of-family equivalence this milestone exists to refuse.
* **Stem or lemmatise.** Same reason, more aggressively.
* **Transliterate or de-accent.** Losing an accent is losing a character a
  source chose to print.
* **Drop hydrate, salt or form information.** ``sodium ascorbyl phosphate`` is
  not ``ascorbic acid``; ``retinal`` is not ``retinaldehyde``; anhydrous is not
  the monohydrate. These are different substances with different sources.
* **Expand or guess abbreviations.** ``THDA`` is not self-evidently anything.
* **Rewrite separators.** ``vitamin-e`` does not become ``vitamin e`` merely
  because that would produce a convenient match. If both spellings are genuinely
  the same identity, a reviewed claim records both names and both resolve — by
  evidence, not by string surgery.
* **Fuzzy-match, embed, or measure edit distance.** Nothing here is
  approximate, and no model is consulted.

If a name fails to resolve because of a spelling this normalizer preserved, the
answer is a reviewed identity claim carrying that spelling — never a looser
normalizer. Silence is recoverable; a wrong identity is not.
"""
from __future__ import annotations

import unicodedata

#: Longest input this layer will normalise. A label token is short; anything
#: past this is a paste of something else and is rejected rather than truncated,
#: because truncation would silently change the identity being asked about.
MAX_NAME_LENGTH = 200


def normalize_name(raw: object) -> str | None:
    """Normalise one candidate name, or ``None`` when it is not usable.

    ``None`` means "this is not a name I can compare", and every caller treats
    that as unresolvable rather than as a wildcard. A non-string, an empty
    string, a string that is only whitespace, or one longer than
    :data:`MAX_NAME_LENGTH` all yield ``None``.

    Pure and deterministic: same input, same output, no I/O, no clock, no
    randomness, no database, no model.
    """
    if not isinstance(raw, str):
        return None
    if len(raw) > MAX_NAME_LENGTH:
        return None
    # NFKC first, so compatibility forms fold before whitespace is measured.
    text = unicodedata.normalize("NFKC", raw)
    # ``str.split()`` with no argument splits on any Unicode whitespace run and
    # discards leading/trailing runs, which is exactly steps 2 and 3 together.
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    folded = collapsed.casefold()
    # Casefolding can, in principle, lengthen a string (ß -> ss). A key longer
    # than the column can hold would be truncated by the database, which is a
    # silent identity change, so it is refused here instead.
    if len(folded) > MAX_NAME_LENGTH:
        return None
    return folded


__all__ = ["MAX_NAME_LENGTH", "normalize_name"]
