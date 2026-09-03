"""Step 6A — one comparable alternative, decided by the rules that already exist.

This domain answers exactly one question: *is there a product in the same kind
of category that we can defensibly compare this one with?* It does not answer
"what is the best food", "what should this person eat", or "what is cheapest".

Four properties hold it together, and all four are the point:

* **It knows nothing about the person.** No profile, no history, no account. The
  same pack plus the same cached candidates plus the same ruleset produce the
  same answer for everybody, signed in or not.
* **It owns no data.** It reads Store A at query time, grades candidates with
  the grading engine, and writes nothing anywhere.
* **It invents no ranking.** There is no alternative score. A candidate is
  chosen by a strictly higher published grade, then by the canonical action,
  then by barcode — a lexicographic order, never a weighted average.
* **It asks no AI anything.** Category, availability, eligibility and selection
  are all deterministic.
"""
from app.domains.alternatives.category import (
    INDIA_COUNTRY_TAG,
    comparable_category,
    comparable_category_fingerprint,
    listed_for_india,
    same_comparable_category,
)
from app.domains.alternatives.policy import (
    ACTION_ORDER,
    CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY,
    CATEGORY_SOURCE,
    COMPARABLE_ALTERNATIVE_POLICY_VERSION,
    DISCOVERY_PAGE_SIZE,
    MAX_DISCOVERY_PAGES,
    MAX_DISCOVERY_ROWS,
    REASON_AVAILABLE,
    REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN,
    REASON_CURRENT_CATEGORY_STALE,
    REASON_CURRENT_CATEGORY_UNAVAILABLE,
    REASON_CURRENT_GRADE_UNAVAILABLE,
    REASON_NO_COMPARABLE_CANDIDATE,
    REASON_SEARCH_BUDGET_EXHAUSTED,
    SOURCE_KNOWN_BASES,
    STATUS_AVAILABLE,
    STATUS_NOT_ENOUGH_INFORMATION,
    Candidate,
    action_is_no_worse,
    basis_key,
    comparable_basis,
    published_grade,
    ranking_key,
    source_known_basis,
    strictly_better_grade,
)
from app.domains.alternatives.service import comparable_alternative_envelope

__all__ = [
    "ACTION_ORDER",
    "CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY",
    "CATEGORY_SOURCE",
    "COMPARABLE_ALTERNATIVE_POLICY_VERSION",
    "DISCOVERY_PAGE_SIZE",
    "INDIA_COUNTRY_TAG",
    "MAX_DISCOVERY_PAGES",
    "MAX_DISCOVERY_ROWS",
    "REASON_AVAILABLE",
    "REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN",
    "REASON_CURRENT_CATEGORY_STALE",
    "REASON_CURRENT_CATEGORY_UNAVAILABLE",
    "REASON_NO_COMPARABLE_CANDIDATE",
    "REASON_SEARCH_BUDGET_EXHAUSTED",
    "REASON_CURRENT_GRADE_UNAVAILABLE",
    "SOURCE_KNOWN_BASES",
    "STATUS_AVAILABLE",
    "STATUS_NOT_ENOUGH_INFORMATION",
    "Candidate",
    "action_is_no_worse",
    "basis_key",
    "comparable_category",
    "comparable_category_fingerprint",
    "comparable_alternative_envelope",
    "comparable_basis",
    "listed_for_india",
    "published_grade",
    "ranking_key",
    "same_comparable_category",
    "source_known_basis",
    "strictly_better_grade",
]
