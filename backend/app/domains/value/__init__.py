"""Step 6B — what recently confirmed pack labels stated as MRP.

One narrow factual question, asked only after Step 6A has already chosen an
alternative on scientific grounds: *what did these two packs declare as their
maximum retail price, per 100 g or per 100 ml?*

It is not a price. It is not a saving. It is not a value score, and there is no
number here that mixes a grade with rupees. The app has no evidence about what
any shop charges today, so nothing may be described as cheap, expensive, worth
it, or a better buy.

Four properties hold it together:

* **It runs last, and reads a decision it cannot change.** Step 6A's candidate
  is an input. There is no path from a price back into selection.
* **It owns no data.** Observations come from confirmed scan events that already
  exist; this milestone adds no table and writes nothing.
* **It asks no AI anything.** The model transcribes the printed MRP clause when
  somebody photographs a label. Parsing, normalising and comparing are
  deterministic code that fails closed.
* **It reaches no retailer.** No web call, no scraping, no affiliate, no
  commerce. The only evidence is a pack somebody confirmed.
"""
from app.domains.value.parsing import Quantity, parse_mrp_rupees, parse_quantity
from app.domains.value.policy import (
    MRP_OBSERVATION_MAX_AGE_DAYS,
    PACK_MRP_VALUE_POLICY_VERSION,
    RELATIONSHIP_HIGHER,
    RELATIONSHIP_LOWER,
    RELATIONSHIP_SAME,
    SOURCE_CONFIRMED_PACK_LABEL,
    STATUS_AVAILABLE,
    STATUS_NOT_ENOUGH_INFORMATION,
    observation_is_fresh,
)
from app.domains.value.service import (
    PackObservation,
    latest_confirmed_capture,
    pack_mrp_value_envelope,
)

__all__ = [
    "MRP_OBSERVATION_MAX_AGE_DAYS",
    "PACK_MRP_VALUE_POLICY_VERSION",
    "RELATIONSHIP_HIGHER",
    "RELATIONSHIP_LOWER",
    "RELATIONSHIP_SAME",
    "SOURCE_CONFIRMED_PACK_LABEL",
    "STATUS_AVAILABLE",
    "STATUS_NOT_ENOUGH_INFORMATION",
    "PackObservation",
    "Quantity",
    "latest_confirmed_capture",
    "observation_is_fresh",
    "pack_mrp_value_envelope",
    "parse_mrp_rupees",
    "parse_quantity",
]
