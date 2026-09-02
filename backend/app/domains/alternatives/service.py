"""Computing the one comparable alternative, at request time, from cached data.

The shape of this module is the licence boundary showing through. Store A is
read through its own session and its rows are turned into a graded verdict in
memory; Store B is not touched at all, because nothing about a comparable
alternative depends on who is asking. Nothing is written to either store, no
combined record is ever built, and the pairing is thrown away with the
response. See ``docs/architecture/ODBL_DATA_WALL.md``.

Three things this deliberately does not do:

* **No live discovery.** Candidates come from what Store A already holds. No
  Open Food Facts search endpoint is called, no category is crawled, and there
  is no request per candidate. If the cache has nothing suitable, the honest
  answer is that we do not have enough information — which is not the same
  claim as "nothing better exists", and must never be rendered as one.
* **No AI.** Nothing here reaches the gateway. Category, availability,
  eligibility and selection are deterministic, and the customer-facing words
  are keyed copy chosen by the app.
* **No second opinion on the science.** Candidates are graded by the same
  engine, against the same resolved production ruleset object as the product in
  the shopper's hand. Comparing a letter from one ruleset against a letter from
  another would make the comparison meaningless.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.alternatives.category import (
    category_leaf,
    coarse_category_filter,
    listed_for_india,
)
from app.domains.alternatives.policy import (
    COMPARABLE_ALTERNATIVE_POLICY_VERSION,
    MAX_DISCOVERY_CANDIDATES,
    REASON_AVAILABLE,
    REASON_CURRENT_CATEGORY_UNAVAILABLE,
    REASON_CURRENT_GRADE_UNAVAILABLE,
    REASON_NO_COMPARABLE_CANDIDATE,
    STATUS_AVAILABLE,
    STATUS_NOT_ENOUGH_INFORMATION,
    Candidate,
    action_is_no_worse,
    comparable_basis,
    comparison_block,
    published_grade,
    strictly_better_grade,
)
from app.domains.alternatives.policy import (
    select as select_candidate,
)
from app.domains.nutrition.grading import from_scan, grade_product, presentation
from app.domains.nutrition.grading.engine import GradeResult, ProductInput
from app.domains.nutrition.grading.production_rules import (
    ProductionRuleset,
    enforce_published_required_rules,
)
from app.domains.nutrition.grading.rules import Grade
from app.domains.off.attribution import attribution
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker


def _envelope(status: str, reason_key: str, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    """The additive Product Result block, in the one shape every path returns."""
    return {
        "policy_version": COMPARABLE_ALTERNATIVE_POLICY_VERSION,
        "status": status,
        "reason_key": reason_key,
        "candidate": candidate,
    }


def not_enough_information(reason_key: str) -> dict[str, Any]:
    """The honest empty answer.

    It says we cannot establish a comparable alternative from what we hold. It
    does not say this product is the best of its kind, that nothing better
    exists, or that a market search was performed — our cached candidate set is
    not the Indian market and no surface may imply that it is.
    """
    return _envelope(STATUS_NOT_ENOUGH_INFORMATION, reason_key)


async def _discover(session: AsyncSession, *, leaf: str, exclude_barcode: str) -> list[OffProduct]:
    """A bounded, deterministic read of Store A. Never a full-table scan.

    The SQL filter is coarse on purpose — it prunes, it does not decide. Every
    row it returns is re-tested with the exact category parser in Python before
    it can be accepted, so a loose pattern can cost recall and can never admit a
    product from a different category.

    Ordering by barcode makes the window itself deterministic: the same cached
    data yields the same candidates whatever order the database would otherwise
    have chosen, and whatever order the rows were inserted in.
    """
    pattern = coarse_category_filter(leaf)
    if pattern is None:
        return []
    statement = (
        select(OffProduct)
        .where(
            OffProduct.barcode != exclude_barcode,
            OffProduct.categories.is_not(None),
            OffProduct.countries.is_not(None),
            OffProduct.ingredients_text.is_not(None),
            OffProduct.nutriments.is_not(None),
            OffProduct.categories.ilike(pattern, escape="\\"),
        )
        .order_by(OffProduct.barcode.asc())
        .limit(MAX_DISCOVERY_CANDIDATES)
    )
    return list((await session.execute(statement)).scalars().all())


def _off_half(row: OffProduct) -> dict[str, Any]:
    """The Open Food Facts fields this evaluation needs, and no others.

    Kept as its own dictionary rather than merged with anything of ours: the two
    halves never share an object, here or anywhere else.
    """
    return {
        "product_name": row.product_name,
        "brands": row.brands,
        "ingredients_text": row.ingredients_text,
        "nutriments": row.nutriments,
        "categories": row.categories,
        "quantity": row.quantity,
        "countries": row.countries,
    }


def _evaluate(
    row: OffProduct,
    *,
    current_product: ProductInput,
    current_grade: Grade,
    current_action: str | None,
    ruleset: ProductionRuleset,
) -> Candidate | None:
    """Grade one candidate and decide whether it may be offered. Fails closed.

    Runs through exactly the paths the shopper's own product ran through: the
    same adapter, the same grader, the same required-evidence enforcement
    against the same ruleset object, and the same action ladder the verdict
    screen prints. There is no alternative-specific score and no stored grade is
    trusted — an old letter may have been produced by rules that have since
    changed meaning.
    """
    off_half = _off_half(row)
    name = (row.product_name or "").strip() or row.barcode
    product = from_scan.build(barcode=row.barcode, name=name, off_half=off_half)

    # Basis before grading: comparing a per 100 ml panel against a per 100 g one
    # would need a density nobody printed, and we do not assume one.
    if not comparable_basis(product.basis, current_product.basis):
        return None

    result = enforce_published_required_rules(grade_product(product), ruleset)
    grade = published_grade(result)
    if not strictly_better_grade(grade, current_grade):
        return None

    action = presentation.action_for(result)
    if not action_is_no_worse(action, current_action):
        return None

    return Candidate(
        barcode=row.barcode,
        product_name=row.product_name,
        brand=row.brands,
        grade=grade,
        action=action,
        basis=product.basis,
    )


def _candidate_payload(candidate: Candidate, *, current_grade: Grade) -> dict[str, Any]:
    """What the shopper is shown about the alternative, and nothing more.

    Absent from this deliberately: the size of the candidate pool, the SQL that
    found it, any ranking number, the raw category hierarchy, shopper
    observations, any reading of an official record, and every Store B
    identifier. None of those are facts about the product.
    """
    return {
        "barcode": candidate.barcode,
        "product_name": candidate.product_name,
        # Kept absent rather than invented when the source does not carry one.
        "brand": (candidate.brand or None),
        "grade": candidate.grade.value,
        "band": presentation.BAND_FOR_GRADE.get(candidate.grade.value),
        "decision": candidate.action,
        "comparison": comparison_block(
            current_grade=current_grade,
            candidate_grade=candidate.grade,
            basis=candidate.basis,
        ),
        # A licence condition, not a nicety. The candidate's identity and its
        # category come from Open Food Facts, so the notice travels with them
        # even though the card is ours.
        "attribution": attribution(),
    }


async def comparable_alternative_envelope(
    *,
    barcode: str,
    current_off_half: dict[str, Any] | None,
    current_product: ProductInput,
    current_result: GradeResult,
    ruleset: ProductionRuleset,
) -> dict[str, Any]:
    """At most one comparable alternative for the product in the shopper's hand.

    ``current_off_half`` is the Open Food Facts half of the join the verdict
    route already performed, reused rather than re-fetched — so this adds no
    network call and no second lookup. When the shopper's grade came from a
    confirmed label snapshot, that snapshot remains the whole basis of the
    grade; the Open Food Facts row is consulted only for the source's own
    category, at runtime, on barcode, and is never written back.

    Free and anonymous. There is no account, entitlement or profile in this
    path: the same pack and the same cached data produce the same alternative
    for a signed-in shopper and for a phone that has never registered.
    """
    current_grade = published_grade(current_result)
    if current_grade is None:
        # NOT_GRADED (a cooking ingredient) and NOT_ENOUGH_INFORMATION have no
        # letter to improve on. Offering a "better" oil or ghee here would be
        # manufacturing a comparison the science does not support.
        return not_enough_information(REASON_CURRENT_GRADE_UNAVAILABLE)

    leaf = category_leaf((current_off_half or {}).get("categories"))
    if leaf is None:
        # No source category, no comparison. It is never inferred from the
        # product name, the brand, the ingredients, the barcode or an image.
        return not_enough_information(REASON_CURRENT_CATEGORY_UNAVAILABLE)

    current_action = presentation.action_for(current_result)

    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = await _discover(session, leaf=leaf, exclude_barcode=barcode)

    eligible: list[Candidate] = []
    for row in rows:
        # Cheap gates first, in the order that discards the most for the least
        # work. A row rejected here is never assembled into a graded product.
        if row.barcode == barcode:
            continue
        if category_leaf(row.categories) != leaf:
            continue
        if not listed_for_india(row.countries):
            continue
        candidate = _evaluate(
            row,
            current_product=current_product,
            current_grade=current_grade,
            current_action=current_action,
            ruleset=ruleset,
        )
        if candidate is not None:
            eligible.append(candidate)

    chosen = select_candidate(eligible)
    if chosen is None:
        return not_enough_information(REASON_NO_COMPARABLE_CANDIDATE)
    return _envelope(
        STATUS_AVAILABLE,
        REASON_AVAILABLE,
        _candidate_payload(chosen, current_grade=current_grade),
    )


__all__ = ["comparable_alternative_envelope", "not_enough_information"]
