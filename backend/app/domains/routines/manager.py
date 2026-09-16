"""The Skin & Hair manager: one decision at a time, from what you already own.

The shelf engine in :mod:`.shelf` reports everything true about a person's Skin
Care and Hair Care products. That is a report, and a report is not a decision.
This module answers the narrower question the product actually promises:

    Given the products you own, what is the single most useful thing
    GlamGenius should decide for you right now?

It is a **queue with one thing at the front**, not a dashboard. Four rules shape
all of it:

* **Nothing new is invented.** Every decision comes from a
  :class:`~app.domains.routines.rules.Finding` produced by the reviewed rules
  engine and carries that finding's ``rule_id``, ``severity`` and
  ``evidence_note``. No model is consulted and nothing here is random.
* **Order is deterministic.** :func:`decision_rank` fixes the priority and
  :func:`sort_key` fixes every tie. The same shelf compiles to the same queue,
  byte for byte, every time. Money, price, brand and any commercial
  relationship are not inputs and never will be.
* **It gives things back.** A restriction this manager applied and the person
  accepted is offered back the moment the reason for it is gone — see
  :func:`give_back_decisions`. A manager that only ever takes products away is
  not a manager.
* **An override is free.** "Not now" is one tap, it is recorded, and it costs
  the person nothing: no score, no streak, no compliance flag, no escalation.
  The same unchanged decision does not ask again.

Scope is deliberately narrow: Skin Care (``beauty``) and Hair Care (``hair``)
products the person owns. Perfumes, supplements, food, cookware and everything
in the wardrobe categories are out of scope here by design.

Low confidence never warns
--------------------------
A low-confidence ingredient read produces exactly one decision — "Confirm this
first." It can never become an avoid decision, because
:func:`~app.domains.routines.rules.allergy_findings` only matches confirmed
ingredients. The manager inherits that, and
``tests/test_step10b_shelf_manager.py`` holds it up.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.product_preferences import (
    CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY,
    CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
    is_effective_user_pause,
    is_effective_user_preference,
)
from app.domains.inventory.models import InventoryAttribute, InventoryEvent
from app.domains.routines import rules as rules_engine
from app.domains.routines import shelf
from app.domains.routines.models import ShelfManagerDecisionEvent
from app.domains.routines.ontology import SEVERITY_AVOID, SEVERITY_CAUTION, SEVERITY_INFO, SLOT_BY_KEY
from app.domains.routines.rules import Finding, ShelfProduct
from app.domains.routines.shelf import ShelfContext

MANAGER_CONTRACT_VERSION = "step-10b-v1"

# The manager reasons over exactly the two care categories. This is not a
# convenience alias: widening it would pull perfumes, supplements or wardrobe
# items into a surface that has no reviewed rules for them.
MANAGER_CATEGORIES: tuple[str, ...] = shelf.ROUTINE_CATEGORIES

# --- The two kinds of thing in the queue -------------------------------------

KIND_FINDING = "finding"
KIND_GIVE_BACK = "give_back"
MANAGER_KINDS: tuple[str, ...] = (KIND_FINDING, KIND_GIVE_BACK)

# --- Actions: a closed list, deliberately ------------------------------------
# There is no "open this route" action carrying a server-supplied path. The app
# maps a kind to a screen; the server never hands it somewhere to navigate to.

ACTION_PAUSE_PRODUCT = "pause_product"
ACTION_RESUME_PRODUCT = "resume_product"
ACTION_PREFER_PRODUCT = "prefer_product"
ACTION_UNPREFER_PRODUCT = "unprefer_product"
ACTION_CONFIRM_LABEL = "confirm_label"
ACTION_RECORD_DATE = "record_date"
ACTION_ADD_OWNED_PRODUCT = "add_owned_product"
ACTION_OPEN_ROUTINE = "open_routine"
ACTION_OPEN_INVENTORY_ITEM = "open_inventory_item"
ACTION_NONE = "none"

ACTION_KINDS: tuple[str, ...] = (
    ACTION_PAUSE_PRODUCT,
    ACTION_RESUME_PRODUCT,
    ACTION_PREFER_PRODUCT,
    ACTION_UNPREFER_PRODUCT,
    ACTION_CONFIRM_LABEL,
    ACTION_RECORD_DATE,
    ACTION_ADD_OWNED_PRODUCT,
    ACTION_OPEN_ROUTINE,
    ACTION_OPEN_INVENTORY_ITEM,
    ACTION_NONE,
)

# Actions that change stored state. Each one is applied by the existing Care
# authority in ``service.py`` — this module never writes a preference itself.
MUTATING_ACTION_KINDS: frozenset[str] = frozenset({
    ACTION_PAUSE_PRODUCT,
    ACTION_RESUME_PRODUCT,
    ACTION_PREFER_PRODUCT,
    ACTION_UNPREFER_PRODUCT,
})

# Everything else takes the person to a screen. Accepting one of these is
# recorded, but it resolves nothing: the decision stays until the underlying
# fact changes. Marking a navigation as "done" would be a lie.
ROUTE_ONLY_ACTION_KINDS: frozenset[str] = frozenset(ACTION_KINDS) - MUTATING_ACTION_KINDS - {ACTION_NONE}

# --- What the person chose ---------------------------------------------------

CHOICE_ACCEPTED = "accepted"
CHOICE_OVERRIDDEN = "overridden"
CHOICE_RESTORED = "restored"
CHOICE_RESTORE_OVERRIDDEN = "restore_overridden"
MANAGER_CHOICES: tuple[str, ...] = (
    CHOICE_ACCEPTED, CHOICE_OVERRIDDEN, CHOICE_RESTORED, CHOICE_RESTORE_OVERRIDDEN,
)

# A choice that means "not this, not now". These are the ones that quieten a
# decision, and only while nothing material about it has changed.
DECLINED_CHOICES: frozenset[str] = frozenset({CHOICE_OVERRIDDEN, CHOICE_RESTORE_OVERRIDDEN})

# What the client is allowed to send. It says yes or no; the server works out
# which of the four stored values that is.
REQUEST_ACCEPT = "accept"
REQUEST_OVERRIDE = "override"
REQUEST_CHOICES: tuple[str, ...] = (REQUEST_ACCEPT, REQUEST_OVERRIDE)

_STORED_CHOICE: dict[tuple[str, str], str] = {
    (KIND_FINDING, REQUEST_ACCEPT): CHOICE_ACCEPTED,
    (KIND_FINDING, REQUEST_OVERRIDE): CHOICE_OVERRIDDEN,
    (KIND_GIVE_BACK, REQUEST_ACCEPT): CHOICE_RESTORED,
    (KIND_GIVE_BACK, REQUEST_OVERRIDE): CHOICE_RESTORE_OVERRIDDEN,
}

# --- The words -------------------------------------------------------------
# Short, imperative, and written once. Nothing here is generated, rewritten or
# personalised by a model. None of it blames anybody for owning a product.

DECISION_PAUSE_ALLERGY = "Pause this product."
DECISION_PAUSE_EXPIRED = "Pause this until you replace it."
DECISION_CONFIRM_LABEL = "Confirm this first."
DECISION_USE_NEXT = "Use this one next."
DECISION_RECORD_DATE = "Add the date on this pack."
DECISION_USE_BEFORE_REPLACING = "Use this before replacing it."
DECISION_USE_THESE_BEFORE_REPLACING = "Use these before replacing them."
DECISION_PICK_ONE = "Pick one for this step."
DECISION_KEEP_APART = "Keep these two apart."
DECISION_BRING_IT_BACK = "Bring it back."

OVERRIDE_NOT_NOW = "Not now"
OVERRIDE_KEEP_PAUSED = "Keep paused"

ACTION_LABELS: dict[str, str] = {
    ACTION_PAUSE_PRODUCT: "Pause it",
    ACTION_RESUME_PRODUCT: "Bring it back",
    ACTION_PREFER_PRODUCT: "Use this one next",
    ACTION_UNPREFER_PRODUCT: "Use my normal choice",
    ACTION_CONFIRM_LABEL: "Confirm the label",
    ACTION_RECORD_DATE: "Add the date",
    ACTION_ADD_OWNED_PRODUCT: "Add one you own",
    ACTION_OPEN_ROUTINE: "Open your routine",
    ACTION_OPEN_INVENTORY_ITEM: "Open this product",
    ACTION_NONE: "",
}

GIVE_BACK_EVIDENCE_NOTE = (
    "You paused this here, and the reason we gave has gone. It is yours to bring back."
)

EMPTY_QUEUE_MESSAGE = "Nothing needs deciding on your shelf right now."
NO_PRODUCTS_MESSAGE = "Add a Skin Care or Hair Care product and the manager has something to work with."


# --- Priority ----------------------------------------------------------------


def decision_rank(*, kind: str, rule_id: str, severity: str) -> int | None:
    """Where a decision sits in the queue, or ``None`` if it does not belong.

    The order is fixed here and nowhere else. Note that ``RULE_EXPIRED`` is
    checked **before** the general caution band even though it carries caution
    severity: a product past its date is a more concrete decision than a
    layering caution, and the order has to say so explicitly rather than let a
    severity string decide it.

    Returning ``None`` is how the manager stays narrow. A reviewed
    compatibility rule at ``info`` severity is worth reading on the shelf and is
    not a decision — it has no action, so it is not queued.
    """
    if kind == KIND_GIVE_BACK:
        return 0
    if severity == SEVERITY_AVOID:
        return 1
    if rule_id == rules_engine.RULE_EXPIRED:
        return 2
    if severity == SEVERITY_CAUTION:
        return 3
    if rule_id == rules_engine.RULE_UNCONFIRMED:
        return 4
    if rule_id == rules_engine.RULE_EXPIRING:
        return 5
    if rule_id == rules_engine.RULE_NO_EXPIRY:
        return 6
    if rule_id == rules_engine.RULE_LOW_USE:
        return 7
    if rule_id == rules_engine.RULE_DUPLICATE_SLOT:
        return 8
    if rule_id == rules_engine.RULE_MISSING_SLOT:
        return 9
    return None


def _slot_sort(slot: str | None) -> tuple[int, int, str]:
    """Canonical routine order for a slot, with unslotted decisions last."""
    spec = SLOT_BY_KEY.get(slot or "")
    return (0, spec.order, spec.key) if spec else (1, 0, "")


# --- The decision itself ------------------------------------------------------


@dataclass(frozen=True)
class ManagerAction:
    """What the one primary button does. ``kind`` is from :data:`ACTION_KINDS`."""

    kind: str
    inventory_item_id: str | None = None

    @property
    def label(self) -> str:
        return ACTION_LABELS[self.kind]

    @property
    def mutates(self) -> bool:
        return self.kind in MUTATING_ACTION_KINDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "inventory_item_id": self.inventory_item_id,
            "mutates": self.mutates,
        }


@dataclass(frozen=True)
class ManagerDecision:
    """One decision, with the reviewed rule it came from still attached."""

    decision_key: str
    kind: str
    category: str | None
    rule_id: str
    severity: str
    decision: str
    reason: str
    evidence_note: str
    item_ids: tuple[str, ...]
    slot: str | None
    action: ManagerAction
    override_label: str
    rank: int
    # Ordering only: how many days until the recorded date, counted from today.
    expiry_days: int | None = None
    # Material: the date itself, which only the person changes.
    expiry_on: date | None = None

    @property
    def fingerprint(self) -> str:
        """A hash of exactly the material inputs of this decision.

        Material means the facts the decision rests on and what it would do:
        the rule, how serious it is, which products it is about, which routine
        step, and what the button does. Change any of those and this is a
        different decision, which is how a stale client request is caught.

        Two things are deliberately **out**.

        *The rendered words.* They are derived from the inputs, not an input.
        Rewording a sentence should not make every person who already said "not
        now" get asked again.

        *Anything that moves with the clock.* ``expiry_days`` counts down every
        night; ``expiry_on`` is the date the person actually recorded and only
        changes when they change it. Hashing the countdown would re-ask about a
        product that is running out once a day, every day, which is exactly the
        nagging an override is supposed to stop.
        """
        payload = {
            "contract_version": MANAGER_CONTRACT_VERSION,
            "decision_key": self.decision_key,
            "kind": self.kind,
            "category": self.category,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "item_ids": sorted(self.item_ids),
            "slot": self.slot,
            "action_kind": self.action.kind,
            "action_item_id": self.action.inventory_item_id,
            "expiry_on": self.expiry_on.isoformat() if self.expiry_on is not None else None,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def sort_key(self) -> tuple:
        """Total order. No random, no database order, no insertion order."""
        return (
            self.rank,
            (0, self.expiry_days) if self.expiry_days is not None else (1, 0),
            self.category or "",
            _slot_sort(self.slot),
            tuple(sorted(self.item_ids)),
            self.rule_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_key": self.decision_key,
            "decision_fingerprint": self.fingerprint,
            "kind": self.kind,
            "category": self.category,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "decision": self.decision,
            "reason": self.reason,
            "evidence_note": self.evidence_note,
            "item_ids": list(self.item_ids),
            "slot": self.slot,
            "action": self.action.as_dict(),
            "override": {"label": self.override_label, "choice": REQUEST_OVERRIDE},
        }


# --- Turning findings into decisions -----------------------------------------


# A decision key is stored, indexed and sent back by the client, so it has to
# stay comfortably inside the column that holds it however many products a
# decision names. One product and a pair are written out in full — those are
# the forms worth reading in a log. Beyond that the sorted ids are counted and
# digested, which is bounded, still changes the moment the set of products
# changes, and never truncates an identifier into ambiguity.
_INLINE_ITEM_LIMIT = 2
DECISION_KEY_MAX_LENGTH = 200


def _item_key(rule_id: str, item_ids: Sequence[str]) -> str:
    ordered = sorted(item_ids)
    if len(ordered) == 1:
        return f"{rule_id}:item:{ordered[0]}"
    if len(ordered) <= _INLINE_ITEM_LIMIT:
        return f"{rule_id}:items:{'+'.join(ordered)}"
    digest = hashlib.sha256("+".join(ordered).encode("utf-8")).hexdigest()[:32]
    return f"{rule_id}:items:{len(ordered)}:{digest}"


def _slot_key(rule_id: str, category: str, slot: str | None) -> str:
    return f"{rule_id}:{category}:{slot or 'unassigned'}"


def _nearest_expiry(
    item_ids: Sequence[str], products: dict[str, ShelfProduct], today: date
) -> tuple[int | None, date | None]:
    """The soonest recorded date across these products, as a date and a count."""
    dates = [
        products[item_id].effective_expiry
        for item_id in item_ids
        if item_id in products and products[item_id].effective_expiry is not None
    ]
    if not dates:
        return None, None
    soonest = min(dates)
    return (soonest - today).days, soonest


def _finding_decision(
    finding: Finding,
    *,
    category: str,
    products: dict[str, ShelfProduct],
    today: date,
    blocked_item_ids: frozenset[uuid.UUID],
) -> ManagerDecision | None:
    """One reviewed finding, expressed as a decision — or nothing.

    Returning ``None`` is normal. A finding the manager has no honest action for
    stays on the shelf report where it belongs.
    """
    rank = decision_rank(kind=KIND_FINDING, rule_id=finding.rule_id, severity=finding.severity)
    if rank is None:
        return None

    item_ids = tuple(sorted(finding.item_ids))
    single = products.get(item_ids[0]) if len(item_ids) == 1 else None

    # Two rules speak about a routine step rather than a product. Every other
    # rule names at least one product, and a decision about no product at all
    # is not a decision — it would key on nothing and act on nothing.
    slot_shaped = finding.rule_id in (rules_engine.RULE_MISSING_SLOT, rules_engine.RULE_DUPLICATE_SLOT)
    if not item_ids and not slot_shaped:
        return None

    if finding.rule_id == rules_engine.RULE_MISSING_SLOT:
        spec = SLOT_BY_KEY.get(finding.slot or "")
        if spec is None:
            return None
        # "Add one you already own", never "buy one". The manager does not shop.
        decision, action = f"Add a {spec.label.lower()} you already own.", ManagerAction(ACTION_ADD_OWNED_PRODUCT)
        key, reason = _slot_key(finding.rule_id, category, finding.slot), finding.headline
    elif finding.rule_id == rules_engine.RULE_DUPLICATE_SLOT:
        decision, action = DECISION_PICK_ONE, ManagerAction(ACTION_OPEN_ROUTINE)
        key, reason = _slot_key(finding.rule_id, category, finding.slot), finding.headline
    elif finding.rule_id == rules_engine.RULE_LOW_USE:
        # Named plainly. Never "wasted", never a number with a currency on it.
        if single is not None:
            if not can_ask_to_use(single, blocked_item_ids):
                return None
            decision = DECISION_USE_BEFORE_REPLACING
            action = ManagerAction(ACTION_PREFER_PRODUCT, item_ids[0])
        else:
            # One reviewed finding names the whole group, so the manager can
            # only speak about the whole group. If Care would refuse any one of
            # them, "use these" is false about that one, and there is no honest
            # way to say it while the finding covers them all. Fail closed for
            # the group rather than quietly redrawing what the finding said.
            if any(
                product_id in products and products[product_id].item.id in blocked_item_ids
                for product_id in item_ids
            ):
                return None
            decision, action = DECISION_USE_THESE_BEFORE_REPLACING, ManagerAction(ACTION_OPEN_ROUTINE)
        key, reason = _item_key(finding.rule_id, item_ids), finding.detail
    elif len(item_ids) != 1:
        # Every remaining rule speaks about one product, except a reviewed
        # compatibility rule, which speaks about a pair. Choosing which of the
        # two to drop is the person's call, not ours.
        decision, action = DECISION_KEEP_APART, ManagerAction(ACTION_OPEN_ROUTINE)
        key, reason = _item_key(finding.rule_id, item_ids), finding.detail
    elif single is None:
        return None
    elif finding.severity == SEVERITY_AVOID:
        decision = DECISION_PAUSE_ALLERGY
        action = ManagerAction(ACTION_PAUSE_PRODUCT, item_ids[0])
        key, reason = _item_key(finding.rule_id, item_ids), finding.headline
    elif finding.rule_id == rules_engine.RULE_EXPIRED:
        decision = DECISION_PAUSE_EXPIRED
        action = ManagerAction(ACTION_PAUSE_PRODUCT, item_ids[0])
        key, reason = _item_key(finding.rule_id, item_ids), finding.headline
    elif finding.rule_id == rules_engine.RULE_UNCONFIRMED:
        decision = DECISION_CONFIRM_LABEL
        action = ManagerAction(ACTION_CONFIRM_LABEL, item_ids[0])
        key, reason = _item_key(finding.rule_id, item_ids), finding.headline
    elif finding.rule_id == rules_engine.RULE_EXPIRING:
        if not can_ask_to_use(single, blocked_item_ids):
            return None
        decision = DECISION_USE_NEXT
        action = ManagerAction(ACTION_PREFER_PRODUCT, item_ids[0])
        key, reason = _item_key(finding.rule_id, item_ids), finding.headline
    elif finding.rule_id == rules_engine.RULE_NO_EXPIRY:
        decision = DECISION_RECORD_DATE
        action = ManagerAction(ACTION_RECORD_DATE, item_ids[0])
        key, reason = _item_key(finding.rule_id, item_ids), finding.headline
    else:
        # A reviewed caution about a single product with no action we can take.
        decision, action = DECISION_KEEP_APART, ManagerAction(ACTION_OPEN_ROUTINE)
        key, reason = _item_key(finding.rule_id, item_ids), finding.detail

    expiry_days, expiry_on = _nearest_expiry(item_ids, products, today)
    return ManagerDecision(
        decision_key=key,
        kind=KIND_FINDING,
        category=category,
        rule_id=finding.rule_id,
        severity=finding.severity,
        decision=decision,
        reason=reason,
        evidence_note=finding.evidence_note,
        item_ids=item_ids,
        slot=finding.slot,
        action=action,
        override_label=OVERRIDE_NOT_NOW,
        rank=rank,
        expiry_days=expiry_days,
        expiry_on=expiry_on,
    )


def preference_blocked_item_ids(
    products: Sequence[ShelfProduct],
    *,
    allergies: Sequence[str],
    today: date,
    paused_item_ids: frozenset[uuid.UUID],
) -> frozenset[uuid.UUID]:
    """Products the Care authority would refuse to make preferred.

    These are exactly the three blocking reasons
    :mod:`app.domains.care.decisions` uses — past its date, a confirmed match
    against a declared allergy, and explicitly paused — computed from the same
    helpers rather than a second opinion about what they mean. It is repeated
    here only because ``prefer_care_product`` needs a whole Care context to
    answer, and this runs on every read of the shelf.
    """
    blocked = set(paused_item_ids)
    for product in products:
        days = product.days_to_expiry(today)
        if days is not None and days < 0:
            blocked.add(product.item.id)
            continue
        match = rules_engine.allergy_product_matches((product,), allergies)[0]
        if match.confirmed_ingredient_keys:
            blocked.add(product.item.id)
    return frozenset(blocked)


def can_ask_to_use(product: ShelfProduct, blocked_item_ids: frozenset[uuid.UUID]) -> bool:
    """May the manager tell somebody to use this product?

    Only where the Care authority would accept making it the product for its
    step. ``prefer_care_product`` refuses a product with no confirmed routine
    role, one that is paused, one past its date and one carrying a confirmed
    match against a declared allergy — and refusing is right in every one of
    those cases.

    The sentence is the decision, not the button. Softening the button to
    "open this product" while still saying *use this* would leave the manager
    telling somebody to use a product it has itself just decided they should
    not. Where it cannot honestly ask, it says nothing: the reviewed finding
    stays on the shelf report, where it is information rather than an
    instruction. Narrow beats contradictory.
    """
    return product.slot is not None and product.item.id not in blocked_item_ids


# --- Giving things back -------------------------------------------------------


@dataclass(frozen=True)
class GiveBackCandidate:
    """A product this manager paused, which the person accepted pausing."""

    item_id: uuid.UUID
    rule_id: str
    display_name: str
    category: str
    slot: str | None


def give_back_decisions(
    candidates: Sequence[GiveBackCandidate],
    *,
    products: dict[str, ShelfProduct],
    today: date,
    still_restricted_item_ids: frozenset[uuid.UUID],
) -> list[ManagerDecision]:
    """Offer back what the manager took away, once the reason has gone.

    A candidate is only offered where the pause is still in place, the product
    is still on the shelf, and nothing active would pause it again. The reverse
    action exists already — ``resume_care_product`` — which is why pausing is
    the only restriction this manager is willing to apply.
    """
    decisions: list[ManagerDecision] = []
    for candidate in candidates:
        if candidate.item_id in still_restricted_item_ids:
            continue
        item_id = str(candidate.item_id)
        if item_id not in products:
            continue
        expiry_days, expiry_on = _nearest_expiry((item_id,), products, today)
        decisions.append(ManagerDecision(
            decision_key=f"manager.give_back:item:{item_id}",
            kind=KIND_GIVE_BACK,
            category=candidate.category,
            rule_id=candidate.rule_id,
            severity=SEVERITY_INFO,
            decision=DECISION_BRING_IT_BACK,
            reason=f"{candidate.display_name} can return.",
            evidence_note=GIVE_BACK_EVIDENCE_NOTE,
            item_ids=(item_id,),
            slot=candidate.slot,
            action=ManagerAction(ACTION_RESUME_PRODUCT, item_id),
            override_label=OVERRIDE_KEEP_PAUSED,
            rank=0,
            expiry_days=expiry_days,
            expiry_on=expiry_on,
        ))
    return decisions


# --- The queue ----------------------------------------------------------------


@dataclass
class ManagerQueue:
    """Everything one compile produced, active and suppressed alike."""

    active: list[ManagerDecision] = field(default_factory=list)
    overridden: list[ManagerDecision] = field(default_factory=list)
    has_products: bool = False

    @property
    def primary(self) -> ManagerDecision | None:
        return self.active[0] if self.active else None

    def find(self, decision_key: str) -> ManagerDecision | None:
        return next((row for row in self.active if row.decision_key == decision_key), None)

    def as_dict(self) -> dict[str, Any]:
        primary = self.primary
        return {
            "contract_version": MANAGER_CONTRACT_VERSION,
            "primary": primary.as_dict() if primary else None,
            "remaining_count": max(len(self.active) - 1, 0),
            "counts": {
                "active": len(self.active),
                "overridden": len(self.overridden),
                "give_back": sum(1 for row in self.active if row.kind == KIND_GIVE_BACK),
            },
            "message": self._message(),
        }

    def _message(self) -> str | None:
        if self.primary is not None:
            return None
        return EMPTY_QUEUE_MESSAGE if self.has_products else NO_PRODUCTS_MESSAGE


def _already_done(decision: ManagerDecision, paused: frozenset[uuid.UUID], preferred: frozenset[uuid.UUID]) -> bool:
    """Is the stored state already what this decision would produce?

    Only a state-changing action can be satisfied this way. A navigation is
    never "already done" — the person may have opened the screen and changed
    nothing, and pretending otherwise would quietly drop a real decision.
    """
    if decision.action.inventory_item_id is None:
        return False
    item_id = uuid.UUID(decision.action.inventory_item_id)
    if decision.action.kind == ACTION_PAUSE_PRODUCT:
        return item_id in paused
    if decision.action.kind == ACTION_RESUME_PRODUCT:
        return item_id not in paused
    if decision.action.kind == ACTION_PREFER_PRODUCT:
        return item_id in preferred
    if decision.action.kind == ACTION_UNPREFER_PRODUCT:
        return item_id not in preferred
    return False


def compile_queue(
    context: ShelfContext,
    *,
    paused_item_ids: frozenset[uuid.UUID],
    preferred_item_ids: frozenset[uuid.UUID],
    give_back_candidates: Sequence[GiveBackCandidate] = (),
    declined: dict[str, str] | None = None,
) -> ManagerQueue:
    """Compile the whole queue from one shelf context. Pure and deterministic.

    ``declined`` maps a decision key to the fingerprint the person last said no
    to. A decision whose fingerprint still matches is not shown again; one whose
    material inputs have changed is, because it is no longer the same decision.
    """
    declined = declined or {}
    by_category = {category: shelf.build(context, category) for category in MANAGER_CATEGORIES}
    products: dict[str, ShelfProduct] = {
        product.id: product for built in by_category.values() for product in built
    }
    raw: list[ManagerDecision] = []
    blocked = preference_blocked_item_ids(
        list(products.values()),
        allergies=context.allergies,
        today=context.today,
        paused_item_ids=paused_item_ids,
    )

    for category, built in by_category.items():
        for finding in shelf.findings_for(built, category, context):
            # A required step is only missing from a routine that exists. Telling
            # somebody who has recorded no hair products at all that they have
            # no shampoo is not a decision about their routine — they have not
            # started one. The shelf report still says it; the manager does not.
            if finding.rule_id == rules_engine.RULE_MISSING_SLOT and not built:
                continue
            decision = _finding_decision(
                finding,
                category=category,
                products=products,
                today=context.today,
                blocked_item_ids=blocked,
            )
            if decision is not None:
                raw.append(decision)

    # A product something would still pause is not a product to offer back.
    still_restricted = frozenset(
        uuid.UUID(row.action.inventory_item_id)
        for row in raw
        if row.action.kind == ACTION_PAUSE_PRODUCT and row.action.inventory_item_id is not None
    )
    raw.extend(give_back_decisions(
        give_back_candidates,
        products=products,
        today=context.today,
        still_restricted_item_ids=still_restricted,
    ))

    queue = ManagerQueue(has_products=bool(products))
    for decision in sorted(raw, key=lambda row: row.sort_key):
        if _already_done(decision, paused_item_ids, preferred_item_ids):
            continue
        if declined.get(decision.decision_key) == decision.fingerprint:
            queue.overridden.append(decision)
            continue
        queue.active.append(decision)
    return queue


# --- Reading the stored state -------------------------------------------------


async def care_preference_state(
    session: AsyncSession, item_ids: Sequence[uuid.UUID]
) -> tuple[frozenset[uuid.UUID], frozenset[uuid.UUID]]:
    """Current paused and preferred products, read the one canonical way."""
    if not item_ids:
        return frozenset(), frozenset()
    rows = (await session.execute(
        select(InventoryAttribute).where(
            InventoryAttribute.item_id.in_(tuple(item_ids)),
            InventoryAttribute.key.in_((
                CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY, CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY,
            )),
        )
    )).scalars().all()
    paused = frozenset(
        row.item_id for row in rows
        if row.key == CARE_ROUTINE_PAUSED_ATTRIBUTE_KEY and is_effective_user_pause(
            value=row.value, source=row.source, verification_state=row.verification_state,
        )
    )
    preferred = frozenset(
        row.item_id for row in rows
        if row.key == CARE_ROUTINE_PREFERRED_ATTRIBUTE_KEY and is_effective_user_preference(
            value=row.value, source=row.source, verification_state=row.verification_state,
        )
    )
    return paused, preferred


async def declined_fingerprints(session: AsyncSession, account_id: uuid.UUID) -> dict[str, str]:
    """The last answer for every decision key this account has responded to.

    Only the latest answer counts, and only a "no" quietens anything. Saying no
    once and then yes later must not leave the decision silenced.
    """
    rows = (await session.execute(
        select(ShelfManagerDecisionEvent)
        .where(ShelfManagerDecisionEvent.account_id == account_id)
        .order_by(ShelfManagerDecisionEvent.created_at, ShelfManagerDecisionEvent.id)
    )).scalars().all()
    latest: dict[str, ShelfManagerDecisionEvent] = {row.decision_key: row for row in rows}
    return {
        key: row.decision_fingerprint
        for key, row in latest.items()
        if row.choice in DECLINED_CHOICES
    }


def _rule_id_from_key(decision_key: str) -> str | None:
    """Recover the reviewed rule a stored decision key came from.

    Keys are built here and nowhere else, so this always resolves for a key we
    wrote. Anything that does not resolve to a reviewed rule is dropped rather
    than guessed at.
    """
    for separator in (":item:", ":items:", ":"):
        candidate = decision_key.split(separator)[0]
        if candidate in rules_engine.all_rule_ids():
            return candidate
    return None


async def give_back_candidates(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    products: dict[str, ShelfProduct],
    paused_item_ids: frozenset[uuid.UUID],
) -> list[GiveBackCandidate]:
    """Products this manager paused that the person could have back.

    Two things disqualify a candidate, and both are about respecting a choice
    the person made themselves:

    * the pause is no longer in place — they already resumed it, so there is
      nothing to give back;
    * the pause currently in place is **newer** than the manager's, which means
      they paused it themselves afterwards. Offering to undo that would be the
      manager overruling them.
    """
    rows = (await session.execute(
        select(ShelfManagerDecisionEvent)
        .where(
            ShelfManagerDecisionEvent.account_id == account_id,
            ShelfManagerDecisionEvent.choice == CHOICE_ACCEPTED,
            ShelfManagerDecisionEvent.action_kind == ACTION_PAUSE_PRODUCT,
            ShelfManagerDecisionEvent.target_inventory_item_id.is_not(None),
        )
        .order_by(ShelfManagerDecisionEvent.created_at, ShelfManagerDecisionEvent.id)
    )).scalars().all()
    accepted: dict[uuid.UUID, ShelfManagerDecisionEvent] = {
        row.target_inventory_item_id: row for row in rows if row.target_inventory_item_id is not None
    }
    accepted = {item_id: row for item_id, row in accepted.items() if item_id in paused_item_ids}
    if not accepted:
        return []

    pause_events = (await session.execute(
        select(InventoryEvent).where(
            InventoryEvent.account_id == account_id,
            InventoryEvent.item_id.in_(tuple(accepted)),
            InventoryEvent.event_type == "care_routine_paused",
        ).order_by(InventoryEvent.created_at, InventoryEvent.id)
    )).scalars().all()
    latest_pause: dict[uuid.UUID, InventoryEvent] = {
        row.item_id: row for row in pause_events if row.item_id is not None
    }

    candidates: list[GiveBackCandidate] = []
    for item_id, event in accepted.items():
        product = products.get(str(item_id))
        if product is None:
            continue
        rule_id = _rule_id_from_key(event.decision_key)
        if rule_id is None:
            continue
        pause_event = latest_pause.get(item_id)
        if pause_event is not None and pause_event.created_at > event.created_at:
            continue
        candidates.append(GiveBackCandidate(
            item_id=item_id,
            rule_id=rule_id,
            display_name=product.item.display_name,
            category=product.item.category,
            slot=product.slot,
        ))
    return sorted(candidates, key=lambda row: (row.category, str(row.item_id)))


async def build_queue(
    session: AsyncSession, *, account_id: uuid.UUID, today: date | None = None
) -> ManagerQueue:
    """Read everything the manager is allowed to see, then compile."""
    context = await shelf.gather(session, account_id=account_id, today=today)
    products: dict[str, ShelfProduct] = {}
    for category in MANAGER_CATEGORIES:
        for product in shelf.build(context, category):
            products[product.id] = product

    item_ids = [product.item.id for product in products.values()]
    paused, preferred = await care_preference_state(session, item_ids)
    candidates = await give_back_candidates(
        session, account_id=account_id, products=products, paused_item_ids=paused,
    )
    return compile_queue(
        context,
        paused_item_ids=paused,
        preferred_item_ids=preferred,
        give_back_candidates=candidates,
        declined=await declined_fingerprints(session, account_id),
    )


def stored_choice_for(kind: str, requested: str) -> str:
    """Map what the client asked for onto what is stored. Server-derived only."""
    return _STORED_CHOICE[(kind, requested)]


__all__ = [
    "ACTION_KINDS",
    "MANAGER_CATEGORIES",
    "MANAGER_CHOICES",
    "MANAGER_CONTRACT_VERSION",
    "MANAGER_KINDS",
    "MUTATING_ACTION_KINDS",
    "REQUEST_CHOICES",
    "ROUTE_ONLY_ACTION_KINDS",
    "GiveBackCandidate",
    "ManagerAction",
    "ManagerDecision",
    "ManagerQueue",
    "build_queue",
    "compile_queue",
    "decision_rank",
    "give_back_candidates",
    "give_back_decisions",
    "can_ask_to_use",
    "preference_blocked_item_ids",
    "stored_choice_for",
]
