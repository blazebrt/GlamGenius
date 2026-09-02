"""Reading a comparable category, and a country, off the source taxonomy.

Two products are comparable in V1 when the **final** category token Open Food
Facts published for each, normalised the same conservative way, is the same
string. Nothing cleverer than that, deliberately:

* No AI, no embeddings, no fuzzy distance, no "close enough" mapping.
* No parent/child equivalence. ``breakfast cereals`` is not ``cereal bars``,
  and a parent category is never quietly treated as the same use case.
* No hand-written food taxonomy of our own, which would have to be maintained
  by somebody and would silently lose whatever nobody remembered to add.

What the match means is narrow and worth saying out loud: *the source lists
these as the same kind of product.* It does not mean the same nutrition, the
same ingredients, the same effect on anybody, or the same safety. Those are
different questions and they are answered elsewhere.

Every value read here comes from Store A and stays in memory for the length of
one response. Nothing is written back — see ``docs/architecture/ODBL_DATA_WALL.md``.
"""
from __future__ import annotations

import unicodedata

#: How Open Food Facts separates the category path in the ``categories`` text
#: field, and how it separates the country list in ``countries``. Both are
#: comma-separated source text; this is not a guess, it is the shape the cache
#: in ``app/domains/product/service.py`` writes and the fixtures carry.
SOURCE_SEPARATOR = ","

#: The exact tokens that mean India. A closed set of literal spellings, not a
#: pattern: ``en:india`` is Open Food Facts' own tag form of the same country,
#: not a fuzzy match. Anything else — a barcode prefix, a brand, a language, a
#: name that looks Indian — is not evidence and is never read as one.
INDIA_COUNTRY_TOKENS: frozenset[str] = frozenset({"india", "en:india"})


def _normalise_token(raw: str) -> str:
    """Trim, collapse repeated whitespace, casefold. In that order, always."""
    return " ".join(raw.split()).casefold()


def _tokens(source: str | None) -> tuple[str, ...]:
    """Split one source field into normalised, non-empty tokens.

    ``None``, a non-string, or a field that normalises to nothing all produce an
    empty tuple. Missing data stays missing; it is never filled in.
    """
    if not isinstance(source, str):
        return ()
    text = unicodedata.normalize("NFKC", source)
    return tuple(
        token for token in (_normalise_token(part) for part in text.split(SOURCE_SEPARATOR))
        if token
    )


def category_leaf(categories: str | None) -> str | None:
    """The final normalised category token, or ``None`` when there is not one.

    Open Food Facts writes the path broadest-first — ``"Foods, Breakfasts,
    Breakfast cereals"`` — so the last token is the most specific statement the
    source makes about what the product is. Taking any earlier token would
    broaden the comparison, which is exactly the mistake this refuses to make.
    """
    tokens = _tokens(categories)
    return tokens[-1] if tokens else None


def same_source_category(left: str | None, right: str | None) -> bool:
    """True only when both leaves exist and are the identical normalised string."""
    left_leaf = category_leaf(left)
    right_leaf = category_leaf(right)
    return left_leaf is not None and left_leaf == right_leaf


def country_tokens(countries: str | None) -> tuple[str, ...]:
    """Every country the source lists, normalised the same way as a category."""
    return _tokens(countries)


def listed_for_india(countries: str | None) -> bool:
    """Does the source itself say this product is sold in India?

    This is the whole claim, and it is a claim about a database row rather than
    about a shop. A missing country list means ineligible: absence is not
    availability, and inferring India from a barcode prefix, a brand, an
    FSSAI-looking name or somebody else's scan would be inventing the fact.

    It is never "in stock near you". No retailer is consulted in this milestone.
    """
    return any(token in INDIA_COUNTRY_TOKENS for token in country_tokens(countries))


#: Characters that mean something to SQL ``LIKE`` and must be neutralised
#: before a category word is interpolated into a pattern.
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    for character in (_LIKE_ESCAPE, "%", "_"):
        value = value.replace(character, _LIKE_ESCAPE + character)
    return value


def coarse_category_filter(leaf: str) -> str | None:
    """A bounded SQL ``ILIKE`` pattern that prunes the candidate scan.

    It matches on the single longest **word** of the leaf rather than the leaf
    itself, because the leaf has already had its internal whitespace collapsed
    and the stored source text may not have: a row reading
    ``"BREAKFAST   Cereals"`` is a genuine match that a pattern built from
    ``"breakfast cereals"`` would miss.

    This is a prune, never a decision. Every row it returns is re-tested with
    :func:`category_leaf` in Python before it can be accepted, so the filter can
    only ever cost recall — a product it fails to surface is simply not offered.
    That is the safe direction: this system's failure mode is silence.
    """
    words = [word for word in leaf.split(" ") if word]
    if not words:
        return None
    longest = max(words, key=len)
    return f"%{_escape_like(longest)}%"


__all__ = [
    "INDIA_COUNTRY_TOKENS",
    "SOURCE_SEPARATOR",
    "category_leaf",
    "coarse_category_filter",
    "country_tokens",
    "listed_for_india",
    "same_source_category",
]
