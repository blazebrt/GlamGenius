"""Comparing what two confirmed pack labels said their MRP was.

Runs strictly *after* Step 6A has already chosen zero or one alternative, and
reads that choice rather than participating in it. That ordering is the
invariant this whole domain rests on: money cannot promote a candidate, demote
one, break a tie or exclude a scientifically eligible product, because by the
time any of it is read the candidate is already decided and this module has no
way to ask for a different one.

If the chosen candidate has no usable MRP, the answer is that we do not have
enough information — never a search for a cheaper product that Step 6A did not
pick.

**Why the scan event and not the label snapshot.** Step 3 deduplicates equal
semantic label content: photograph the same formula twice and the second
capture legitimately reuses the first snapshot. If only the price changed, that
snapshot still carries the *old* MRP. The exact confirmed capture is therefore
the commercial authority, and the snapshot is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.product.models import ScanEvent
from app.domains.product.service import OUTCOME_LABEL
from app.domains.value.parsing import Quantity, parse_mrp_rupees, parse_quantity
from app.domains.value.policy import (
    PACK_MRP_VALUE_POLICY_VERSION,
    REASON_AVAILABLE,
    REASON_CANDIDATE_MRP_UNAVAILABLE,
    REASON_CANDIDATE_QUANTITY_UNAVAILABLE,
    REASON_CANDIDATE_STALE,
    REASON_CURRENT_MRP_UNAVAILABLE,
    REASON_CURRENT_QUANTITY_UNAVAILABLE,
    REASON_CURRENT_STALE,
    REASON_NO_COMPARABLE_ALTERNATIVE,
    REASON_QUANTITY_BASIS_INCOMPATIBLE,
    SOURCE_CONFIRMED_PACK_LABEL,
    STATUS_AVAILABLE,
    STATUS_NOT_ENOUGH_INFORMATION,
    difference,
    dimension_for_basis,
    money_string,
    mrp_per_100,
    observation_is_fresh,
    quantity_string,
    quantize_money,
    relationship,
)


@dataclass(frozen=True)
class PackObservation:
    """One pack, as one confirmed capture recorded it.

    The MRP and the quantity come from the *same* capture, always. Pairing a
    price from one photograph with a pack size from another would produce a
    number describing a pack that never existed.
    """

    barcode: str
    mrp_rupees: Decimal
    quantity: Quantity
    observed_at: datetime

    def per_100(self) -> Decimal | None:
        return mrp_per_100(self.mrp_rupees, self.quantity.base_amount)

    def per_100_shown(self) -> Decimal | None:
        """The per-100 figure as the card prints it: quantised to one paise.

        The only per-100 value anything public may use. The comparison is drawn
        from these, not from the exact quotients behind them, so the sentence a
        shopper reads always agrees with the two numbers beside it.
        """
        exact = self.per_100()
        return None if exact is None else quantize_money(exact)

    def as_payload(self) -> dict[str, Any]:
        """What a shopper is shown about one side of the comparison.

        Both the absolute pack facts and the normalised figure, because either
        alone misleads: a smaller pack with a smaller number on it is not
        cheaper per 100 g, and a per-100 figure with no pack size behind it is
        a number nobody can check against the shelf.
        """
        return {
            "barcode": self.barcode,
            "mrp_inr": money_string(self.mrp_rupees),
            "quantity": {
                "amount": quantity_string(self.quantity.base_amount),
                "unit": self.quantity.base_unit,
            },
            "mrp_per_100_inr": str(self.per_100_shown()),
            "observed_at": self.observed_at.isoformat(),
            # Never "price", never "current price". This is what a pack said.
            "source": SOURCE_CONFIRMED_PACK_LABEL,
        }


def _envelope(
    status: str, reason_key: str, comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "policy_version": PACK_MRP_VALUE_POLICY_VERSION,
        "status": status,
        "reason_key": reason_key,
        "comparison": comparison,
    }


def not_enough_information(reason_key: str) -> dict[str, Any]:
    """The honest empty answer.

    It says we have no recent confirmed pack observation to compare. It does not
    say a product is expensive, that no cheaper option exists, or that any
    market was searched — none of which we looked at.
    """
    return _envelope(STATUS_NOT_ENOUGH_INFORMATION, reason_key)


async def latest_confirmed_capture(session: AsyncSession, barcode: str) -> ScanEvent | None:
    """The newest confirmed label capture for one barcode, by server time.

    Ordered by ``created_at`` then ``id``, both written by the database. A
    phone's ``scanned_at`` is a client claim and is deliberately not consulted.

    Exactly one row, and no looking further back. If the newest capture could
    not read a price, the truthful statement is that we no longer have a usable
    observation — not that an older photograph once said something convenient.
    """
    return (await session.execute(
        select(ScanEvent)
        .where(
            ScanEvent.barcode == barcode,
            ScanEvent.outcome == OUTCOME_LABEL,
            ScanEvent.label_facts.is_not(None),
        )
        .order_by(ScanEvent.created_at.desc(), ScanEvent.id.desc())
        .limit(1)
    )).scalar_one_or_none()


def observation_from(event: ScanEvent | None) -> tuple[PackObservation | None, str | None]:
    """Turn one confirmed capture into an observation, or say what was missing.

    Returns ``(observation, missing)`` where ``missing`` is ``"mrp"`` or
    ``"quantity"``, so the caller can name which side of which product could not
    be read without this module knowing which side it is looking at.
    """
    if event is None:
        return None, "mrp"
    facts = event.label_facts
    if not isinstance(facts, dict):
        return None, "mrp"
    mrp = parse_mrp_rupees(facts.get("mrp_text"))
    if mrp is None:
        return None, "mrp"
    quantity = parse_quantity(facts.get("net_quantity"))
    if quantity is None:
        return None, "quantity"
    return PackObservation(
        barcode=event.barcode, mrp_rupees=mrp, quantity=quantity,
        observed_at=event.created_at,
    ), None


async def observe(session: AsyncSession, barcode: str) -> tuple[PackObservation | None, str | None]:
    """Read the newest confirmed capture for a barcode and interpret it."""
    return observation_from(await latest_confirmed_capture(session, barcode))


async def pack_mrp_value_envelope(
    session: AsyncSession,
    *,
    barcode: str,
    alternative: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The MRP comparison between the current pack and the chosen alternative.

    ``alternative`` is Step 6A's already-decided envelope, read and never
    influenced. The candidate barcode and the scientific basis both come from
    it, so the two products compared here are exactly the two products the
    science chose, on the basis the science established.

    ``now`` exists so a test can control the freshness clock; production passes
    nothing and the server's own time is used.
    """
    moment = now or datetime.now(UTC)

    candidate = (alternative or {}).get("candidate")
    if not isinstance(candidate, dict):
        # Step 6A found nothing to compare with. Money does not get to go
        # looking for a different product.
        return not_enough_information(REASON_NO_COMPARABLE_ALTERNATIVE)

    candidate_barcode = candidate.get("barcode")
    basis = (candidate.get("comparison") or {}).get("basis")
    dimension = dimension_for_basis(basis)
    if not isinstance(candidate_barcode, str) or dimension is None:
        return not_enough_information(REASON_QUANTITY_BASIS_INCOMPATIBLE)

    current, current_missing = await observe(session, barcode)
    if current is None:
        return not_enough_information(
            REASON_CURRENT_QUANTITY_UNAVAILABLE if current_missing == "quantity"
            else REASON_CURRENT_MRP_UNAVAILABLE
        )
    if not observation_is_fresh(current.observed_at, now=moment):
        return not_enough_information(REASON_CURRENT_STALE)

    other, candidate_missing = await observe(session, candidate_barcode)
    if other is None:
        return not_enough_information(
            REASON_CANDIDATE_QUANTITY_UNAVAILABLE if candidate_missing == "quantity"
            else REASON_CANDIDATE_MRP_UNAVAILABLE
        )
    if not observation_is_fresh(other.observed_at, now=moment):
        return not_enough_information(REASON_CANDIDATE_STALE)

    # The pack sizes have to be the physical dimension the nutrition panel was
    # measured in, on both sides. A per-100-g comparison against a bottle stated
    # in millilitres would need a density, and no pack prints one.
    if current.quantity.dimension != dimension or other.quantity.dimension != dimension:
        return not_enough_information(REASON_QUANTITY_BASIS_INCOMPATIBLE)

    # Quantise first, conclude second. Both figures are rounded to the paise the
    # card will print, and the relationship and the difference are then derived
    # from those printed figures. The other order produces a card that shows
    # ₹24.00 against ₹24.00 and calls one of them lower, which is indefensible
    # however true it is of the twelfth decimal place.
    current_per_100 = current.per_100_shown()
    other_per_100 = other.per_100_shown()
    if current_per_100 is None or other_per_100 is None:
        return not_enough_information(REASON_QUANTITY_BASIS_INCOMPATIBLE)

    return _envelope(STATUS_AVAILABLE, REASON_AVAILABLE, {
        "basis": basis,
        "current": current.as_payload(),
        "candidate": other.as_payload(),
        # Factual, closed vocabulary. Not a winner and not a recommendation.
        "relationship": relationship(other_per_100, current_per_100),
        # Candidate minus current, defined once in policy so no surface can
        # read the sign the other way round.
        "difference_inr_per_100": money_string(difference(other_per_100, current_per_100)),
    })


__all__ = [
    "PackObservation",
    "latest_confirmed_capture",
    "not_enough_information",
    "observation_from",
    "observe",
    "pack_mrp_value_envelope",
]
