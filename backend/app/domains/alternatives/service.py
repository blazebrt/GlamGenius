"""Computing the one comparable alternative, at request time, from canonical facts.

The shape of this module is two boundaries showing through at once.

**The licence boundary.** Store A is read through its own session; its rows are
paired with facts of ours in memory and the pair is thrown away with the
response. Nothing is written to either store and no combined record is ever
built. See ``docs/architecture/ODBL_DATA_WALL.md``.

**The truth boundary.** Each store answers only what it is entitled to answer:

* Store A says which products *might* be comparable — the category the source
  publishes, the countries it lists, and how recently we copied the row.
* Store B's confirmed label snapshot says what a candidate actually *is* — its
  name, its ingredients, its panel, and crucially the basis that panel was
  printed on.

The second half is the correction that matters. Open Food Facts does not tell
us whether a panel was printed per 100 g or per 100 ml; inferring it from words
in a product name would make ``comparison.basis`` a guess dressed as a fact. A
confirmed snapshot states it, so a candidate without one is not offered at all.

Three things this deliberately does not do:

* **No live discovery.** Candidates come from what Store A already holds. No
  search endpoint, no crawl, no request per candidate. An empty result means we
  cannot establish a comparable alternative — never that none exists.
* **No AI.** Category, availability, freshness, eligibility and selection are
  all deterministic, and the customer-facing words are keyed copy.
* **No second opinion on the science.** Candidates are graded by the same
  engine, against the same resolved production ruleset object, from the same
  canonical facts their own Product Result would use.
"""
from __future__ import annotations

from datetime import UTC, datetime
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
    REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN,
    REASON_CURRENT_CATEGORY_STALE,
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
    source_known_basis,
    strictly_better_grade,
)
from app.domains.alternatives.policy import select as select_candidate
from app.domains.nutrition.grading import from_scan, grade_product, presentation
from app.domains.nutrition.grading.engine import GradeResult, ProductInput
from app.domains.nutrition.grading.production_rules import (
    ProductionRuleset,
    enforce_published_required_rules,
)
from app.domains.nutrition.grading.rules import Grade
from app.domains.off import freshness as off_freshness
from app.domains.off.attribution import attribution
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.product import service as product_service
from app.domains.product.models import LabelSnapshot

#: The only completeness a candidate may be graded from. A snapshot marked
#: anything else was judged insufficient by the same rule the Product Result
#: uses, and re-judging it here would be a second opinion.
COMPLETE_FOR_GRADING = "complete_for_grading"


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


async def _current_source_row(session: AsyncSession, barcode: str) -> OffProduct | None:
    """The current product's own Store A row, read for its category and its age.

    Read here rather than taken from the lookup the route already performed,
    because this needs ``fetched_at`` alongside the category. One row, one
    primary-key read, and no network: the lookup has already had whatever
    refresh attempt it was entitled to.
    """
    return await session.get(OffProduct, barcode)


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
            OffProduct.fetched_at.is_not(None),
            OffProduct.categories.ilike(pattern, escape="\\"),
        )
        .order_by(OffProduct.barcode.asc())
        .limit(MAX_DISCOVERY_CANDIDATES)
    )
    return list((await session.execute(statement)).scalars().all())


def _comparable_source_rows(
    rows: list[OffProduct], *, leaf: str, exclude_barcode: str, now: datetime,
) -> list[OffProduct]:
    """The cheap gates, in the order that discards the most for the least work.

    A row rejected here is never assembled into a graded product and never costs
    a Store B read. Each gate is a question Store A alone is entitled to answer:
    is this the same kind of product, does the source list it for India, and is
    our copy of that answer recent enough to repeat.
    """
    kept: list[OffProduct] = []
    for row in rows:
        if row.barcode == exclude_barcode:
            continue
        if category_leaf(row.categories) != leaf:
            continue
        if not listed_for_india(row.countries):
            continue
        # An expired copy is not refreshed — no fan-out, ever. It simply does
        # not qualify to support a comparative claim during this request.
        if not off_freshness.is_fresh(row.fetched_at, now=now):
            continue
        kept.append(row)
    return kept


def _gradeable_facts(snapshot: LabelSnapshot | None) -> dict[str, Any] | None:
    """The confirmed facts a candidate may be graded from, or ``None``.

    Fails closed at every step. The snapshot must be the latest one for that
    barcode (the caller's batch read guarantees that), it must have been judged
    complete for grading by the same rule the Product Result uses, and it must
    state its panel basis explicitly.

    That last condition is the whole point of reading Store B here. Open Food
    Facts does not publish whether a panel is per 100 g or per 100 ml, and the
    adapter that guesses it from words in a category or a product name is an
    inference, not a fact. A comparison that reports its basis has to know it.
    """
    if snapshot is None or snapshot.completeness != COMPLETE_FOR_GRADING:
        return None
    facts = snapshot.facts
    if not isinstance(facts, dict) or not facts:
        return None
    if not source_known_basis(facts.get("nutrition_basis")):
        return None
    return facts


def _evaluate(
    *,
    barcode: str,
    facts: dict[str, Any],
    current_product: ProductInput,
    current_grade: Grade,
    current_action: str | None,
    ruleset: ProductionRuleset,
) -> Candidate | None:
    """Grade one candidate from its confirmed label and decide whether to offer it.

    Runs the paths the shopper's own product ran: the confirmed-label adapter,
    the same grader, the same required-evidence enforcement against the same
    ruleset object, the same action ladder the verdict screen prints, and the
    same identity helper the Product Result publishes. There is no
    alternative-specific score and no stored grade is trusted — an old letter
    may have been produced by rules that have since changed meaning.
    """
    product_name, brand = product_service.result_identity(barcode, facts)
    # A recommendation has to name something. The barcode is an identifier, not
    # a name, and "Better option: 8901000000002" is not a suggestion anybody can
    # act on — so a candidate whose canonical facts carry no name is not offered.
    if not (facts.get("product_name") or "").strip():
        return None

    product = from_scan.build_confirmed_label(barcode=barcode, facts=facts)

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
        barcode=barcode,
        product_name=product_name,
        brand=brand,
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
        # Kept absent rather than invented when the pack does not carry one.
        "brand": (candidate.brand or None),
        "grade": candidate.grade.value,
        "band": presentation.BAND_FOR_GRADE.get(candidate.grade.value),
        "decision": candidate.action,
        "comparison": comparison_block(
            current_grade=current_grade,
            candidate_grade=candidate.grade,
            basis=candidate.basis,
        ),
        # A licence condition, not a nicety. The candidate reached this card
        # through Open Food Facts' category and country listing, so the notice
        # travels with it even though the science beside it is ours.
        "attribution": attribution(),
    }


async def comparable_alternative_envelope(
    session: AsyncSession,
    *,
    barcode: str,
    current_snapshot: LabelSnapshot | None,
    current_product: ProductInput,
    current_result: GradeResult,
    ruleset: ProductionRuleset,
    now: datetime | None = None,
) -> dict[str, Any]:
    """At most one comparable alternative for the product in the shopper's hand.

    ``session`` is Store B, used only to batch-read candidate label snapshots.
    Store A is opened separately, read, and closed. ``now`` exists so a test can
    control the freshness clock; production passes nothing and the server's own
    time is used, never a client's.

    Free and anonymous. There is no account, entitlement or profile in this
    path: the same pack and the same cached data produce the same alternative
    for a signed-in shopper and for a phone that has never registered.
    """
    moment = now or datetime.now(UTC)

    current_grade = published_grade(current_result)
    if current_grade is None:
        # NOT_GRADED (a cooking ingredient) and NOT_ENOUGH_INFORMATION have no
        # letter to improve on. Offering a "better" oil or ghee here would be
        # manufacturing a comparison the science does not support.
        return not_enough_information(REASON_CURRENT_GRADE_UNAVAILABLE)

    # Both halves of a stated comparison have to be measured on a basis somebody
    # printed. The shopper's own pack qualifies only through a confirmed label;
    # a basis inferred from a product name cannot be reported as a fact about
    # what was compared, on either side.
    current_facts = (current_snapshot.facts if current_snapshot is not None else None) or {}
    if not source_known_basis(current_facts.get("nutrition_basis")):
        return not_enough_information(REASON_CURRENT_BASIS_NOT_SOURCE_KNOWN)

    factory = get_off_sessionmaker()
    async with factory() as off_session:
        source_row = await _current_source_row(off_session, barcode)
        leaf = category_leaf(source_row.categories if source_row is not None else None)
        if leaf is None:
            # No source category, no comparison. It is never inferred from the
            # product name, the brand, the ingredients, the barcode or an image.
            return not_enough_information(REASON_CURRENT_CATEGORY_UNAVAILABLE)
        if not off_freshness.is_fresh(source_row.fetched_at, now=moment):
            # The ordinary Product Result may go on showing this product from an
            # expired copy — a stale answer beats a blank screen. A fresh
            # comparative claim about two products is a different act, and an
            # expired copy of somebody else's category is not enough to make it.
            return not_enough_information(REASON_CURRENT_CATEGORY_STALE)
        source_rows = _comparable_source_rows(
            await _discover(off_session, leaf=leaf, exclude_barcode=barcode),
            leaf=leaf, exclude_barcode=barcode, now=moment,
        )

    if not source_rows:
        return not_enough_information(REASON_NO_COMPARABLE_CANDIDATE)

    current_action = presentation.action_for(current_result)

    # One Store B read for the whole bounded window. A query per candidate would
    # turn a single Product Result into fifty round trips.
    snapshots = await product_service.latest_label_snapshots(
        session, [row.barcode for row in source_rows],
    )

    eligible: list[Candidate] = []
    for row in source_rows:
        facts = _gradeable_facts(snapshots.get(row.barcode))
        if facts is None:
            continue
        candidate = _evaluate(
            barcode=row.barcode,
            facts=facts,
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


__all__ = ["COMPLETE_FOR_GRADING", "comparable_alternative_envelope", "not_enough_information"]
