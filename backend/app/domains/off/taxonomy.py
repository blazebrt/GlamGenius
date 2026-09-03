"""Reading a category identity and a country off the source taxonomy — correctly.

This module carries the scars of two mistakes, each corrected in turn, because
the second was hidden underneath the first.

**Mistake one: the ``categories`` text field.** Open Food Facts documents it as
untaxonomised prose in the last editor's language, "mostly used for debugging
and testing purposes". It cannot be a comparison authority.

**Mistake two: the ``categories_tags`` array.** The obvious correction — and the
one this module first shipped — was to compare the taxonomy tags instead. But
``categories_tags`` is the *indexed* representation, and Open Food Facts is
explicit that it is **lossy, for search only**: an entry that matched no
taxonomy entry is deaccented and lowercased before it is stored there. Two
genuinely different source categories can therefore collapse onto the *same*
indexed token, so comparing ``categories_tags`` can **manufacture** an equality
that does not exist. The earlier claim that "a lossy entry can only cost a
match, never manufacture one" was simply false, and a wrong comparison is worse
than a missed one.

What their own API schema says, verbatim
----------------------------------------

``categories_tags`` — ``indexed_taxonomy_tag_entry``::

    This field is used for search only. It is a lossy representation of the
    taxonomy tag entry ... for entries that could not be matched to a taxonomy
    entry: a string in a specific language, prefixed by the language code, and
    normalized (deaccented and lowercased, depending on language).

``categories_hierarchy`` — ``taxonomy_tag_entry``::

    An array of categories tag entries (for display and editing). That is the id
    of categories found in taxonomy + categories not found in taxonomy (as-is,
    with no normalization). This is the field that should be used for display
    purposes, as it is not lossy.

One entry (``taxonomy_tag_entry``)::

    a taxonomy entry id, in the form [2 letter language code]:[normalized
    canonical name] (e.g. "en:green-teas")  -> matched
    a string in a specific language, prefixed by the 2 letter language code
    (e.g. "fr:Thés verts")                  -> unmatched, kept as-is

``compared_to_category`` **exists** (this is the second correction to the record):
``docs/api/ref/schemas/product_extended.yaml`` defines it as *"the category to
use for comparison. **TODO** explain how it is chosen."* Because its own schema
carries a TODO for how it is generated — and the server assigns it from the
lossy ``categories_tags`` — it is not defensible as our comparison authority. It
was investigated and deliberately not used.

Sources, retrieved 2026-09-03 from the ``main`` branch of
``openfoodfacts/openfoodfacts-server``:

* ``docs/api/ref/schemas/product_tags.yaml`` — ``categories_hierarchy``
* ``docs/api/ref/schemas/product_base_tags.yaml`` — ``categories``,
  ``categories_tags``, ``countries``, ``countries_tags``
* ``docs/api/ref/schemas/tags/taxonomy_tag_entry.yaml`` — the non-lossy entry
* ``docs/api/ref/schemas/tags/indexed_taxonomy_tag_entry.yaml`` — the lossy one
* ``docs/api/ref/schemas/product_extended.yaml`` — ``compared_to_category``
* ``taxonomies/countries.txt`` — the India entry begins ``en: India, Bharat,
  Hindustan, IN, IND`` with ``country_code_2:en: IN``, giving the id ``en:india``

The rule
--------

Two products are comparable when their **complete ``categories_hierarchy``**,
compared as an order-independent collection of the *exact* source strings, is
equal. Not the last element, not a leaf, not a parent, not a fuzzy match, not a
taxonomy of our own.

**The source strings are preserved, not re-normalised.** Open Food Facts went to
the trouble of keeping unmatched entries as-is in this field; re-applying our own
casefold/deaccent here would just rebuild the lossy representation we are trying
to escape, and could re-introduce the very collision this module exists to
avoid. So we validate the shape, keep the string, and sort. Duplicates are
preserved — nothing Open Food Facts documents says a repeat is meaningless, and
inventing that guarantee could collapse two classifications into one.

Every failure fails closed. A missing, empty or malformed hierarchy yields
``None``, and every caller treats ``None`` as ineligible rather than as an empty
answer that matches another empty answer. **False negatives are acceptable;
manufactured equality is not.**

Everything here reads Open Food Facts values and produces Open Food Facts
values. It imports nothing proprietary, by design and by test: the derived
columns it feeds live in Store A, and a value derived from Store B reaching them
would build the combined database the ODbL wall exists to prevent. See
``docs/architecture/ODBL_DATA_WALL.md``.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

#: The one canonical Open Food Facts country id that means India.
#:
#: Not a list of spellings. Their taxonomy already resolves "India", "Bharat",
#: "Hindustan", "IN", "IND" and every translation to this single id, so reading
#: it is reading their answer rather than re-deriving it badly.
INDIA_COUNTRY_TAG = "en:india"

#: The documented shape of one tag entry: a language code, a colon, then a
#: non-empty remainder. Used only to reject a value that is clearly not a tag
#: entry — never to transform one. A matched id (``en:green-teas``) and an
#: unmatched as-is string (``fr:Thés verts``) both satisfy it.
_TAG_SHAPE = re.compile(r"^[a-z]{2,3}:.+$", re.IGNORECASE | re.DOTALL)


def canonical_hierarchy(hierarchy: Any) -> tuple[str, ...] | None:
    """A ``categories_hierarchy`` array as a sorted tuple of its exact strings.

    ``None`` means "this row cannot answer", and every caller treats that as
    ineligible. The distinction from an empty tuple matters: two empty tuples
    compare equal, which would quietly make every unclassified product
    comparable with every other one.

    The transformation is deliberately minimal — this is the whole point of
    reading the non-lossy field:

    * A non-list, or an empty list, is unavailable.
    * Every entry must be a string of the documented ``lang:value`` shape with a
      non-blank value. One malformed entry voids the **whole** classification;
      dropping it would silently widen the set of products this row matches, and
      a partially-read classification is not a classification.
    * The source string is kept exactly as published. It is never casefolded,
      deaccented, NFKC-folded or whitespace-collapsed — doing any of those would
      rebuild the lossy ``categories_tags`` form and could collapse two distinct
      categories into one.
    * Duplicates are preserved (sorted list, not set). Nothing Open Food Facts
      documents says a repeated entry is meaningless, and de-duplicating could
      make two different classifications look identical.
    """
    if not isinstance(hierarchy, (list, tuple)) or not hierarchy:
        return None
    kept: list[str] = []
    for entry in hierarchy:
        if not isinstance(entry, str):
            return None
        # A value that is only a language prefix, or only whitespace after it,
        # is malformed. Note we test the shape but store the untouched string.
        if not entry.strip() or _TAG_SHAPE.match(entry) is None:
            return None
        _lang, _colon, value = entry.partition(":")
        if not value.strip():
            return None
        kept.append(entry)
    return tuple(sorted(kept))


def category_fingerprint(hierarchy: Any) -> str | None:
    """A fixed-size, collision-defended SQL discovery key, or ``None``.

    The canonical hierarchy — the exact source strings, sorted, duplicates kept
    — serialised as canonical JSON and hashed with SHA-256 to a 64-character hex
    digest. Fixed size, so it can be a B-tree key without an unbounded joined
    string; deterministic, so the same classification always fingerprints the
    same way.

    A fingerprint is **only** a discovery key. It narrows the candidate scan in
    SQL; it never establishes a match. Before any candidate is graded, its
    stored hierarchy is re-compared against the current product's with
    :func:`same_category`, so a hash collision, a corrupted key, or an
    inconsistent Store-A row cannot manufacture a comparison. See the revalidate
    step in ``app/domains/alternatives/service.py``.

    The digest is derived from Open Food Facts data alone, so it is Store A data
    in another shape and is exported with the rest of Store A.
    """
    canonical = canonical_hierarchy(hierarchy)
    if canonical is None:
        return None
    material = json.dumps(list(canonical), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def same_category(left: Any, right: Any) -> bool:
    """True only when both rows publish a hierarchy and it is *exactly* equal.

    This is the authority, and the fingerprint's backstop. It compares the exact
    source strings, so it never depends on the digest and cannot be fooled by a
    collision in it.
    """
    left_canonical = canonical_hierarchy(left)
    return left_canonical is not None and left_canonical == canonical_hierarchy(right)


def listed_for_india(countries_tags: Any) -> bool:
    """Does the source's own country taxonomy list this product for India?

    An exact membership test on ``countries_tags``: the canonical id
    ``en:india`` must be present. Country entries are canonical taxonomy ids, so
    no normalisation is applied and none is needed — a raw ``countries`` spelling
    is never consulted and no translation map is kept.

    A claim about a database row, not about a shop, and never "in stock near
    you". A missing or unreadable array means ineligible: absence is not
    availability, and India is never inferred from a barcode prefix, a brand, an
    FSSAI-looking name or somebody else's scan.
    """
    if not isinstance(countries_tags, (list, tuple)) or not countries_tags:
        return False
    return any(entry == INDIA_COUNTRY_TAG for entry in countries_tags)


__all__ = [
    "INDIA_COUNTRY_TAG",
    "canonical_hierarchy",
    "category_fingerprint",
    "listed_for_india",
    "same_category",
]
