"""``b2b-product-truth-v1``: Store B-only Product Truth for one exact barcode.

Eligibility
-----------
An answer is ``available`` only when all of these hold::

    exact GS1 barcode (validated by the route before anything runs)
    + a confirmed label snapshot exists for it              (Store B, ours)
    + its facts are readable                                 (readable_label_snapshot)
    + it is complete enough to grade                         (completeness == complete_for_grading)
    + the shared Product Truth authority grades it           (product.truth.grade, outcome graded)
    + every factor that lowers it rests on a published rule  (no source -> no claim)

Anything else is ``not_enough_information`` with one closed reason code. That
is a scientific answer, returned as HTTP 200, not a failure.

What is never consulted
-----------------------
Open Food Facts, in any form: no Store A read, no catalogue lookup, no network
call. A barcode Open Food Facts describes richly and we have never confirmed
is ``not_enough_information`` here, and becomes ``available`` the moment our
own confirmed label is complete — from that label alone. The comparable
alternative and the value comparison are not part of V1 because both read
Store A. Product name and brand come from the confirmed label, never the
catalogue.

Nothing about a pack in somebody's hand either. A B2B caller holds no packet,
so there is no device, no lot, no batch and no official-record pack match:
``scope.physical_pack_context`` is always ``false`` and
``scope.official_records`` is always ``not_evaluated``.

Nothing about a person: no account, subject, personal lens, health mode,
memory, shelf or history.

Independence
------------
:func:`product_truth` takes a session and a barcode. Nothing else. It cannot
see the client, the key, the quota or the usage count, so none of them can
change a grade, a verdict, a reason, a factor, a piece of evidence, the
confidence or the ruleset. The ``truth_fingerprint`` covers exactly the
canonical answer (``contract_version``, ``barcode``, ``state``, ``reason``,
``truth``) in canonical JSON, so two clients can compare answers byte for byte
and an operational field can never shift it.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.urls import openable_url
from app.domains.nutrition.grading import from_scan
from app.domains.nutrition.grading.production_rules import (
    FOOD_RULE_VERSION,
    STATUS_PUBLISHED,
    ProductionRuleset,
    resolve_production_ruleset,
)
from app.domains.product import service as product_service
from app.domains.product import truth as shared_truth
from app.domains.product.models import LabelSnapshot

CONTRACT_VERSION = "b2b-product-truth-v1"

STATE_AVAILABLE = "available"
STATE_NOT_ENOUGH_INFORMATION = "not_enough_information"
STATES: tuple[str, ...] = (STATE_AVAILABLE, STATE_NOT_ENOUGH_INFORMATION)

#: Why an answer is ``not_enough_information``. Closed: a new reason is a
#: contract change.
REASON_NO_CONFIRMED_LABEL = "no_confirmed_label"
REASON_LABEL_UNREADABLE = "label_unreadable"
REASON_LABEL_INCOMPLETE = "label_incomplete"
REASON_NOT_GRADED = "not_graded"
REASON_LABEL_FACTS_INSUFFICIENT = "label_facts_insufficient"
REASON_EVIDENCE_UNPUBLISHED = "evidence_unpublished"
REASONS: tuple[str, ...] = (
    REASON_NO_CONFIRMED_LABEL,
    REASON_LABEL_UNREADABLE,
    REASON_LABEL_INCOMPLETE,
    REASON_NOT_GRADED,
    REASON_LABEL_FACTS_INSUFFICIENT,
    REASON_EVIDENCE_UNPUBLISHED,
)

VERDICTS = frozenset({"buy", "wait", "skip"})
FACTS_PROVENANCE = "confirmed_label_snapshot"
COMPLETE_FOR_GRADING = "complete_for_grading"
#: A positive row that only restates what the confirmed label declares (protein,
#: fibre). Its source is the label itself; it makes no claim of ours.
DECLARED_ON_LABEL = "declared_on_label"

#: GS1 GTIN-8, -12, -13 or -14. ASCII digits only — ``\\d`` would also accept
#: other scripts' digits.
_GTIN = re.compile(r"(?:[0-9]{8}|[0-9]{12,14})")


def is_exact_gtin(value: object) -> bool:
    """A GS1 barcode with a valid check digit, and nothing else.

    Deliberately B2B's own few lines rather than an import: the only other copy
    lives in the Commerce domain, and B2B must not depend on Commerce for
    anything.
    """
    if not isinstance(value, str) or not _GTIN.fullmatch(value) or not value.strip("0"):
        return False
    digits = [ord(character) - 48 for character in value]
    body, check = digits[:-1], digits[-1]
    total = sum(digit * (3 if position % 2 == 0 else 1) for position, digit in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(canonical: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def ruleset_fingerprint(ruleset: ProductionRuleset) -> str:
    """Which rules were published, on which claims, when this answer was made."""
    material = sorted(
        [
            row.rule_id, row.rule_version, row.status,
            sorted(str(claim_id) for claim_id in row.claim_ids), row.claim_version,
        ]
        for row in ruleset.provenance.values()
    )
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def answer(barcode: str, state: str, reason: str | None, truth: dict[str, Any] | None) -> dict[str, Any]:
    """The contract body, minus the route's operational ``meta``."""
    if state not in STATES:
        raise ValueError("unknown_state")
    if (state == STATE_AVAILABLE) != (truth is not None) or (state == STATE_AVAILABLE) == (reason is not None):
        raise ValueError("inconsistent_answer")
    if reason is not None and reason not in REASONS:
        raise ValueError("unknown_reason")
    canonical = {
        "contract_version": CONTRACT_VERSION,
        "barcode": barcode,
        "state": state,
        "reason": reason,
        "truth": truth,
    }
    return {**canonical, "truth_fingerprint": fingerprint(canonical)}


def _not_enough(barcode: str, reason: str) -> dict[str, Any]:
    return answer(barcode, STATE_NOT_ENOUGH_INFORMATION, reason, None)


def _rests_on_published_rule(row: dict[str, Any]) -> bool:
    """A named published rule, cited claim and only openable sources.

    Production only ever marks a rule published when it has a published claim;
    this does not take that on trust. A "published" rule that names no claim
    has nothing a client could check, and is not distributed. Neither is a
    lowering factor without a source URL the client can open.
    """
    evidence = row.get("evidence") or {}
    sources = row.get("sources")
    return (
        bool(row.get("rule"))
        and evidence.get("status") == STATUS_PUBLISHED
        and bool(evidence.get("evidence_claim_ids"))
        and isinstance(sources, (list, tuple))
        and bool(sources)
        and all(
            isinstance(source, dict) and openable_url(source.get("url")) is not None
            for source in sources
        )
    )


def _source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": source["name"],
        "url": source["url"],
        "publisher": source.get("publisher"),
        "identifier": source["identifier"],
    }


def _quantity(quantity: dict[str, Any] | None) -> dict[str, Any] | None:
    if quantity is None:
        return None
    return {"value": quantity["value"], "unit": quantity["unit"], "basis": quantity["basis"]}


def _negative(row: dict[str, Any]) -> dict[str, Any]:
    """A factor that lowers the grade, with the published evidence it rests on.

    The engine's free-text ``detail`` (trace findings, interpretation notes) is
    an internal reasoning record and is not part of the contract.
    """
    evidence = row["evidence"]
    return {
        "key": row["key"],
        "label": row["label"],
        "status": row["status"],
        "band": row["band"],
        "explanation": row["explanation"],
        "quantity": _quantity(row.get("quantity")),
        "rule": row["rule"],
        "evidence": {
            "status": evidence["status"],
            "rule_version": evidence["rule_version"],
            "evidence_claim_ids": list(evidence["evidence_claim_ids"]),
            "evidence_claim_version": evidence["evidence_claim_version"],
        },
        "sources": [_source(source) for source in row.get("sources") or []],
    }


def _declared(row: dict[str, Any]) -> dict[str, Any]:
    """What the confirmed label declares. Its source is the label; no rule, no claim."""
    return {
        "key": row["key"],
        "label": row["label"],
        "status": row["status"],
        "band": row["band"],
        "explanation": row["explanation"],
        "quantity": _quantity(row.get("quantity")),
        "rule": None,
        "evidence": None,
        "sources": [],
    }


def _identity_text(facts: dict[str, Any], key: str) -> str | None:
    value = facts.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def project(barcode: str, snapshot: LabelSnapshot, graded: shared_truth.GradedProduct) -> dict[str, Any]:
    """The contract answer for one graded confirmed label. Pure.

    Takes the snapshot the grade was built from and the shared authority's
    result. It re-decides nothing: the grade, the verdict and the reason key
    are the Product Result's own, and the only thing decided here is whether
    every part of that answer may be distributed.
    """
    payload = graded.payload
    outcome = payload["outcome"]
    if outcome == "not_graded":
        return _not_enough(barcode, REASON_NOT_GRADED)
    if outcome != "graded" or payload["grade"] is None:
        # Product Truth records the governed rule IDs that withheld a grade.
        # A fired optional candidate is just as unpublished as a required
        # candidate; invalid label values carry no governed rule ID here.
        if any(
            (row := graded.ruleset.for_rule(missing)) is not None and not row.published
            for missing in graded.result.missing
        ):
            return _not_enough(barcode, REASON_EVIDENCE_UNPUBLISHED)
        return _not_enough(barcode, REASON_LABEL_FACTS_INSUFFICIENT)
    decision = payload["decision"]
    verdict = decision["action"]
    if verdict not in VERDICTS:
        return _not_enough(barcode, REASON_LABEL_FACTS_INSUFFICIENT)
    # No source, no claim. A factor that lowered this grade and rests on a
    # candidate constant (or on no rule at all) cannot be distributed, and the
    # grade cannot be distributed without the factors that explain it.
    negatives = payload["negatives"]
    if not all(_rests_on_published_rule(row) for row in negatives):
        return _not_enough(barcode, REASON_EVIDENCE_UNPUBLISHED)
    facts = snapshot.facts
    truth = {
        "product": {
            "name": _identity_text(facts, "product_name"),
            "brand": _identity_text(facts, "brand"),
        },
        "facts_provenance": FACTS_PROVENANCE,
        "label": {
            "version_number": snapshot.version_number,
            "content_fingerprint": snapshot.content_fingerprint,
            "completeness": snapshot.completeness,
        },
        "confidence": {"level": snapshot.confidence},
        "grade": {
            "outcome": outcome,
            "grade": payload["grade"],
            "band": payload["band"],
            "engine_version": payload["engine_version"],
        },
        "decision": {
            "state": "decided",
            "verdict": verdict,
            "reason_key": decision["reason_key"],
        },
        "factors": {
            "negatives": [_negative(row) for row in negatives],
            # Label declarations only. A positive the engine derives without a
            # published rule of its own is withheld rather than asserted.
            "positives": [
                _declared(row) for row in payload["positives"] if row.get("explanation") == DECLARED_ON_LABEL
            ],
        },
        "ruleset": {
            "rule_version": FOOD_RULE_VERSION,
            "fingerprint": ruleset_fingerprint(graded.ruleset),
        },
        "scope": {
            "physical_pack_context": False,
            "official_records": "not_evaluated",
        },
    }
    return answer(barcode, STATE_AVAILABLE, None, truth)


async def product_truth(session: AsyncSession, barcode: str) -> dict[str, Any]:
    """Current Product Truth for ``barcode``, from Store B and published rules only.

    Computed per request and never stored: rules and evidence change, and a
    stored answer would be a stale second authority.
    """
    snapshot = await product_service.latest_label_snapshot(session, barcode)
    if snapshot is None:
        return _not_enough(barcode, REASON_NO_CONFIRMED_LABEL)
    readable = product_service.readable_label_snapshot(snapshot)
    if readable is None:
        return _not_enough(barcode, REASON_LABEL_UNREADABLE)
    if readable.completeness != COMPLETE_FOR_GRADING:
        return _not_enough(barcode, REASON_LABEL_INCOMPLETE)
    ruleset = await resolve_production_ruleset(session)
    product = from_scan.build_confirmed_label(barcode=barcode, facts=readable.facts)
    return project(barcode, readable, shared_truth.grade(product, ruleset))


__all__ = [
    "CONTRACT_VERSION",
    "REASONS",
    "STATES",
    "STATE_AVAILABLE",
    "STATE_NOT_ENOUGH_INFORMATION",
    "answer",
    "canonical_json",
    "fingerprint",
    "is_exact_gtin",
    "product_truth",
    "project",
    "ruleset_fingerprint",
]
