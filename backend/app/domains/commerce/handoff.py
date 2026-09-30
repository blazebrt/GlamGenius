"""``commerce-handoff-v1`` — the one authority for whether, and to what, a link may go.

It reads a finished Purchase Operating System answer for a scanned pack and
returns one outbound search address for one exact barcode, or nothing. It
decides nothing about the product. It never re-grades, re-matches, re-selects
or re-orders, and nothing it knows (partner, affiliate tag, payout) exists in
any authority it reads.

The matrix
----------
A target exists only when every condition holds:

* the answer is the Step 14 scan contract (``step-14-v1``, kind ``scan``,
  strategy ``scan_product``) with no boundary;
* it is **not** a reference view, the identity is **exact**, and the
  Product Result proved the pack is in this device's hands. Commerce reads only
  a decision the official-record ceiling has had its chance to govern;
* the decision state is ``decided``.

Then:

====================  ==========================================================
Verdict               Target
====================  ==========================================================
``buy``               the current product, if its barcode is an exact GTIN.
``wait`` / ``skip``   the one comparable alternative the Product Result already
                      carries — status ``available``, its own canonical
                      decision ``buy``, an exact GTIN that is not the scanned
                      one. Otherwise nothing. Never the current product.
====================  ==========================================================

Anything else — ``not_enough_information``, ``prohibited``, ``unsupported``,
an insufficient identity, a missing partner, an unusable barcode, an address
that fails the registry check — gets no link. No link is better than a link
that reads as a recommendation the engine did not make.

Two deliberate consequences:

* **The official-record ceiling cannot be walked around.** When a governed
  record turns ``buy`` into ``wait`` for the pack in hand, this layer sees
  ``wait`` and never links the current product.
* **What the person chose is not an input.** Decision Memory, the shelf and a
  one-tap override are never read here; the route asks the Purchase OS for the
  canonical answer with no principal at all.

The alternative must be ``buy`` on its own
------------------------------------------
Step 6A offers a candidate that grades strictly higher and whose own decision
is no worse than the current product's. Against a ``skip`` that can still be a
``wait`` or a ``skip``. Commerce links a product only when the engine's own
answer for that product is ``buy``: it never earns from a product the engine
would tell somebody to wait on or skip. That only removes links; it never
chooses a different candidate.

A listing is not the scanned pack
---------------------------------
A merchant's listing may be another batch, lot, label version, formula or
manufacturing date. Nothing pack-specific transfers to it, so every
``available`` answer carries ``pack_notice: rescan_received_pack`` and the app
tells the person to scan the pack they receive before using it.

Every value here is a stable code; the app renders the words from
``frontend/src/strings/commerce.ts``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domains.alternatives.policy import STATUS_AVAILABLE as ALTERNATIVE_AVAILABLE
from app.domains.commerce import partners
from app.domains.purchase.operating_system import PURCHASE_OS_CONTRACT_VERSION, STATE_DECIDED

COMMERCE_HANDOFF_CONTRACT_VERSION = "commerce-handoff-v1"

STATE_AVAILABLE = "available"
STATE_NOT_APPLICABLE = "not_applicable"
STATE_UNAVAILABLE = "unavailable"

TARGET_CURRENT_PRODUCT = "current_product"
TARGET_ALTERNATIVE = "alternative"

REASON_DECIDED_BUY = "decided_buy"
REASON_CANONICAL_ALTERNATIVE = "canonical_alternative"
REASON_PARTNER_NOT_CONFIGURED = "partner_not_configured"
REASON_UNSUPPORTED_CONTEXT = "unsupported_context"
REASON_REFERENCE_VIEW = "reference_view"
REASON_IDENTITY_INSUFFICIENT = "identity_insufficient"
REASON_PHYSICAL_PACK_REQUIRED = "physical_pack_required"
REASON_DECISION_NOT_MADE = "decision_not_made"
REASON_BARCODE_UNUSABLE = "barcode_unusable"
REASON_NO_ELIGIBLE_ALTERNATIVE = "no_eligible_alternative"
REASON_UNSAFE_DESTINATION = "unsafe_destination"

#: The only pack notice V1 carries: scan what arrives before using it.
PACK_NOTICE_RESCAN = "rescan_received_pack"

_SCAN_KIND = "scan"
_SCAN_STRATEGY = "scan_product"
_CURRENT_PRODUCT_VERDICTS = frozenset({"buy"})
_ALTERNATIVE_VERDICTS = frozenset({"wait", "skip"})
#: The alternative's own canonical decision must be exactly this.
_ALTERNATIVE_OWN_DECISION = "buy"


@dataclass(frozen=True)
class Target:
    kind: str
    barcode: str
    decision: str


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _public_identity(purchase_check: Mapping[str, Any]) -> dict[str, Any] | None:
    """The pack this answer is about, in fields the Purchase OS already publishes."""
    identity = _mapping(purchase_check.get("identity"))
    if not identity:
        return None
    return {
        "barcode": identity.get("barcode"),
        "label_version": identity.get("label_version"),
        "content_fingerprint": identity.get("content_fingerprint"),
    }


def select_target(purchase_check: Mapping[str, Any]) -> tuple[Target | None, str]:
    """Who, if anyone, may be the target. Pure: reads only the answer it is given."""
    context = _mapping(purchase_check.get("context"))
    if (
        purchase_check.get("contract_version") != PURCHASE_OS_CONTRACT_VERSION
        or context.get("kind") != _SCAN_KIND
        or context.get("strategy") != _SCAN_STRATEGY
        or purchase_check.get("boundary") is not None
    ):
        return None, REASON_UNSUPPORTED_CONTEXT
    identity = _mapping(purchase_check.get("identity"))
    if identity.get("reference_view") is not False:
        return None, REASON_REFERENCE_VIEW
    if identity.get("state") != "exact":
        return None, REASON_IDENTITY_INSUFFICIENT
    if identity.get("physical_pack_context") is not True:
        return None, REASON_PHYSICAL_PACK_REQUIRED
    decision = _mapping(purchase_check.get("decision"))
    verdict = decision.get("verdict")
    if decision.get("state") != STATE_DECIDED:
        return None, REASON_DECISION_NOT_MADE
    current_barcode = identity.get("barcode")

    if verdict in _CURRENT_PRODUCT_VERDICTS:
        if not partners.is_exact_gtin(current_barcode):
            return None, REASON_BARCODE_UNUSABLE
        return Target(kind=TARGET_CURRENT_PRODUCT, barcode=current_barcode, decision=verdict), REASON_DECIDED_BUY

    if verdict in _ALTERNATIVE_VERDICTS:
        alternative = _mapping(purchase_check.get("alternative"))
        candidate = _mapping(alternative.get("candidate"))
        barcode = candidate.get("barcode")
        if (
            alternative.get("status") != ALTERNATIVE_AVAILABLE
            or candidate.get("decision") != _ALTERNATIVE_OWN_DECISION
            or not partners.is_exact_gtin(barcode)
            or barcode == current_barcode
        ):
            return None, REASON_NO_ELIGIBLE_ALTERNATIVE
        return Target(kind=TARGET_ALTERNATIVE, barcode=barcode, decision=verdict), REASON_CANONICAL_ALTERNATIVE

    return None, REASON_DECISION_NOT_MADE


def _answer(
    state: str,
    reason: str,
    *,
    purchase_check: Mapping[str, Any] | None = None,
    target: Target | None = None,
    partner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = _mapping(_mapping(purchase_check).get("decision")) if purchase_check is not None else {}
    return {
        "contract_version": COMMERCE_HANDOFF_CONTRACT_VERSION,
        "state": state,
        "target": target.kind if target is not None else None,
        "reason_code": reason,
        # The canonical decision this answer rests on, so the app can refuse to
        # show a link beside a different one.
        "decision": decision.get("verdict") if decision.get("state") == STATE_DECIDED else None,
        "identity": _public_identity(purchase_check) if purchase_check is not None else None,
        "target_barcode": target.barcode if target is not None else None,
        "partner": partner,
        "pack_notice": PACK_NOTICE_RESCAN if state == STATE_AVAILABLE else None,
    }


def partner_not_configured() -> dict[str, Any]:
    """Commerce is off. Nothing was read to say so."""
    return _answer(STATE_UNAVAILABLE, REASON_PARTNER_NOT_CONFIGURED)


def build_handoff(purchase_check: Mapping[str, Any], active: partners.ActivePartner | None) -> dict[str, Any]:
    """The public ``commerce-handoff-v1`` answer for one finished Purchase OS answer."""
    if active is None:
        return partner_not_configured()
    target, reason = select_target(purchase_check)
    if target is None:
        return _answer(STATE_NOT_APPLICABLE, reason, purchase_check=purchase_check)
    try:
        url = partners.search_url(active, target.barcode)
    except partners.UnsafeDestination:
        return _answer(STATE_UNAVAILABLE, REASON_UNSAFE_DESTINATION, purchase_check=purchase_check)
    return _answer(
        STATE_AVAILABLE, reason, purchase_check=purchase_check, target=target,
        partner={
            "key": active.partner.key,
            "display_name": active.partner.display_name,
            "url": url,
            "affiliate": active.affiliate_tag is not None,
        },
    )


__all__ = [
    "COMMERCE_HANDOFF_CONTRACT_VERSION",
    "PACK_NOTICE_RESCAN",
    "REASON_BARCODE_UNUSABLE",
    "REASON_CANONICAL_ALTERNATIVE",
    "REASON_DECIDED_BUY",
    "REASON_DECISION_NOT_MADE",
    "REASON_IDENTITY_INSUFFICIENT",
    "REASON_NO_ELIGIBLE_ALTERNATIVE",
    "REASON_PARTNER_NOT_CONFIGURED",
    "REASON_PHYSICAL_PACK_REQUIRED",
    "REASON_REFERENCE_VIEW",
    "REASON_UNSAFE_DESTINATION",
    "REASON_UNSUPPORTED_CONTEXT",
    "STATE_AVAILABLE",
    "STATE_NOT_APPLICABLE",
    "STATE_UNAVAILABLE",
    "TARGET_ALTERNATIVE",
    "TARGET_CURRENT_PRODUCT",
    "Target",
    "build_handoff",
    "partner_not_configured",
    "select_target",
]
