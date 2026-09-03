"""What makes two products comparable, and what makes one of them sold here.

This module is deliberately thin. It states the comparison rule in this
domain's own words and then defers every reading of Open Food Facts data to
``app/domains/off/taxonomy.py``, which is where the source's field semantics
are documented and where the canonical values stored in Store A are computed.
One reader means the value a row was indexed under and the value a request
compares against cannot drift apart.

**The rule.** Two products are comparable when Open Food Facts publishes the
*same complete category classification* for both — their **non-lossy**
``categories_hierarchy`` arrays, compared as whole collections of the exact
source strings.

What that is not, and each of these was a real option that was rejected:

* Not the ``categories`` text field. Open Food Facts documents it as
  untaxonomised, written in whichever language the last person to edit the
  product happened to be using, and "mostly used for debugging and testing
  purposes". Reading a comparison out of it means two identical products edited
  by an English and a French contributor never match, while two unrelated
  products that happen to end on the same word do.
* Not the ``categories_tags`` array. That one is documented as lossy and "for
  search only": an unmatched entry is deaccented and lowercased, so two distinct
  source categories can collapse onto one token and manufacture a false match.
  ``categories_hierarchy`` keeps unmatched entries as-is, which is why it is the
  authority here.
* Not "the last tag", or any other single element. Nothing in the published
  schema says the array is ordered broadest-first, so calling the final entry
  the most specific one is an assumption dressed as a reading.
* Not a parent, and not a child. ``breakfast cereals`` is not ``cereal bars``,
  and a shared ancestor is never quietly treated as the same use case.
* Not AI, embeddings, fuzzy distance or a "close enough" mapping.
* Not a food taxonomy of our own, which somebody would have to maintain and
  which would silently lose whatever nobody remembered to add.

What a match means is narrow and worth saying out loud: *the source classifies
these as the same kind of product.* It does not mean the same nutrition, the
same ingredients, the same effect on anybody, or the same safety. Those are
different questions, and they are answered elsewhere.

Every value read here comes from Store A and stays in memory for the length of
one response. Nothing is written back — see ``docs/architecture/ODBL_DATA_WALL.md``.
"""
from __future__ import annotations

from app.domains.off.taxonomy import (
    INDIA_COUNTRY_TAG,
    canonical_hierarchy,
    category_fingerprint,
    listed_for_india,
    same_category,
)

#: The fixed-size discovery fingerprint for one product's category, or ``None``
#: when the source publishes no usable ``categories_hierarchy``. It narrows the
#: SQL scan and nothing more: two rows sharing it are re-compared exactly with
#: :func:`same_comparable_category` before either is graded, so a hash collision
#: can never manufacture a comparison. ``None`` is ineligible, never "matches
#: anything".
comparable_category_fingerprint = category_fingerprint

#: The exact, non-lossy classification of one product, or ``None``. This is the
#: authority the fingerprint stands in for, and the revalidation the discovery
#: loop runs against every candidate.
comparable_category = canonical_hierarchy

#: Does the source's own country taxonomy list this product for India? A claim
#: about a database row, never "in stock near you".
listed_for_india = listed_for_india  # noqa: PLW0127 — re-exported under this domain's name

#: True only when both rows publish a ``categories_hierarchy`` and it is exactly
#: equal. The backstop that makes the fingerprint safe to use as a mere key.
same_comparable_category = same_category

__all__ = [
    "INDIA_COUNTRY_TAG",
    "comparable_category",
    "comparable_category_fingerprint",
    "listed_for_india",
    "same_comparable_category",
]
