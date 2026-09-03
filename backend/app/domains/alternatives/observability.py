"""How often each gate closes, and nothing about who hit it.

The comparable alternative fails closed at a dozen points, and every one of
them returns the same careful sentence to the customer. That is right for the
customer and blind for us: "not enough information" covers a category nobody
has classified, a country list nobody has filled in, a cache nobody has
refreshed, a shelf of candidates nobody has ever photographed the label of, and
a work budget that ran out — and those have completely different fixes.

So each request records **which gate closed**, from a closed set, with counts.

What is deliberately not recorded, and must never be added
---------------------------------------------------------

No account id. No device id. No barcode. No product name, brand, batch number
or FSSAI licence. No raw categories or countries. No label facts, ingredients
or nutrition values. Not hashed, not truncated, not "just for debugging".

The reason is that this is a coverage measurement, not a usage trail. We want
to know that the category gate closes on four requests in ten; we do not want,
and must not build, a record of which products a person scanned. The first is
an engineering signal about our own cached data. The second is surveillance of
somebody's shopping, assembled out of a feature they were never asked about.

A counter is not allowed to change an answer
--------------------------------------------

Every function here returns ``None`` and swallows its own failures. The Product
Result must be byte-identical whether observability is configured, working,
broken or absent — a screen that changes because a log line failed is a screen
that cannot be reasoned about. This is measurement bolted to the outside of the
decision, never a participant in it.

It emits through the ordinary application logger, so it inherits the request-id
stamping and the redaction filters the rest of the service already has, and it
aggregates wherever those lines already go.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The one event name every line carries, so a log pipeline can select them.
DISCOVERY_EVENT = "alternative_discovery"

#: Every gate that can close, as a closed set. A code not in here is a
#: programming error rather than new information, and is refused rather than
#: logged: an open-ended reason field is how free text and then identifiers
#: eventually arrive in a metrics stream.
DISCOVERY_OUTCOMES: frozenset[str] = frozenset({
    # The shopper's own product could not enter a comparison at all.
    "current_grade_unavailable",
    "current_basis_not_source_known",
    "current_category_unavailable",
    "current_category_stale",
    # Discovery ran, and this is how it ended.
    "candidate_offered",
    "no_eligible_candidate",
    "search_budget_exhausted",
})


def record_discovery(
    outcome: str,
    *,
    rows_scanned: int = 0,
    pages_read: int = 0,
    snapshots_read: int = 0,
    candidates_evaluated: int = 0,
) -> None:
    """Record one discovery attempt. Counts only, and never an identifier.

    ``rows_scanned`` is Store A rows that passed every source gate;
    ``snapshots_read`` is how many of those had a label snapshot to look at;
    ``candidates_evaluated`` is how many were actually graded. Together they say
    where coverage is lost — a large gap between the first two is a cache with
    no confirmed labels behind it, which is a data problem rather than a code
    one, and is invisible without this.
    """
    try:
        if outcome not in DISCOVERY_OUTCOMES:
            raise ValueError(f"{outcome!r} is not a known discovery outcome")
        logger.info(
            "%s outcome=%s rows_scanned=%d pages_read=%d snapshots_read=%d candidates_evaluated=%d",
            DISCOVERY_EVENT, outcome, rows_scanned, pages_read, snapshots_read, candidates_evaluated,
        )
    except Exception:  # noqa: BLE001 — a counter must never change an answer
        logger.debug("%s_record_failed", DISCOVERY_EVENT, exc_info=False)


__all__ = ["DISCOVERY_EVENT", "DISCOVERY_OUTCOMES", "record_discovery"]
