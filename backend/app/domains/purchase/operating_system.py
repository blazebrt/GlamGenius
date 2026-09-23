"""Step 14 — the Purchase Operating System: one current answer, composed.

"Should I buy this?" already has authorities. The Product Result grades a
scanned pack; the Care and Fragrance checks decide a candidate; Decision Memory
remembers what a person chose; the shelf knows what they own; the official
records know what FSSAI lists; Step 12A and 12B know what changed. This module
decides nothing those authorities already decide. It reads them, keeps their
meaning, and says one thing:

* one current decision (``buy``/``wait``/``skip``), or an honest truth state
  (``not_enough_information``, ``prohibited``, ``unsupported``);
* one primary reason, named by the authority it came from;
* the authorities that mattered, in order, as structured states;
* the person's memory, ownership and one alternative, as context.

What it deliberately is not
---------------------------
* **Not a verdict engine.** No grading, no Care policy, no Fragrance policy is
  re-run here. The Care and Fragrance verdicts, and the Product Result action,
  are taken as they are.
* **Not a score.** There is no number that combines grade, price, ownership,
  evidence or regulation. Precedence is written out below as policy, and the
  policy has exactly one cross-authority rule.
* **Not a store.** Nothing is written. Every field is derived per request from
  rows other domains own; there is no Purchase OS table, cache or snapshot.

The one cross-authority rule (scan only)
----------------------------------------
An exact, governed, current FSSAI official record matching the pack in this
person's hand places a ``wait`` ceiling on the scan decision::

    buy  -> wait     wait -> wait     skip -> skip     none -> wait

"Governed" is Product Watch's own rule (:func:`product.watch.record_governs_pack`):
the exact licence-and-lot matcher resolved it, its Step 12B ledger validates,
and its source is the openable FoSCoS page. It is applied only under
server-proven physical-pack authority. It is never released by a record's
absence from a later export, by a termination date or by status text: no
reviewed rule says any of those means "no longer applies", and this module does
not invent one.

Everything else — a changed formula, a changed regulatory record, a better
alternative, owning the exact pack, having chosen differently before — is
context. It is shown, and it never changes the decision here. Care and
Fragrance already weigh their own analogues inside their canonical verdicts.

Customer copy
-------------
Nothing here is prose. Every state and reason is a stable code; the app renders
each one from its keyed string file (``frontend/src/strings/purchaseOs.ts``).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import date
from types import MappingProxyType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.family.decision_subject import DecisionSubject, serialize_decision_subject
from app.domains.inventory import scan_ownership
from app.domains.inventory.schemas import ScanOwnershipCreate
from app.domains.official_records import service as official_records_service
from app.domains.official_records.source import SOURCE_URL as OFFICIAL_SOURCE_URL
from app.domains.product import scan_memory
from app.domains.product import watch as product_watch
from app.domains.product.models import ScanDevice
from app.domains.purchase import decision_memory
from app.domains.purchase import service as purchase_service
from app.domains.purchase.candidate_truth import build_care_candidate_truth
from app.domains.purchase.contract import PurchaseStrategy, resolve_purchase_strategy
from app.domains.purchase.fragrance_truth import build_fragrance_candidate_truth
from app.domains.recommendation.models import ShoppingCandidate

PURCHASE_OS_CONTRACT_VERSION = "step-14-v1"

# --- Decision states --------------------------------------------------------
STATE_DECIDED = "decided"
STATE_NOT_ENOUGH_INFORMATION = "not_enough_information"
STATE_PROHIBITED = "prohibited"
STATE_UNSUPPORTED = "unsupported"

VERDICTS = frozenset({"buy", "wait", "skip"})

# --- Reason codes this layer itself may state ---------------------------------
#: The only reason the Purchase OS originates as a *decision* reason.
REASON_OFFICIAL_RECORD = "official_record_matches_pack"
REASON_IDENTITY_INSUFFICIENT = "identity_insufficient"
REASON_PRODUCT_RESULT_UNAVAILABLE = "product_result_unavailable"
REASON_CANDIDATE_CONFIRMATION_REQUIRED = "candidate_confirmation_required"
REASON_CANDIDATE_DETAILS_UNSUPPORTED = "candidate_details_unsupported"
REASON_PURCHASE_PROHIBITED = "supplement_purchase_prohibited"
REASON_UNSUPPORTED_STRATEGY = "unsupported_strategy"

# --- Authorities the chain may name -----------------------------------------
AUTHORITY_PRODUCT_RESULT = "product_result"
AUTHORITY_OFFICIAL_RECORDS = "official_records"
AUTHORITY_REGULATORY_CHANGE = "regulatory_change"
AUTHORITY_LABEL_CHANGE = "label_change"
AUTHORITY_OWNERSHIP = "ownership"
AUTHORITY_DECISION_MEMORY = "decision_memory"
AUTHORITY_ALTERNATIVE = "alternative"
AUTHORITY_PURCHASE_OS = "purchase_os"
AUTHORITY_SUPPLEMENT_BOUNDARY = "supplement_boundary"

#: The official source a customer can open. The record's own page, exactly as
#: Product Watch requires it; "FSSAI / FoSCoS" is the authority's proper name.
OFFICIAL_SOURCE = MappingProxyType({"name": "FSSAI / FoSCoS", "url": OFFICIAL_SOURCE_URL})


def _fingerprint(material: Mapping[str, Any]) -> str:
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decision(
    state: str,
    *,
    verdict: str | None = None,
    reason: str,
    authority: str,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    if state == STATE_DECIDED:
        if verdict not in VERDICTS:
            raise ValueError("decided_state_requires_buy_wait_or_skip")
    elif verdict is not None:
        raise ValueError("verdict_only_on_decided_state")
    return {
        "state": state,
        "verdict": verdict,
        "primary_reason_code": reason,
        "primary_reason_authority": authority,
        "decision_fingerprint": fingerprint,
    }


def _envelope(
    *,
    kind: str,
    strategy: str | None,
    category: str | None,
    subject: DecisionSubject | None,
    identity: Mapping[str, Any],
    decision: Mapping[str, Any],
    authorities: list[dict[str, Any]],
    memory: Mapping[str, Any] | None,
    ownership: Mapping[str, Any] | None,
    alternative: Mapping[str, Any] | None,
    value: Mapping[str, Any] | None,
    missing_information: list[str],
    boundary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "contract_version": PURCHASE_OS_CONTRACT_VERSION,
        "context": {"kind": kind, "strategy": strategy, "category": category},
        "subject": serialize_decision_subject(subject) if subject is not None else None,
        "identity": dict(identity),
        "decision": dict(decision),
        "authorities": authorities,
        "memory": dict(memory) if memory is not None else None,
        "ownership": dict(ownership) if ownership is not None else None,
        "alternative": dict(alternative) if alternative is not None else None,
        "value": dict(value) if value is not None else None,
        "boundary": dict(boundary) if boundary is not None else None,
        "missing_information": sorted(set(missing_information)),
    }


# ===========================================================================
# Scan product
# ===========================================================================
def official_ceiling(base: str | None) -> str:
    """The ``wait`` ceiling an exact governed official record places on a scan decision."""
    return "skip" if base == "skip" else "wait"


def _scan_identity(barcode: str, label_version: Any) -> tuple[dict[str, Any], uuid.UUID | None]:
    """The exact product version the answer is about, or ``insufficient``.

    Exact means a governed confirmed label version exists: its number and its
    integrity fingerprint, which the existing override and shelf writes already
    require. Without one there is only a barcode and possibly a catalogue
    record, and neither is an exact version.
    """
    if not isinstance(label_version, Mapping):
        return {"state": "insufficient", "barcode": barcode, "label_version": None, "content_fingerprint": None}, None
    try:
        snapshot_id = uuid.UUID(str(label_version.get("id")))
        number = int(label_version["version_number"])
        fingerprint = str(label_version["content_fingerprint"])
    except (KeyError, TypeError, ValueError):
        return {"state": "insufficient", "barcode": barcode, "label_version": None, "content_fingerprint": None}, None
    return {
        "state": "exact", "barcode": barcode, "label_version": number, "content_fingerprint": fingerprint,
    }, snapshot_id


async def _governed_official_records(
    session: AsyncSession, product_result: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """The official records that govern this pack, by Product Watch's rule, from the envelope."""
    envelope = product_result.get("official_records")
    records = [
        row for row in (envelope.get("records") or [] if isinstance(envelope, Mapping) else [])
        if isinstance(row, Mapping)
    ]
    ids = [row["recall_id"] for row in records if isinstance(row.get("recall_id"), str)]
    heads = await official_records_service.validated_revision_heads(session, ids)
    return [row for row in records if product_watch.record_governs_pack(row, heads)]


def compose_scan_decision(
    *,
    base_action: str | None,
    base_reason: str | None,
    identity_exact: bool,
    official_governs: bool,
) -> tuple[str, str | None, str, str, bool]:
    """The explicit Step 14 precedence for a scan. Returns (state, verdict, reason, authority, guard_changed).

    1. No exact version: not enough information. Nothing version-scoped can be
       said, and a decision is not manufactured from a barcode.
    2. A governed exact official record: the ``wait`` ceiling.
    3. Otherwise the Product Result action, unchanged — or not enough
       information when it has none (not graded, not enough information).
    """
    base = base_action if base_action in VERDICTS else None
    if not identity_exact:
        return STATE_NOT_ENOUGH_INFORMATION, None, REASON_IDENTITY_INSUFFICIENT, AUTHORITY_PURCHASE_OS, False
    if official_governs:
        verdict = official_ceiling(base)
        if verdict != base:
            return STATE_DECIDED, verdict, REASON_OFFICIAL_RECORD, AUTHORITY_OFFICIAL_RECORDS, True
        return STATE_DECIDED, verdict, base_reason or REASON_OFFICIAL_RECORD, AUTHORITY_PRODUCT_RESULT, False
    if base is None:
        return (
            STATE_NOT_ENOUGH_INFORMATION, None, base_reason or REASON_PRODUCT_RESULT_UNAVAILABLE,
            AUTHORITY_PRODUCT_RESULT, False,
        )
    return STATE_DECIDED, base, base_reason or REASON_PRODUCT_RESULT_UNAVAILABLE, AUTHORITY_PRODUCT_RESULT, False


def _scan_alternative(product_result: Mapping[str, Any]) -> dict[str, Any]:
    """The Product Result's one comparable alternative, as context. At most one candidate."""
    envelope = product_result.get("alternative")
    if not isinstance(envelope, Mapping):
        return {"status": "not_enough_information", "reason_key": None, "candidate": None}
    candidate = envelope.get("candidate")
    public = None
    if isinstance(candidate, Mapping):
        public = {
            "barcode": candidate.get("barcode"),
            "product_name": candidate.get("product_name"),
            "brand": candidate.get("brand"),
            "grade": candidate.get("grade"),
            "decision": candidate.get("decision"),
            # The Open Food Facts licence notice travels with the candidate.
            "attribution": candidate.get("attribution"),
        }
    return {"status": envelope.get("status"), "reason_key": envelope.get("reason_key"), "candidate": public}


async def _scan_memory(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID | None,
    decision_subject: DecisionSubject | None,
    barcode: str,
    identity: Mapping[str, Any],
    snapshot_id: uuid.UUID | None,
    reference_view: bool,
) -> dict[str, Any]:
    """What this person chose about this exact version — their outcome, not our history.

    Scan memory stores the person's own BUY/WAIT/SKIP and the exact version it
    was about. It stores no recommendation, so none is reported: ``fidelity``
    says so, and what GlamGenius "must have said" then is never reconstructed.
    """
    base = {"kind": "scan_decision", "fidelity": "user_outcome_only"}
    if principal_account_id is None or decision_subject is None:
        return {**base, "state": "sign_in_required"}
    if reference_view:
        return {**base, "state": "reference_view"}
    if identity.get("state") != "exact" or snapshot_id is None:
        return {**base, "state": "identity_insufficient"}
    version = int(identity["label_version"])
    fingerprint = str(identity["content_fingerprint"])
    envelope = await scan_memory.read_scan_memory(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        barcode=barcode,
        label_snapshot_id=snapshot_id,
        label_version=version,
        content_fingerprint=fingerprint,
    )
    earlier = await scan_memory.latest_decision_on_another_version(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        barcode=barcode,
        label_snapshot_id=snapshot_id,
        label_version=version,
        content_fingerprint=fingerprint,
    )
    # Three separate facts, never folded into one: the exact current decision,
    # the latest attributable decision on another version, and whether this
    # subject's history for the whole barcode is complete. An unattributed
    # decision on an older version is excluded from the second read, so only
    # the barcode-wide answer can say that something was left out.
    coverage = await scan_memory.barcode_history_coverage(
        session,
        principal_account_id=principal_account_id,
        decision_subject=decision_subject,
        barcode=barcode,
    )
    current = envelope.get("decision")
    complete = bool(envelope["history_coverage"]["complete_for_subject"]) and bool(coverage["complete_for_subject"])
    # A known exact decision is kept even when older history is incomplete;
    # ``history_complete`` then says so alongside it.
    if current:
        state = "prior_exact_decision"
    elif not complete:
        state = "history_incomplete"
    else:
        state = "no_prior_exact_decision"
    return {
        **base,
        "state": state,
        "current_decision": (
            {"decision": current["decision"], "occurred_at": current["occurred_at"]} if current else None
        ),
        "exact_decision_count": len(envelope.get("history") or []),
        "history_complete": complete,
        "earlier_version_decision": earlier,
    }


async def _scan_ownership(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID | None,
    device: ScanDevice,
    barcode: str,
    identity: Mapping[str, Any],
    snapshot_id: uuid.UUID | None,
    physical: bool,
    reference_view: bool,
) -> dict[str, Any]:
    """Only what an ``InventoryProductLink`` for this exact version proves. Context only."""
    base = {"authority": "inventory_product_link", "effect": "context_only"}
    if principal_account_id is None:
        return {**base, "state": "sign_in_required"}
    if reference_view:
        return {**base, "state": "reference_view"}
    if identity.get("state") != "exact" or snapshot_id is None:
        return {**base, "state": "identity_insufficient"}
    if not physical:
        return {**base, "state": "physical_pack_required"}
    body = ScanOwnershipCreate(
        barcode=barcode, label_snapshot_id=snapshot_id, label_version=int(identity["label_version"]),
        content_fingerprint=str(identity["content_fingerprint"]), client_mutation_id="purchase-os-read",
    )
    try:
        status = await scan_ownership.status_from_scan(
            session, account_id=principal_account_id, device=device, body=body,
        )
    except (ValueError, scan_ownership.OwnershipConflict):
        return {**base, "state": "physical_pack_required"}
    state = {
        "owned": "owned_exact_version",
        "eligible_not_owned": "not_owned",
        "not_enough_information": "not_eligible_for_shelf",
    }.get(status.get("status"), "not_eligible_for_shelf")
    return {**base, "state": state}


async def scan_purchase_check(
    session: AsyncSession,
    *,
    barcode: str,
    product_result: Mapping[str, Any],
    device: ScanDevice,
    requested_physical_pack_context: bool,
    principal_account_id: uuid.UUID | None,
    decision_subject: DecisionSubject | None,
) -> dict[str, Any]:
    """One purchase answer for a scanned barcode, from the Product Result it was given.

    ``product_result`` is the payload the Product Result route itself built for
    this request — same ruleset, same effective pack authority (the request
    ceiling AND a server-proven capture), same official-records envelope. It is
    consumed, never re-derived.
    """
    identity, snapshot_id = _scan_identity(barcode, product_result.get("label_version"))
    identity_exact = identity["state"] == "exact"
    reference_view = not requested_physical_pack_context
    physical = product_result.get("physical_pack_context") is True
    decision_block = product_result.get("decision") if isinstance(product_result.get("decision"), Mapping) else {}
    base_action = decision_block.get("action")
    base_reason = decision_block.get("reason_key")

    governed: list[Mapping[str, Any]] = []
    if physical and identity_exact:
        governed = await _governed_official_records(session, product_result)
    state, verdict, reason, authority, guard_changed = compose_scan_decision(
        base_action=base_action, base_reason=base_reason,
        identity_exact=identity_exact, official_governs=bool(governed),
    )
    decision = _decision(
        state, verdict=verdict, reason=reason, authority=authority,
        fingerprint=_fingerprint({
            "contract": PURCHASE_OS_CONTRACT_VERSION, "kind": "scan", "barcode": barcode,
            "label_version": identity.get("label_version"),
            "content_fingerprint": identity.get("content_fingerprint"),
            "base_action": base_action if base_action in VERDICTS else None,
            "state": state, "verdict": verdict, "reason": reason,
            "governed_records": sorted(str(row["recall_id"]) for row in governed),
        }),
    )

    authorities: list[dict[str, Any]] = [{
        "authority": AUTHORITY_PRODUCT_RESULT,
        "status": "applied" if base_action in VERDICTS else "not_enough_information",
        "action": base_action if base_action in VERDICTS else None,
        "reason_key": base_reason,
    }]
    if reference_view:
        official_status = "reference_view"
    elif not identity_exact:
        official_status = "identity_insufficient"
    elif not physical:
        official_status = "physical_pack_required"
    elif not governed:
        official_status = "no_governed_match"
    else:
        official_status = "applied" if guard_changed else "consistent_with_decision"
    official_entry: dict[str, Any] = {"authority": AUTHORITY_OFFICIAL_RECORDS, "status": official_status}
    if governed:
        official_entry["matched_record_count"] = len(governed)
        official_entry["source"] = dict(OFFICIAL_SOURCE)
    authorities.append(official_entry)
    changed_records = [
        row for row in governed
        if isinstance(row.get("regulatory_change"), Mapping) and row["regulatory_change"].get("status") == "changed"
    ]
    if changed_records:
        authorities.append({
            "authority": AUTHORITY_REGULATORY_CHANGE, "status": "changed", "effect": "context_only",
            "changed_record_count": len(changed_records), "source": dict(OFFICIAL_SOURCE),
        })
    label_change = product_result.get("label_change")
    authorities.append({
        "authority": AUTHORITY_LABEL_CHANGE,
        "status": label_change.get("status") if isinstance(label_change, Mapping) else "no_confirmed_label",
        "effect": "context_only",
    })

    ownership = await _scan_ownership(
        session, principal_account_id=principal_account_id, device=device, barcode=barcode,
        identity=identity, snapshot_id=snapshot_id, physical=physical, reference_view=reference_view,
    )
    authorities.append({"authority": AUTHORITY_OWNERSHIP, "status": ownership["state"], "effect": "context_only"})
    memory = await _scan_memory(
        session, principal_account_id=principal_account_id, decision_subject=decision_subject,
        barcode=barcode, identity=identity, snapshot_id=snapshot_id, reference_view=reference_view,
    )
    authorities.append({"authority": AUTHORITY_DECISION_MEMORY, "status": memory["state"], "effect": "context_only"})
    alternative = _scan_alternative(product_result)
    authorities.append({"authority": AUTHORITY_ALTERNATIVE, "status": alternative["status"], "effect": "context_only"})

    missing = [str(item) for item in (product_result.get("missing") or []) if isinstance(item, str)]
    if not identity_exact:
        missing.append("exact_label_version")
    if identity_exact and not physical and not reference_view:
        missing.append("physical_pack")
    return _envelope(
        kind="scan", strategy="scan_product", category=(
            (product_result.get("taxonomy") or {}).get("category")
            if isinstance(product_result.get("taxonomy"), Mapping) else None
        ),
        subject=decision_subject, identity={
            **identity, "physical_pack_context": physical, "reference_view": reference_view,
        },
        decision=decision, authorities=authorities, memory=memory, ownership=ownership,
        alternative=alternative,
        # A scanned pack has no customer price here, and Step 14 builds no
        # price engine: the price is simply unknown.
        value={"state": "missing", "missing": ["current_price"]},
        missing_information=missing,
    )


# ===========================================================================
# Candidate strategies
# ===========================================================================
def _candidate_identity(candidate: ShoppingCandidate, guard_identity: Mapping[str, Any] | None) -> dict[str, Any]:
    state = (guard_identity or {}).get("state")
    return {
        "state": "exact" if state == "exact" else "insufficient",
        "version": (guard_identity or {}).get("version"),
        "display_name": candidate.display_name,
        "brand": candidate.brand,
    }


_GUARD_STATES = MappingProxyType({
    "no_step9a_prior_event": "no_prior_exact_decision",
    "exact_prior_bought": "prior_exact_decision",
    "exact_prior_waiting": "prior_exact_decision",
    "exact_prior_skipped": "prior_exact_decision",
    "exact_prior_consideration": "prior_exact_decision",
    "historical_context_incomplete": "history_incomplete",
    "identity_insufficient": "identity_insufficient",
})


def _candidate_memory(embedded: Mapping[str, Any] | None, guard: Mapping[str, Any]) -> dict[str, Any]:
    """Candidate memory keeps its own historical recommendation snapshot, verbatim.

    ``current_decision`` is this person's decision about this candidate;
    ``most_recent_exact`` is their newest decision about any candidate with the
    same exact identity. Each carries the recommendation *as recorded then*,
    which is a historical fact and is never recomputed with today's policy.
    """
    current = None
    if embedded:
        current = {
            "decision": embedded.get("decision"),
            "recommendation_at_decision": (embedded.get("recommendation_at_decision") or {}).get("verdict"),
            "followed_recommendation": embedded.get("followed_recommendation"),
            "updated_at": embedded.get("updated_at"),
        }
    recent = guard.get("most_recent")
    most_recent = None
    if isinstance(recent, Mapping):
        most_recent = {
            "decision": recent.get("decision"),
            "recommendation_at_decision": (recent.get("recommendation_at_decision") or {}).get("verdict"),
            "occurred_at": recent.get("occurred_at"),
        }
    coverage = guard.get("history_coverage") or {}
    return {
        "kind": "candidate_decision",
        "fidelity": "recommendation_snapshot",
        "state": _GUARD_STATES.get(str(guard.get("guard_state")), "history_incomplete"),
        # The Decision Memory guard's own state, verbatim, for the existing
        # purchase-memory card; ``state`` above is its common-contract reading.
        "guard_state": guard.get("guard_state"),
        "current_decision": current,
        "most_recent_exact": most_recent,
        "prior_consideration_count": int(guard.get("prior_consideration_count") or 0),
        "history_complete": bool(coverage.get("complete_for_subject", False)),
    }


def _candidate_not_ready(
    candidate: ShoppingCandidate, strategy: PurchaseStrategy, subject: DecisionSubject, reason: str,
) -> dict[str, Any]:
    return _envelope(
        kind="candidate", strategy=strategy.key, category=candidate.category, subject=subject,
        identity=_candidate_identity(candidate, None),
        decision=_decision(STATE_NOT_ENOUGH_INFORMATION, reason=reason, authority=strategy.key),
        authorities=[{"authority": strategy.key, "status": "not_enough_information", "reason_code": reason}],
        memory=None, ownership=None, alternative=None, value=None,
        missing_information=["confirmed_candidate_facts"],
    )


async def _care(
    session: AsyncSession, *, candidate: ShoppingCandidate, strategy: PurchaseStrategy,
    principal_account_id: uuid.UUID, account_id_str: str, decision_subject: DecisionSubject,
    plan_date: date | None,
) -> dict[str, Any]:
    """Care: the canonical Care check is the decision. Nothing is recalculated."""
    from app.domains.purchase.check_service import resolve_care_purchase_check

    if not build_care_candidate_truth(candidate).facts_trusted:
        return _candidate_not_ready(candidate, strategy, decision_subject, REASON_CANDIDATE_CONFIRMATION_REQUIRED)
    check = await resolve_care_purchase_check(
        session, account_id=principal_account_id, account_id_str=account_id_str,
        candidate_id=candidate.id, plan_date=plan_date, decision_subject=decision_subject,
    )
    verdict = check["verdict"]
    context = verdict.get("decision_context") or {}
    guard = await decision_memory.purchase_guard(
        session, principal_account_id=principal_account_id,
        decision_subject=decision_subject, candidate=candidate,
    )
    memory = _candidate_memory(check.get("decision"), guard)
    ownership = {
        "authority": "care_purchase", "effect": "within_care_verdict",
        "role_status": context.get("role_status"),
        "eligible_owned_same_slot_count": context.get("eligible_owned_same_slot_count"),
    }
    value = {
        "authority": "care_purchase", "effect": "within_care_verdict",
        "candidate_spend_status": context.get("candidate_spend_status"),
        "owned_value_recovery_status": context.get("owned_value_recovery_status"),
        "currency_context_status": context.get("currency_context_status"),
    }
    return _envelope(
        kind="candidate", strategy=strategy.key, category=candidate.category, subject=decision_subject,
        identity=_candidate_identity(candidate, guard.get("identity")),
        decision=_decision(
            STATE_DECIDED, verdict=verdict["verdict"], reason=verdict["primary_reason_code"],
            authority=strategy.key, fingerprint=verdict["decision_fingerprint"],
        ),
        authorities=[
            {"authority": strategy.key, "status": "applied", "supporting_reason_codes": list(verdict.get("supporting_reason_codes") or [])},
            {"authority": "care_evidence", "status": context.get("evidence_support_status"), "effect": "within_care_verdict"},
            {"authority": AUTHORITY_OWNERSHIP, "status": context.get("role_status"), "effect": "within_care_verdict"},
            {"authority": AUTHORITY_DECISION_MEMORY, "status": memory["state"], "effect": "context_only"},
        ],
        memory=memory, ownership=ownership, alternative=None, value=value,
        missing_information=[],
    )


async def _fragrance(
    session: AsyncSession, *, candidate: ShoppingCandidate, strategy: PurchaseStrategy,
    principal_account_id: uuid.UUID, account_id_str: str, decision_subject: DecisionSubject,
    plan_date: date | None,
) -> dict[str, Any]:
    """Fragrance: the canonical Fragrance check is the decision. Nothing is recalculated."""
    from app.domains.purchase.check_service import resolve_fragrance_check

    del account_id_str, plan_date
    try:
        trusted = build_fragrance_candidate_truth(candidate).facts_trusted
    except ValueError:
        return _candidate_not_ready(candidate, strategy, decision_subject, REASON_CANDIDATE_DETAILS_UNSUPPORTED)
    if not trusted:
        return _candidate_not_ready(candidate, strategy, decision_subject, REASON_CANDIDATE_CONFIRMATION_REQUIRED)
    check = await resolve_fragrance_check(
        session, account_id=principal_account_id, candidate_id=candidate.id, decision_subject=decision_subject,
    )
    verdict = check["verdict"]
    collection = check.get("collection_context") or {}
    guard = await decision_memory.purchase_guard(
        session, principal_account_id=principal_account_id,
        decision_subject=decision_subject, candidate=candidate,
    )
    memory = _candidate_memory(check.get("decision"), guard)
    ownership = {
        "authority": "fragrance_purchase", "effect": "within_fragrance_verdict",
        "owned_perfume_count": collection.get("owned_perfume_count"),
        "exact_owned_count": len(collection.get("exact_owned") or []),
        "same_family_owned_count": len(collection.get("same_family_owned") or []),
    }
    return _envelope(
        kind="candidate", strategy=strategy.key, category=candidate.category, subject=decision_subject,
        identity=_candidate_identity(candidate, guard.get("identity")),
        decision=_decision(
            STATE_DECIDED, verdict=verdict["verdict"], reason=verdict["primary_reason_code"],
            authority=strategy.key, fingerprint=verdict["decision_fingerprint"],
        ),
        authorities=[
            {"authority": strategy.key, "status": "applied", "supporting_reason_codes": list(verdict.get("supporting_reason_codes") or [])},
            {"authority": AUTHORITY_OWNERSHIP, "status": "within_fragrance_verdict", "effect": "within_fragrance_verdict"},
            {"authority": AUTHORITY_DECISION_MEMORY, "status": memory["state"], "effect": "context_only"},
        ],
        memory=memory, ownership=ownership, alternative=None,
        value={
            "authority": "fragrance_purchase", "effect": "within_fragrance_verdict",
            "candidate_price": "recorded" if candidate.price is not None else "missing",
        },
        missing_information=list(verdict.get("missing_information") or []),
    )


async def _supplement(
    session: AsyncSession, *, candidate: ShoppingCandidate, strategy: PurchaseStrategy,
    principal_account_id: uuid.UUID, account_id_str: str, decision_subject: DecisionSubject,
    plan_date: date | None,
) -> dict[str, Any]:
    """Supplements are purchase-prohibited. No check runs, and no strategy stands in for one."""
    del session, principal_account_id, account_id_str, plan_date
    return _envelope(
        kind="candidate", strategy=strategy.key, category=candidate.category, subject=decision_subject,
        identity=_candidate_identity(candidate, None),
        decision=_decision(STATE_PROHIBITED, reason=REASON_PURCHASE_PROHIBITED, authority=AUTHORITY_SUPPLEMENT_BOUNDARY),
        authorities=[{"authority": AUTHORITY_SUPPLEMENT_BOUNDARY, "status": "prohibited"}],
        memory=None, ownership=None, alternative=None, value=None, missing_information=[],
        # Where the product belongs instead: the supplement label and ownership
        # utility, which records what the label says and never whether to buy.
        boundary={"code": REASON_PURCHASE_PROHIBITED, "redirect": "supplement_label_utility"},
    )


CandidateAdapter = Callable[..., Awaitable[dict[str, Any]]]

#: Explicit strategy routing. A literal table, not reflection: a strategy with
#: no entry here is unsupported, and nothing falls back to another strategy.
CANDIDATE_ADAPTERS: Mapping[str, CandidateAdapter] = MappingProxyType({
    "care_purchase": _care,
    "fragrance_purchase": _fragrance,
    "supplement_purchase": _supplement,
})


def _unsupported(candidate: ShoppingCandidate, subject: DecisionSubject) -> dict[str, Any]:
    return _envelope(
        kind="candidate", strategy=None, category=candidate.category, subject=subject,
        identity=_candidate_identity(candidate, None),
        decision=_decision(STATE_UNSUPPORTED, reason=REASON_UNSUPPORTED_STRATEGY, authority=AUTHORITY_PURCHASE_OS),
        authorities=[{"authority": AUTHORITY_PURCHASE_OS, "status": REASON_UNSUPPORTED_STRATEGY}],
        memory=None, ownership=None, alternative=None, value=None, missing_information=[],
    )


async def candidate_purchase_check(
    session: AsyncSession,
    *,
    principal_account_id: uuid.UUID,
    account_id_str: str,
    candidate_id: uuid.UUID,
    decision_subject: DecisionSubject,
    plan_date: date | None = None,
) -> dict[str, Any]:
    """One purchase answer for a candidate, routed to exactly its own strategy.

    The candidate is loaded under the authenticated account (another account's
    candidate is the same not-found as an invented one). Its registered
    strategy picks the adapter; an unregistered category, or a registered
    strategy with no adapter, is unsupported — never Care by default.
    """
    candidate = await purchase_service.owned_purchase_candidate(session, principal_account_id, candidate_id)
    strategy = resolve_purchase_strategy(candidate.category)
    adapter = CANDIDATE_ADAPTERS.get(strategy.key) if strategy is not None else None
    if strategy is None or adapter is None:
        return _unsupported(candidate, decision_subject)
    if strategy.state not in {"active", "prohibited"}:
        return _unsupported(candidate, decision_subject)
    return await adapter(
        session, candidate=candidate, strategy=strategy,
        principal_account_id=principal_account_id, account_id_str=account_id_str,
        decision_subject=decision_subject, plan_date=plan_date,
    )


__all__ = [
    "CANDIDATE_ADAPTERS",
    "OFFICIAL_SOURCE",
    "PURCHASE_OS_CONTRACT_VERSION",
    "REASON_OFFICIAL_RECORD",
    "STATE_DECIDED",
    "STATE_NOT_ENOUGH_INFORMATION",
    "STATE_PROHIBITED",
    "STATE_UNSUPPORTED",
    "candidate_purchase_check",
    "compose_scan_decision",
    "official_ceiling",
    "scan_purchase_check",
]
