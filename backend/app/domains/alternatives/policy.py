"""What makes one product a defensible alternative to another, and which one wins.

Every rule here is conservative on purpose. The free alternative is a single
public claim about two products, and the cheapest way to make it indefensible
is to relax one of these:

* **Strictly higher grade, or nothing.** A candidate with the same grade is not
  offered, however much cleaner its ingredient list reads, however much more
  protein it declares, and whatever anybody thinks of the brand. Same-grade
  optimisation needs an explicit factor-comparison policy, and there is not one
  yet.
* **The grade ladder is the existing one.** ``GRADE_ORDER`` from the grading
  rules, unchanged. There is no second ladder here, and ``NOT_GRADED`` and
  ``NOT_ENOUGH_INFORMATION`` are never ranked as though they were poor letters:
  they are different states, not bad scores.
* **The action may not contradict the card.** A product whose own Product
  Result says SKIP is not offered as the better option against a BUY, whatever
  its letter.
* **Selection is lexicographic.** Grade, then action, then barcode. No
  ``alternative_score``, no weighting, nothing that averages incompatible
  things — the Constitution rejects composite scores and one is not smuggled in
  here as a ranking key.

Money appears nowhere in this module. Price, MRP and value are Step 6B, and an
alternative chosen in this milestone is chosen independently of what it costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.nutrition.grading.engine import GradeResult
from app.domains.nutrition.grading.rules import GRADE_ORDER, Grade, GradeOutcome

#: Bumped when any rule in this module changes meaning, so an answer can always
#: be read back against the policy that produced it.
COMPARABLE_ALTERNATIVE_POLICY_VERSION = "comparable-food-alternative-v1"

#: The two states the envelope can be in. Deliberately two and not ten: every
#: further distinction we might draw is either private reasoning or a claim we
#: are not in a position to make.
STATUS_AVAILABLE = "available"
STATUS_NOT_ENOUGH_INFORMATION = "not_enough_information"

#: Why the envelope reads the way it does. A closed set, resolved to words by
#: the app's string file — no prose crosses this boundary.
REASON_AVAILABLE = "comparable_option_found"
REASON_CURRENT_GRADE_UNAVAILABLE = "current_product_has_no_published_grade"
REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN = "current_product_basis_not_source_known"
REASON_CURRENT_CATEGORY_UNAVAILABLE = "no_source_category_for_this_product"
REASON_CURRENT_CATEGORY_STALE = "source_category_copy_is_out_of_date"
REASON_NO_COMPARABLE_CANDIDATE = "no_comparable_candidate_in_cached_data"
#: Distinct from the one above, and the distinction is the honest part: we ran
#: out of the work one request may do before we ran out of products to consider.
#: "We did not find one" and "we stopped looking" are different statements, and
#: collapsing them would let a capacity limit be read as a fact about the
#: market. Both are still ``not_enough_information`` to a customer — the app
#: says the same careful sentence — but only one of them means the cached data
#: has been exhausted, and the difference is what tells us to raise the budget.
REASON_SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted_before_a_comparable_candidate"

#: What ``category_match`` in the response means, spelled out rather than
#: implied: Open Food Facts' own category taxonomy tags, in full, matched
#: exactly. Not a leaf, not a parent, not a guess at which tag is the most
#: specific one — the whole published classification, identical on both sides.
#: See ``app/domains/off/taxonomy.py`` for why that is the narrowest comparison
#: their documented field semantics support.
CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY = "exact_source_taxonomy"

#: Where the category came from. Named so no surface can imply GlamGenius
#: authored or certified a market category. We did not; we read theirs.
CATEGORY_SOURCE = "open_food_facts"

#: How many qualifying Store A rows one page of discovery reads.
#:
#: Every question Store A can answer — the canonical category, the canonical
#: India listing, the exclusion of the shopper's own barcode, and the age of
#: the copy — is already answered in SQL before this limit applies, so a page
#: is a page of rows that have *passed* those gates rather than a window that
#: they will later be filtered out of.
DISCOVERY_PAGE_SIZE = 50

#: How many such pages one request may read.
#:
#: This is the fix for a starvation the single-window design could not avoid.
#: A Store A row qualifies on category, country and freshness; whether it can
#: actually be offered depends on a confirmed label snapshot in Store B, which
#: Store A cannot see and which the licence wall forbids us from joining to. So
#: a window of fifty source-qualified rows that all happen to lack a usable
#: snapshot returned nothing, permanently, while a perfectly good candidate sat
#: at position fifty-one — and no amount of waiting or re-scanning would ever
#: reach it, because the window started in the same place every time.
#:
#: Paging past them costs one more bounded Store B read per page and turns a
#: permanent blind spot into a stated, raisable limit.
MAX_DISCOVERY_PAGES = 10

#: The total work ceiling for one request, stated in the unit that matters.
#: Reaching it is reported as its own reason so a capacity limit is never
#: mistaken for a fact about what the cached data contains.
MAX_DISCOVERY_ROWS = DISCOVERY_PAGE_SIZE * MAX_DISCOVERY_PAGES

#: The canonical purchase actions, worst last. Not a new vocabulary — the same
#: buy/wait/skip the verdict already speaks.
ACTION_ORDER: tuple[str, ...] = ("buy", "wait", "skip")

#: Which per-100 basis a grading basis corresponds to. Solids compare with
#: solids and drinks with drinks; millilitres are never converted to grams,
#: because that needs a density nobody printed on the pack.
_BASIS_KEYS: dict[str, str] = {"solid": "per_100g", "drink": "per_100ml"}

#: The only two things a confirmed label may say its panel was printed on.
#:
#: This is the gate that keeps ``comparison.basis`` a fact. The grading adapter
#: for catalogue data will happily decide "drink" because a category or a
#: product name contains the word *milk*, and that guess is fine for choosing
#: which threshold table to read — it is not fine as a published statement about
#: how two products were compared. A basis is source-known only when a person
#: read it off the pack and confirmed it.
SOURCE_KNOWN_BASES: frozenset[str] = frozenset({"per_100g", "per_100ml"})


def source_known_basis(nutrition_basis: object) -> bool:
    """Did somebody actually read this basis off a pack?

    Anything else — absent, blank, an unrecognised string, a non-string — is
    unknown, and unknown fails closed. It is never inferred from a product name,
    a category, a net quantity, a brand, a barcode or an ingredient list.
    """
    return isinstance(nutrition_basis, str) and nutrition_basis in SOURCE_KNOWN_BASES


@dataclass(frozen=True)
class Candidate:
    """One evaluated candidate, and everything selection is allowed to see.

    There is no score field and there is deliberately nowhere to put one.
    """

    barcode: str
    product_name: str | None
    brand: str | None
    grade: Grade
    action: str
    basis: str

    @property
    def rank(self) -> tuple[int, int, str]:
        """The lexicographic key. Grade, then action, then barcode."""
        return (GRADE_ORDER.index(self.grade), ACTION_ORDER.index(self.action), self.barcode)


def published_grade(result: GradeResult) -> Grade | None:
    """The letter this result may be compared on, or ``None``.

    Fail closed in every other case. ``NOT_GRADED`` (a cooking ingredient) and
    ``NOT_ENOUGH_INFORMATION`` (including a required rule that has not finished
    its evidence lifecycle) are states, not letters, and neither is ever placed
    on the ladder.
    """
    if result.outcome is not GradeOutcome.GRADED:
        return None
    if result.grade is None or result.grade not in GRADE_ORDER:
        return None
    return result.grade


def strictly_better_grade(candidate: Grade | None, current: Grade | None) -> bool:
    """Is the candidate's letter strictly higher than the current product's?

    Equal is not better. This is the whole of the V1 improvement test.
    """
    if candidate is None or current is None:
        return False
    if candidate not in GRADE_ORDER or current not in GRADE_ORDER:
        return False
    return GRADE_ORDER.index(candidate) < GRADE_ORDER.index(current)


def action_is_no_worse(candidate: str | None, current: str | None) -> bool:
    """Does the candidate's own canonical decision avoid contradicting the card?

    An alternative labelled "better option" whose own Product Result says SKIP,
    against a current product that says BUY, is a card arguing with itself. When
    either action is unknown the candidate is ineligible rather than assumed
    equivalent.
    """
    if candidate not in ACTION_ORDER or current not in ACTION_ORDER:
        return False
    return ACTION_ORDER.index(candidate) <= ACTION_ORDER.index(current)


def comparable_basis(candidate: str | None, current: str | None) -> bool:
    """Are both panels measured on the same basis?

    A source category string matching is not licence to compare a drink's per
    100 ml panel with a solid's per 100 g one. There is no conversion here and
    there will not be one: it would need a density assumption.
    """
    if candidate not in _BASIS_KEYS or current not in _BASIS_KEYS:
        return False
    return candidate == current


def basis_key(basis: str | None) -> str | None:
    """``solid`` -> ``per_100g``. The wire says which panel was compared."""
    return _BASIS_KEYS.get(basis or "")


def ranking_key(candidate: Candidate) -> tuple[int, int, str]:
    """Sort key for the eligible set. Deterministic, and never random.

    Barcode is the final tie-break because it is stable, immutable and the one
    identifier both stores already agree on. Two runs over the same cached data
    return the same product, whatever order the database hands the rows back in.
    """
    return candidate.rank


def is_unbeatable(candidate: Candidate) -> bool:
    """Can any row we have not read yet displace this one? ``True`` means no.

    Discovery walks Store A in ascending barcode order and barcode is the final
    tie-break, so a later row beats this one only by being *strictly* better on
    grade or action. A candidate already at the top of both ladders therefore
    cannot be displaced by anything further down the scan, and reading on would
    change nothing but the bill.

    Stated here, beside the ranking it depends on, so the two cannot drift: if
    the ranking key ever gains a term, this has to be reconsidered with it.
    """
    grade_rank, action_rank, _barcode = candidate.rank
    return grade_rank == 0 and action_rank == 0


def select(candidates: list[Candidate]) -> Candidate | None:
    """The one candidate the public contract is allowed to carry.

    Several may be evaluated; exactly zero or one is ever returned. A ranked
    list, a top three or a carousel is a different product feature reserved for
    the paid personalised layer.
    """
    if not candidates:
        return None
    return min(candidates, key=ranking_key)


def comparison_block(
    *, current_grade: Grade, candidate_grade: Grade, basis: str,
) -> dict[str, Any]:
    """What the two products were compared on, stated rather than implied."""
    return {
        "category_match": CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY,
        "category_source": CATEGORY_SOURCE,
        "current_grade": current_grade.value,
        "candidate_grade": candidate_grade.value,
        "basis": basis_key(basis),
    }


__all__ = [
    "ACTION_ORDER",
    "CATEGORY_MATCH_EXACT_SOURCE_TAXONOMY",
    "CATEGORY_SOURCE",
    "COMPARABLE_ALTERNATIVE_POLICY_VERSION",
    "DISCOVERY_PAGE_SIZE",
    "MAX_DISCOVERY_PAGES",
    "MAX_DISCOVERY_ROWS",
    "REASON_AVAILABLE",
    "REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN",
    "REASON_CURRENT_CATEGORY_STALE",
    "REASON_CURRENT_CATEGORY_UNAVAILABLE",
    "REASON_CURRENT_GRADE_UNAVAILABLE",
    "REASON_NO_COMPARABLE_CANDIDATE",
    "REASON_SEARCH_BUDGET_EXHAUSTED",
    "SOURCE_KNOWN_BASES",
    "STATUS_AVAILABLE",
    "STATUS_NOT_ENOUGH_INFORMATION",
    "Candidate",
    "action_is_no_worse",
    "basis_key",
    "comparable_basis",
    "comparison_block",
    "is_unbeatable",
    "published_grade",
    "ranking_key",
    "select",
    "source_known_basis",
    "strictly_better_grade",
]
