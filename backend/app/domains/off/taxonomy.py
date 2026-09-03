"""Reading a canonical category and a canonical country off the source taxonomy.

This module exists because of a specific, documented mistake: treating Open
Food Facts' ``categories`` and ``countries`` **text** fields as though they
were taxonomy. They are not, and Open Food Facts says so themselves.

What their own API schema says, verbatim
----------------------------------------

``categories``::

    Comma separated list of categories (not taxonomized), in the last language
    used to edit it (recorded in categories_lc)
    This field is mostly used for debugging and testing purposes. Do not use it
    for display purposes.

``categories_tags``::

    An array of indexed categories tag entries (for search).
    That is the id of categories found in taxonomy +
    categories not found in taxonomy (with case / accents / spaces normalized).
    This is mostly used for search as the normalization of entries not in the
    taxonomy is lossy.

``categories_hierarchy``::

    An array of categories tag entries (for display and editing).
    That is the id of categories found in taxonomy +
    categories not found in taxonomy (as-is, with no normalization).
    This is the field that should be used for display purposes, as it is not
    lossy.

And a tag entry itself::

    a taxonomy entry id, in the form [2 letter language code]:[normalized
    canonical name] (e.g. "en:green-teas") -> for entries that could be matched
    to a taxonomy entry
    a string in a specific language, prefixed by the 2 letter language code,
    and normalized -> for entries that could not be matched

Sources, retrieved 2026-09-03 from the ``main`` branch of
``openfoodfacts/openfoodfacts-server``:

* ``docs/api/ref/schemas/product_base_tags.yaml`` — ``categories``,
  ``categories_tags``, ``countries``, ``countries_tags``
* ``docs/api/ref/schemas/product_tags.yaml`` — ``categories_hierarchy``,
  ``categories_lc``, ``countries_hierarchy``
* ``docs/api/ref/schemas/tags/taxonomy_tag_entry.yaml`` and
  ``tags/indexed_taxonomy_tag_entry.yaml`` — the shape of one entry
* ``taxonomies/countries.txt`` — the country taxonomy, where the India entry
  begins ``en: India, Bharat, Hindustan, IN, IND`` with ``country_code_2:en:
  IN``, giving the canonical id ``en:india``

Three consequences, and every rule below follows from them
-----------------------------------------------------------

**1. The text fields cannot be authority.** ``categories`` is whatever the last
editor typed, in whatever language they were editing in. Two identical products
edited by an English and a French contributor carry different text and would
never compare; two different products can share a final comma-separated token
and would compare wrongly. The field is documented as being for debugging.

**2. There is no documented "most specific" element.** Nothing in the schema
says ``categories_tags`` is ordered broadest-first, so picking the last entry
as the leaf is an assumption, not a reading. We therefore do not pick one
element at all: the key is the **whole set**, sorted, which is order-independent
by construction and is the narrowest comparison the documented semantics
support — two products match only when their entire published classification is
identical.

**3. Lossy normalisation can only ever make us stricter.** The lossiness in
``categories_tags`` applies to entries *not* found in the taxonomy. Because the
whole set must match exactly, such an entry has to be byte-identical on both
sides before it can contribute to a match. A lossy entry can cost us a match;
it cannot manufacture one.

``compared_to_category`` appears nowhere in the published product schema, so it
is not part of the documented contract and nothing here depends on it.

No AI, no embeddings, no fuzzy distance, no parent/child equivalence, and no
taxonomy of our own. Anything unavailable, unparseable or empty fails closed —
missing data stays missing and is never filled in from a neighbouring field.

Everything in this module reads Open Food Facts values and produces Open Food
Facts values. It imports nothing proprietary, by design and by test: the
canonical columns it feeds live in Store A, and a value derived from Store B
reaching them would build the combined database the ODbL wall exists to
prevent. See ``docs/architecture/ODBL_DATA_WALL.md``.
"""
from __future__ import annotations

import unicodedata
from typing import Any

#: Joins the sorted canonical tags into one comparable key. A tag containing
#: this character would make the key ambiguous, so such a tag voids the key
#: rather than being escaped — see :func:`category_key`.
KEY_SEPARATOR = "|"

#: The one canonical Open Food Facts country id that means India.
#:
#: Not a list of spellings. Their taxonomy already resolves "India", "Bharat",
#: "Hindustan", "IN", "IND" and every translation to this single id, so reading
#: it is reading their answer rather than re-deriving it badly. Adding raw
#: spellings here would be rebuilding a mapping they publish.
INDIA_COUNTRY_TAG = "en:india"


def normalise_tag(value: Any) -> str | None:
    """One taxonomy tag entry, normalised, or ``None`` when it is not one.

    NFKC, whitespace collapsed, casefolded — the same conservative treatment
    every source string in this codebase gets. A non-string, an empty string,
    or a string carrying :data:`KEY_SEPARATOR` is not a usable tag.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not text or KEY_SEPARATOR in text:
        return None
    return text


def canonical_tags(tags: Any) -> tuple[str, ...] | None:
    """A ``*_tags`` array as a sorted, de-duplicated tuple, or ``None``.

    ``None`` means "this row cannot answer", and every caller treats that as
    ineligible rather than as an empty answer. The distinction matters: an
    empty tuple would compare equal to another empty tuple and would quietly
    make every unclassified product comparable with every other one.

    Fails closed on anything that is not a non-empty list of usable strings.
    One bad entry voids the whole array — a partially-read classification is
    not a classification, and dropping the entry we could not read would widen
    the set of products it matches.
    """
    if not isinstance(tags, (list, tuple)) or not tags:
        return None
    normalised: set[str] = set()
    for entry in tags:
        tag = normalise_tag(entry)
        if tag is None:
            return None
        normalised.add(tag)
    if not normalised:
        return None
    return tuple(sorted(normalised))


def category_key(categories_tags: Any) -> str | None:
    """The canonical comparison key for one product's category, or ``None``.

    The whole published classification, sorted and joined. Deterministic for a
    given row, independent of the order Open Food Facts happened to return the
    array in, and independent of whichever language the last editor used.

    It is not a category *name* and must never be shown to anybody: it is an
    opaque equality key, and the words a customer reads come from the keyed
    string file.
    """
    tags = canonical_tags(categories_tags)
    if tags is None:
        return None
    return KEY_SEPARATOR.join(tags)


def listed_for_india(countries_tags: Any) -> bool:
    """Does the source's own country taxonomy list this product for India?

    A claim about a database row, not about a shop, and never "in stock near
    you". A missing or unreadable country array means ineligible: absence is
    not availability, and India is never inferred from a barcode prefix, a
    brand, an FSSAI-looking name, a language or somebody else's scan.
    """
    tags = canonical_tags(countries_tags)
    if tags is None:
        return False
    return INDIA_COUNTRY_TAG in tags


def same_category(left: Any, right: Any) -> bool:
    """True only when both rows publish a classification and it is identical."""
    left_key = category_key(left)
    return left_key is not None and left_key == category_key(right)


__all__ = [
    "INDIA_COUNTRY_TAG",
    "KEY_SEPARATOR",
    "canonical_tags",
    "category_key",
    "listed_for_india",
    "normalise_tag",
    "same_category",
]
