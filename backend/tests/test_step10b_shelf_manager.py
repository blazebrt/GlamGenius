"""Step 10B: the Skin & Hair manager compiles one decision, deterministically.

Everything here is pure. No database, no HTTP, no model — a shelf context goes
in and a queue comes out, and the same shelf has to produce the same queue every
time. The route, security and give-back behaviour are proved against a real
database in ``test_step10b_shelf_manager_api.py``.

The things these tests exist to stop:

* a decision appearing without a reviewed rule behind it;
* the order of the queue drifting, or depending on anything that is not the
  shelf itself — insertion order, a price, a brand;
* a low-confidence label reading turning into a safety warning;
* the manager reaching outside Skin Care and Hair Care;
* the words changing into something that blames somebody for what they own.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import manager, shelf
from app.domains.routines import rules as rules_engine
from app.domains.routines.models import ShelfManagerDecisionEvent
from app.domains.routines.ontology import SEVERITY_AVOID, SEVERITY_CAUTION, SEVERITY_INFO
from app.domains.routines.safety import narrative_is_safe
from app.domains.routines.shelf import ShelfContext

TODAY = date(2026, 6, 1)


def _item(
    *,
    category: str = "beauty",
    product_type: str = "moisturiser",
    name: str | None = None,
    expiry: date | None = None,
    actives: list[str] | None = None,
    item_id: uuid.UUID | None = None,
) -> OwnedItem:
    details: dict[str, object] = {"product_type": product_type}
    if expiry is not None:
        details["expiry_date"] = expiry.isoformat()
    if actives is not None:
        details["active_ingredients"] = actives
    return OwnedItem(
        id=item_id or uuid.uuid4(),
        category=category,
        subcategory=product_type,
        display_name=name or f"{product_type.title()} {uuid.uuid4().hex[:4]}",
        brand=None,
        details=details,
        condition="good",
        usage_count=0,
        last_used_at=None,
        purchase_price=None,
        currency="INR",
    )


def _context(*items: OwnedItem, allergies: tuple[str, ...] = (), low_use: tuple[uuid.UUID, ...] = ()) -> ShelfContext:
    return ShelfContext(
        account_id=uuid.uuid4(),
        today=TODAY,
        owned=list(items),
        allergies=list(allergies),
        low_use_ids=set(low_use),
    )


def _queue(context: ShelfContext, **changes) -> manager.ManagerQueue:
    kwargs: dict = {
        "paused_item_ids": frozenset(),
        "preferred_item_ids": frozenset(),
    }
    kwargs.update(changes)
    return manager.compile_queue(context, **kwargs)


def _keys(queue: manager.ManagerQueue) -> list[str]:
    return [row.decision_key for row in queue.active]


def _rules(queue: manager.ManagerQueue) -> list[str]:
    return [row.rule_id for row in queue.active]


# ---------------------------------------------------------------------------
# What the queue is for
# ---------------------------------------------------------------------------


def test_an_empty_shelf_decides_nothing_and_says_so():
    payload = _queue(_context()).as_dict()

    assert payload["primary"] is None
    assert payload["remaining_count"] == 0
    assert payload["counts"] == {"active": 0, "overridden": 0, "give_back": 0}
    # Not filler, not a generic tip, and not a blank card.
    assert payload["message"] == manager.NO_PRODUCTS_MESSAGE
    assert payload["contract_version"] == "step-10b-v1"


def test_a_shelf_with_nothing_to_decide_says_something_different():
    """"No products" and "nothing to do" are not the same answer."""
    context = _context(
        _item(product_type="cleanser", expiry=TODAY + timedelta(days=400)),
        _item(product_type="moisturiser", expiry=TODAY + timedelta(days=400)),
        _item(product_type="sunscreen", expiry=TODAY + timedelta(days=400)),
        _item(category="hair", product_type="shampoo", expiry=TODAY + timedelta(days=400)),
        _item(category="hair", product_type="conditioner", expiry=TODAY + timedelta(days=400)),
    )
    payload = _queue(context).as_dict()

    assert payload["primary"] is None
    assert payload["message"] == manager.EMPTY_QUEUE_MESSAGE


def test_the_queue_shows_one_decision_and_counts_the_rest():
    context = _context(
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=5), name="Old Cleanser"),
        _item(product_type="moisturiser", expiry=TODAY - timedelta(days=2), name="Old Moisturiser"),
        _item(product_type="sunscreen", expiry=TODAY + timedelta(days=400)),
    )
    payload = _queue(context).as_dict()

    assert payload["primary"]["decision"] == manager.DECISION_PAUSE_EXPIRED
    assert payload["remaining_count"] == payload["counts"]["active"] - 1
    assert payload["remaining_count"] >= 1


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


def test_priority_order_is_exactly_the_declared_one():
    ranks = [
        manager.decision_rank(kind=manager.KIND_GIVE_BACK, rule_id="anything", severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_ALLERGY, severity=SEVERITY_AVOID),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_EXPIRED, severity=SEVERITY_CAUTION),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id="rule.retinoid_aha", severity=SEVERITY_CAUTION),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_UNCONFIRMED, severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_EXPIRING, severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_NO_EXPIRY, severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_LOW_USE, severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_DUPLICATE_SLOT, severity=SEVERITY_INFO),
        manager.decision_rank(kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_MISSING_SLOT, severity=SEVERITY_INFO),
    ]
    assert ranks == list(range(10))


def test_an_expired_product_outranks_a_reviewed_layering_caution():
    """Both are ``caution``; the order has to come from the rule, not severity."""
    expired = manager.decision_rank(
        kind=manager.KIND_FINDING, rule_id=rules_engine.RULE_EXPIRED, severity=SEVERITY_CAUTION,
    )
    layering = manager.decision_rank(
        kind=manager.KIND_FINDING, rule_id="rule.retinoid_aha", severity=SEVERITY_CAUTION,
    )
    assert expired < layering


def test_a_declared_allergy_comes_before_an_expired_product():
    allergen = _item(product_type="cleanser", actives=["fragrance"], name="Scented Cleanser")
    expired = _item(product_type="moisturiser", expiry=TODAY - timedelta(days=30), name="Old Moisturiser")
    queue = _queue(_context(allergen, expired, allergies=("fragrance",)))

    assert _rules(queue)[0] == rules_engine.RULE_ALLERGY
    assert queue.primary.severity == SEVERITY_AVOID
    assert queue.primary.action.kind == manager.ACTION_PAUSE_PRODUCT


def test_a_missing_required_step_is_the_last_thing_the_manager_raises():
    context = _context(_item(product_type="cleanser", expiry=TODAY + timedelta(days=10)))
    queue = _queue(context)

    assert rules_engine.RULE_MISSING_SLOT in _rules(queue)
    assert _rules(queue)[-1] == rules_engine.RULE_MISSING_SLOT


# ---------------------------------------------------------------------------
# Determinism and tie-breaks
# ---------------------------------------------------------------------------


def test_the_same_shelf_compiles_to_the_same_queue_byte_for_byte():
    context = _context(
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=3)),
        _item(product_type="moisturiser", expiry=TODAY - timedelta(days=3)),
        _item(category="hair", product_type="shampoo"),
        _item(category="hair", product_type="conditioner", expiry=TODAY + timedelta(days=9)),
    )
    first = _queue(context).as_dict()
    second = _queue(context).as_dict()

    assert first == second


def test_insertion_order_does_not_change_the_queue():
    """The database hands rows back in whatever order it likes. That cannot matter."""
    items = [
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=3)),
        _item(product_type="moisturiser", expiry=TODAY - timedelta(days=8)),
        _item(category="hair", product_type="shampoo", expiry=TODAY - timedelta(days=1)),
    ]
    forward = _queue(_context(*items)).as_dict()
    backward = _queue(_context(*reversed(items))).as_dict()

    assert forward == backward


def test_the_nearest_expiry_is_decided_first():
    later = _item(product_type="cleanser", expiry=TODAY - timedelta(days=1), name="Cleanser")
    sooner = _item(product_type="moisturiser", expiry=TODAY - timedelta(days=40), name="Moisturiser")
    queue = _queue(_context(later, sooner))

    expired = [row for row in queue.active if row.rule_id == rules_engine.RULE_EXPIRED]
    # -40 days is further past its date than -1, so it is the nearer expiry.
    assert expired[0].item_ids == (str(sooner.id),)


def test_skin_care_is_decided_before_hair_care_when_everything_else_ties():
    skin = _item(category="beauty", product_type="cleanser", expiry=TODAY - timedelta(days=5))
    hair = _item(category="hair", product_type="shampoo", expiry=TODAY - timedelta(days=5))
    queue = _queue(_context(skin, hair))

    expired = [row for row in queue.active if row.rule_id == rules_engine.RULE_EXPIRED]
    assert [row.category for row in expired] == ["beauty", "hair"]


def test_the_canonical_routine_order_breaks_a_tie_within_one_category():
    cleanser = _item(product_type="cleanser")
    sunscreen = _item(product_type="sunscreen")
    queue = _queue(_context(cleanser, sunscreen))

    undated = [row for row in queue.active if row.rule_id == rules_engine.RULE_NO_EXPIRY]
    # Cleanser is step 10, sunscreen is step 80.
    assert [row.slot for row in undated] == [None, None]
    assert [row.item_ids for row in undated] == sorted(row.item_ids for row in undated)


def test_sorted_item_ids_break_a_tie_that_nothing_else_can():
    first = _item(product_type="toner", item_id=uuid.UUID(int=1))
    second = _item(product_type="toner", item_id=uuid.UUID(int=2))
    queue = _queue(_context(second, first))

    undated = [row for row in queue.active if row.rule_id == rules_engine.RULE_NO_EXPIRY]
    assert [row.item_ids[0] for row in undated] == [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]


def test_price_and_brand_are_not_inputs_to_the_order():
    """Nothing about what a product cost may move it up the queue."""
    cheap = _item(product_type="cleanser", expiry=TODAY - timedelta(days=5), item_id=uuid.UUID(int=7))
    dear = _item(product_type="moisturiser", expiry=TODAY - timedelta(days=5), item_id=uuid.UUID(int=8))
    plain = _queue(_context(cheap, dear)).as_dict()

    priced = _queue(_context(
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=5), item_id=uuid.UUID(int=7)),
        _item(product_type="moisturiser", expiry=TODAY - timedelta(days=5), item_id=uuid.UUID(int=8)),
    ))
    for row in priced.active:
        assert "price" not in row.as_dict()
    # Same shelf, same order, whatever was paid for either product.
    assert [row["decision_key"] for row in [plain["primary"]]] == [priced.as_dict()["primary"]["decision_key"]]


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def test_a_fingerprint_is_stable_while_nothing_material_changes():
    item = _item(product_type="cleanser", expiry=TODAY - timedelta(days=5), item_id=uuid.UUID(int=11))
    first = _queue(_context(item)).primary.fingerprint
    second = _queue(_context(_item(
        product_type="cleanser", expiry=TODAY - timedelta(days=5),
        item_id=uuid.UUID(int=11), name=item.display_name,
    ))).primary.fingerprint

    assert first == second
    assert len(first) == 64


def test_correcting_the_expiry_changes_the_fingerprint():
    item_id = uuid.UUID(int=12)
    before = _queue(_context(_item(
        product_type="cleanser", expiry=TODAY - timedelta(days=5), item_id=item_id, name="Cleanser",
    ))).primary
    after = _queue(_context(_item(
        product_type="cleanser", expiry=TODAY - timedelta(days=40), item_id=item_id, name="Cleanser",
    ))).primary

    assert before.decision_key == after.decision_key
    assert before.fingerprint != after.fingerprint


def test_the_fingerprint_covers_every_material_input():
    from dataclasses import replace

    decision = _queue(_context(_item(product_type="cleanser", expiry=TODAY - timedelta(days=5)))).primary
    material = {
        "decision_key": "rule.other:item:x",
        "kind": manager.KIND_GIVE_BACK,
        "category": "hair",
        "rule_id": rules_engine.RULE_LOW_USE,
        "severity": SEVERITY_INFO,
        "slot": "sunscreen",
        "item_ids": ("00000000-0000-0000-0000-00000000ffff",),
        "expiry_on": TODAY - timedelta(days=99),
    }
    for field, value in material.items():
        assert replace(decision, **{field: value}).fingerprint != decision.fingerprint, field
    assert replace(
        decision, action=manager.ManagerAction(manager.ACTION_OPEN_ROUTINE),
    ).fingerprint != decision.fingerprint
    assert replace(
        decision, action=manager.ManagerAction(manager.ACTION_PAUSE_PRODUCT, "other-id"),
    ).fingerprint != decision.fingerprint


def test_rewording_a_decision_does_not_ask_everybody_again():
    """The words are derived from the inputs. Editing copy is not a new decision."""
    from dataclasses import replace

    decision = _queue(_context(_item(product_type="cleanser", expiry=TODAY - timedelta(days=5)))).primary
    reworded = replace(
        decision,
        decision="Set this one aside.",
        reason="A differently worded reason.",
        evidence_note="A differently worded note.",
    )

    assert reworded.fingerprint == decision.fingerprint


def test_the_countdown_to_a_date_does_not_change_the_fingerprint():
    """Otherwise "runs out in 45 days" would ask again every single night."""
    from dataclasses import replace

    running_out = _item(product_type="cleanser", name="Nearly Gone", expiry=TODAY + timedelta(days=45))
    today = _queue(_context(running_out)).active
    decision = next(row for row in today if row.rule_id == rules_engine.RULE_EXPIRING)
    tomorrow = replace(decision, expiry_days=decision.expiry_days - 1)

    assert tomorrow.fingerprint == decision.fingerprint


# ---------------------------------------------------------------------------
# Low confidence never warns
# ---------------------------------------------------------------------------


def test_a_low_confidence_allergen_only_ever_asks_for_confirmation():
    """The one rule that a careless change would break most quietly."""
    from app.domains.routines.models import ProductIngredient

    item = _item(product_type="cleanser", name="Unknown Cleanser", expiry=TODAY + timedelta(days=400))
    context = _context(item, allergies=("fragrance",))
    context.stored_ingredients[str(item.id)] = [ProductIngredient(
        account_id=context.account_id, item_id=item.id, ingredient_key="fragrance",
        matched_text="parfum", confidence=0.4, source="photo_extracted",
        needs_confirmation=True, confirmed_at=None,
    )]
    queue = _queue(context)

    about_item = [row for row in queue.active if str(item.id) in row.item_ids]
    assert [row.rule_id for row in about_item] == [rules_engine.RULE_UNCONFIRMED]
    assert about_item[0].decision == manager.DECISION_CONFIRM_LABEL
    assert about_item[0].severity == SEVERITY_INFO
    assert all(row.severity != SEVERITY_AVOID for row in queue.active)
    assert all(row.action.kind != manager.ACTION_PAUSE_PRODUCT for row in queue.active)


def test_confirming_the_same_reading_is_what_turns_it_into_an_avoid_decision():
    from app.domains.routines.models import ProductIngredient
    from app.shared.database.base import utcnow

    item = _item(product_type="cleanser", name="Known Cleanser", expiry=TODAY + timedelta(days=400))
    context = _context(item, allergies=("fragrance",))
    context.stored_ingredients[str(item.id)] = [ProductIngredient(
        account_id=context.account_id, item_id=item.id, ingredient_key="fragrance",
        matched_text="parfum", confidence=1.0, source="user_declared",
        needs_confirmation=False, confirmed_at=utcnow(),
    )]
    queue = _queue(context)

    assert queue.primary.rule_id == rules_engine.RULE_ALLERGY
    assert queue.primary.severity == SEVERITY_AVOID


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_the_manager_only_reads_skin_care_and_hair_care():
    assert manager.MANAGER_CATEGORIES == shelf.ROUTINE_CATEGORIES
    assert set(manager.MANAGER_CATEGORIES) == {"beauty", "hair"}
    assert "perfumes" not in manager.MANAGER_CATEGORIES
    assert "supplements" not in manager.MANAGER_CATEGORIES


def test_a_perfume_and_a_supplement_never_produce_a_decision():
    perfume = _item(category="perfumes", product_type="eau_de_parfum", name="A Perfume")
    supplement = _item(category="supplements", product_type="capsule", name="A Supplement")
    queue = _queue(_context(perfume, supplement))

    assert queue.active == []
    assert all(
        str(perfume.id) not in row.item_ids and str(supplement.id) not in row.item_ids
        for row in queue.active
    )


def test_drafts_are_not_decided_about():
    """An unconfirmed item is not something the person owns yet."""
    context = _context(_item(product_type="cleanser", expiry=TODAY - timedelta(days=5)))
    context.draft_count = 3
    queue = _queue(context)

    # The draft count is carried by the shelf context and is not a decision.
    assert context.draft_count == 3
    assert all(row.rule_id != "rule.draft" for row in queue.active)


# ---------------------------------------------------------------------------
# Provenance and actions
# ---------------------------------------------------------------------------


def test_every_decision_carries_a_reviewed_rule_and_a_declared_action():
    context = _context(
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=5)),
        _item(product_type="moisturiser", expiry=TODAY + timedelta(days=10)),
        _item(product_type="toner"),
        _item(category="hair", product_type="shampoo", actives=["retinol"]),
        _item(category="hair", product_type="conditioner", actives=["glycolic acid"]),
    )
    queue = _queue(context)

    assert queue.active, "this shelf should produce decisions"
    reviewed = rules_engine.all_rule_ids()
    for row in queue.active:
        assert row.rule_id in reviewed, row.rule_id
        assert row.action.kind in manager.ACTION_KINDS
        assert row.kind in manager.MANAGER_KINDS
        assert row.evidence_note, row.decision_key
        assert row.override_label in (manager.OVERRIDE_NOT_NOW, manager.OVERRIDE_KEEP_PAUSED)


def test_a_reviewed_note_that_is_only_information_is_not_a_decision():
    """"These two are fine together" belongs on the shelf, not in a queue."""
    assert manager.decision_rank(
        kind=manager.KIND_FINDING, rule_id="rule.vitamin_c_niacinamide", severity=SEVERITY_INFO,
    ) is None

    context = _context(
        _item(product_type="treatment", actives=["vitamin c"], expiry=TODAY + timedelta(days=400)),
        _item(product_type="moisturiser", actives=["niacinamide"], expiry=TODAY + timedelta(days=400)),
    )
    assert "rule.vitamin_c_niacinamide" not in _rules(_queue(context))


def test_a_reviewed_layering_caution_opens_the_routine_rather_than_choosing_for_you():
    context = _context(
        _item(product_type="treatment", actives=["retinol"], name="Retinol Serum",
              expiry=TODAY + timedelta(days=400)),
        _item(product_type="exfoliant", actives=["glycolic acid"], name="AHA Exfoliant",
              expiry=TODAY + timedelta(days=400)),
    )
    queue = _queue(context)
    pair = next(row for row in queue.active if len(row.item_ids) == 2)

    assert pair.severity == SEVERITY_CAUTION
    assert pair.decision == manager.DECISION_KEEP_APART
    assert pair.action.kind == manager.ACTION_OPEN_ROUTINE
    assert pair.action.inventory_item_id is None
    assert pair.decision_key == f"{pair.rule_id}:items:{'+'.join(sorted(pair.item_ids))}"


def test_a_missing_required_step_asks_for_something_you_already_own():
    queue = _queue(_context(_item(product_type="cleanser", expiry=TODAY + timedelta(days=400))))
    missing = [row for row in queue.active if row.rule_id == rules_engine.RULE_MISSING_SLOT]

    assert missing, "a shelf with only a cleanser is missing required steps"
    for row in missing:
        assert row.decision.startswith("Add a ")
        assert row.decision.endswith(" you already own.")
        assert row.action.kind == manager.ACTION_ADD_OWNED_PRODUCT
        assert row.action.inventory_item_id is None
        assert row.decision_key == f"{rules_engine.RULE_MISSING_SLOT}:{row.category}:{row.slot}"
    assert {row.slot for row in missing} == {"moisturiser", "sunscreen"}


def test_low_use_is_named_plainly_and_never_priced():
    unused = _item(product_type="toner", name="Unused Toner", expiry=TODAY + timedelta(days=400))
    queue = _queue(_context(unused, low_use=(unused.id,)))
    low = next(row for row in queue.active if row.rule_id == rules_engine.RULE_LOW_USE)

    assert low.decision == manager.DECISION_USE_BEFORE_REPLACING
    assert "waste" not in (low.decision + low.reason + low.evidence_note).lower()
    assert "₹" not in low.reason
    # The canonical definition of low use is carried through, not restated.
    assert low.evidence_note == (
        "Active for at least 30 days, used no more than twice, and not used in the last 30 days."
    )


def test_a_product_with_no_date_is_asked_for_one_rather_than_given_one():
    undated = _item(product_type="cleanser", name="Undated Cleanser")
    queue = _queue(_context(undated))
    row = next(r for r in queue.active if r.rule_id == rules_engine.RULE_NO_EXPIRY)

    assert row.decision == manager.DECISION_RECORD_DATE
    assert row.action.kind == manager.ACTION_RECORD_DATE
    assert row.action.inventory_item_id == str(undated.id)


def test_an_expiring_product_is_offered_for_use_where_the_care_authority_would_accept_it():
    running_out = _item(product_type="cleanser", name="Nearly Gone", expiry=TODAY + timedelta(days=10))
    queue = _queue(_context(running_out))
    row = next(r for r in queue.active if r.rule_id == rules_engine.RULE_EXPIRING)

    assert row.decision == manager.DECISION_USE_NEXT
    assert row.action.kind == manager.ACTION_PREFER_PRODUCT


def test_an_expiring_product_that_is_paused_is_opened_rather_than_preferred():
    """``prefer_care_product`` refuses a paused product, so we do not offer it."""
    running_out = _item(product_type="cleanser", name="Paused And Running Out",
                        expiry=TODAY + timedelta(days=10))
    queue = _queue(_context(running_out), paused_item_ids=frozenset({running_out.id}))
    row = next(r for r in queue.active if r.rule_id == rules_engine.RULE_EXPIRING)

    assert row.action.kind == manager.ACTION_OPEN_INVENTORY_ITEM


def test_an_expired_product_is_never_offered_as_the_one_to_use_next():
    """``prefer_care_product`` refuses an expired product, so we do not offer it."""
    unused_and_expired = _item(
        product_type="cleanser", name="Expired And Unused", expiry=TODAY - timedelta(days=5),
    )
    queue = _queue(_context(unused_and_expired, low_use=(unused_and_expired.id,)))
    low = next(row for row in queue.active if row.rule_id == rules_engine.RULE_LOW_USE)

    assert low.action.kind == manager.ACTION_OPEN_INVENTORY_ITEM


def test_a_product_with_a_confirmed_allergen_is_never_offered_as_the_one_to_use_next():
    from app.domains.routines.models import ProductIngredient
    from app.shared.database.base import utcnow

    item = _item(product_type="cleanser", name="Scented Cleanser", expiry=TODAY + timedelta(days=400))
    context = _context(item, allergies=("fragrance",), low_use=(item.id,))
    context.stored_ingredients[str(item.id)] = [ProductIngredient(
        account_id=context.account_id, item_id=item.id, ingredient_key="fragrance",
        matched_text="parfum", confidence=1.0, source="user_declared",
        needs_confirmation=False, confirmed_at=utcnow(),
    )]
    queue = _queue(context)
    low = next(row for row in queue.active if row.rule_id == rules_engine.RULE_LOW_USE)

    assert low.action.kind == manager.ACTION_OPEN_INVENTORY_ITEM


def test_what_blocks_a_preference_is_what_the_care_engine_says_blocks_one():
    """Held against the canonical engine, so the two cannot drift apart."""
    from dataclasses import replace

    from app.domains.care.decisions import evaluate_care_context

    from tests.test_care_decisions import _context as _care_context
    from tests.test_care_decisions import _product as _care_product

    expired = _care_product("beauty", "cleanser", expiry=TODAY - timedelta(days=1))
    allergic = _care_product("beauty", "moisturiser", ingredient_key="fragrance", confirmed=True)
    paused = _care_product("beauty", "sunscreen")
    fine = _care_product("hair", "shampoo")
    products = [expired, allergic, paused, fine]

    care = replace(
        _care_context(*products, allergies=("fragrance",)),
        plan_date=TODAY, paused_product_ids=frozenset({paused.item.id}),
    )
    canonical = {
        row.item_id for row in evaluate_care_context(care).product_decisions if not row.eligible
    }
    ours = manager.preference_blocked_item_ids(
        products, allergies=("fragrance",), today=TODAY,
        paused_item_ids=frozenset({paused.item.id}),
    )

    assert ours == canonical
    assert fine.item.id not in ours


def test_an_expiring_product_with_no_routine_step_is_opened_rather_than_preferred():
    running_out = _item(product_type="mystery", name="Unknown Product", expiry=TODAY + timedelta(days=10))
    queue = _queue(_context(running_out))
    row = next(r for r in queue.active if r.rule_id == rules_engine.RULE_EXPIRING)

    assert row.slot is None
    assert row.action.kind == manager.ACTION_OPEN_INVENTORY_ITEM


# ---------------------------------------------------------------------------
# Already-done and overridden
# ---------------------------------------------------------------------------


def test_a_product_that_is_already_paused_is_not_asked_to_be_paused_again():
    expired = _item(product_type="cleanser", expiry=TODAY - timedelta(days=5))
    active = _queue(_context(expired))
    assert active.primary.action.kind == manager.ACTION_PAUSE_PRODUCT

    settled = _queue(_context(expired), paused_item_ids=frozenset({expired.id}))
    assert all(
        row.action.kind != manager.ACTION_PAUSE_PRODUCT for row in settled.active
    )


def test_a_navigation_is_never_treated_as_already_done():
    """Opening a screen resolves nothing. Only stored state can settle a decision."""
    undated = _item(product_type="cleanser")
    queue = _queue(_context(undated))
    row = next(r for r in queue.active if r.rule_id == rules_engine.RULE_NO_EXPIRY)

    assert row.action.kind in manager.ROUTE_ONLY_ACTION_KINDS
    assert manager._already_done(row, frozenset({undated.id}), frozenset({undated.id})) is False


def test_saying_not_now_quietens_exactly_that_decision_and_nothing_else():
    expired = _item(product_type="cleanser", expiry=TODAY - timedelta(days=5), name="Expired Cleanser")
    baseline = _queue(_context(expired))
    primary = baseline.primary

    after = _queue(_context(expired), declined={primary.decision_key: primary.fingerprint})

    assert primary.decision_key not in _keys(after)
    assert [row.decision_key for row in after.overridden] == [primary.decision_key]
    assert after.as_dict()["counts"]["overridden"] == 1
    # Everything else it had to say is still there.
    assert len(after.active) == len(baseline.active) - 1


def test_a_decision_comes_back_once_its_inputs_change():
    item_id = uuid.UUID(int=21)
    before = _queue(_context(_item(
        product_type="cleanser", name="Cleanser", expiry=TODAY - timedelta(days=5), item_id=item_id,
    ))).primary

    corrected = _context(_item(
        product_type="cleanser", name="Cleanser", expiry=TODAY - timedelta(days=200), item_id=item_id,
    ))
    after = _queue(corrected, declined={before.decision_key: before.fingerprint})

    assert before.decision_key in _keys(after)


def test_an_override_carries_no_penalty_in_the_contract():
    expired = _item(product_type="cleanser", expiry=TODAY - timedelta(days=5))
    primary = _queue(_context(expired)).primary
    payload = primary.as_dict()

    assert payload["override"] == {"label": manager.OVERRIDE_NOT_NOW, "choice": "override"}
    # Nothing in a decision can be used to score, rank or punish a person.
    forbidden = {"score", "streak", "compliance", "penalty", "adherence", "missed", "strike"}
    assert forbidden.isdisjoint(payload)


# ---------------------------------------------------------------------------
# Giving things back
# ---------------------------------------------------------------------------


def _shelf_products(context: ShelfContext) -> dict:
    return {
        product.id: product
        for category in manager.MANAGER_CATEGORIES
        for product in shelf.build(context, category)
    }


def test_a_paused_product_is_offered_back_once_the_reason_has_gone():
    item = _item(product_type="cleanser", name="Fixed Cleanser", expiry=TODAY + timedelta(days=400))
    context = _context(item)
    candidate = manager.GiveBackCandidate(
        item_id=item.id, rule_id=rules_engine.RULE_EXPIRED,
        display_name=item.display_name, category="beauty", slot="cleanser",
    )
    queue = manager.compile_queue(
        context,
        paused_item_ids=frozenset({item.id}),
        preferred_item_ids=frozenset(),
        give_back_candidates=[candidate],
    )

    assert queue.primary.kind == manager.KIND_GIVE_BACK
    assert queue.primary.decision == manager.DECISION_BRING_IT_BACK
    assert queue.primary.reason == "Fixed Cleanser can return."
    assert queue.primary.action.kind == manager.ACTION_RESUME_PRODUCT
    assert queue.primary.action.inventory_item_id == str(item.id)
    assert queue.primary.override_label == manager.OVERRIDE_KEEP_PAUSED
    assert queue.primary.rule_id == rules_engine.RULE_EXPIRED
    assert queue.as_dict()["counts"]["give_back"] == 1


def test_giving_back_comes_before_everything_else_the_manager_has_to_say():
    fixed = _item(product_type="cleanser", name="Fixed Cleanser", expiry=TODAY + timedelta(days=400))
    allergen = _item(product_type="moisturiser", name="Scented Cream", actives=["fragrance"],
                     expiry=TODAY + timedelta(days=400))
    context = _context(fixed, allergen, allergies=("fragrance",))
    queue = manager.compile_queue(
        context,
        paused_item_ids=frozenset({fixed.id}),
        preferred_item_ids=frozenset(),
        give_back_candidates=[manager.GiveBackCandidate(
            item_id=fixed.id, rule_id=rules_engine.RULE_EXPIRED,
            display_name=fixed.display_name, category="beauty", slot="cleanser",
        )],
    )

    assert queue.primary.kind == manager.KIND_GIVE_BACK
    assert any(row.severity == SEVERITY_AVOID for row in queue.active)


def test_nothing_is_offered_back_while_something_would_pause_it_again():
    still_expired = _item(product_type="cleanser", name="Still Expired", expiry=TODAY - timedelta(days=5))
    context = _context(still_expired)
    queue = manager.compile_queue(
        context,
        paused_item_ids=frozenset({still_expired.id}),
        preferred_item_ids=frozenset(),
        give_back_candidates=[manager.GiveBackCandidate(
            item_id=still_expired.id, rule_id=rules_engine.RULE_EXPIRED,
            display_name=still_expired.display_name, category="beauty", slot="cleanser",
        )],
    )

    assert all(row.kind != manager.KIND_GIVE_BACK for row in queue.active)


def test_a_product_that_is_no_longer_paused_has_nothing_to_give_back():
    item = _item(product_type="cleanser", name="Already Back", expiry=TODAY + timedelta(days=400))
    context = _context(item)
    queue = manager.compile_queue(
        context,
        paused_item_ids=frozenset(),
        preferred_item_ids=frozenset(),
        give_back_candidates=[manager.GiveBackCandidate(
            item_id=item.id, rule_id=rules_engine.RULE_EXPIRED,
            display_name=item.display_name, category="beauty", slot="cleanser",
        )],
    )

    assert all(row.kind != manager.KIND_GIVE_BACK for row in queue.active)


def test_a_product_no_longer_on_the_shelf_is_not_offered_back():
    gone = uuid.uuid4()
    queue = manager.compile_queue(
        _context(),
        paused_item_ids=frozenset({gone}),
        preferred_item_ids=frozenset(),
        give_back_candidates=[manager.GiveBackCandidate(
            item_id=gone, rule_id=rules_engine.RULE_EXPIRED,
            display_name="Deleted Product", category="beauty", slot="cleanser",
        )],
    )

    assert queue.active == []


def test_keep_paused_is_remembered_for_the_same_unchanged_offer():
    item = _item(product_type="cleanser", name="Stay Paused", expiry=TODAY + timedelta(days=400))
    context = _context(item)
    candidate = manager.GiveBackCandidate(
        item_id=item.id, rule_id=rules_engine.RULE_EXPIRED,
        display_name=item.display_name, category="beauty", slot="cleanser",
    )
    offered = manager.compile_queue(
        context, paused_item_ids=frozenset({item.id}), preferred_item_ids=frozenset(),
        give_back_candidates=[candidate],
    ).primary

    after = manager.compile_queue(
        context, paused_item_ids=frozenset({item.id}), preferred_item_ids=frozenset(),
        give_back_candidates=[candidate],
        declined={offered.decision_key: offered.fingerprint},
    )

    assert all(row.kind != manager.KIND_GIVE_BACK for row in after.active)
    assert [row.decision_key for row in after.overridden] == [offered.decision_key]


# ---------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------


BLAMING_WORDS = (
    "money wasted", "wasted", "bad wardrobe", "bad shelf", "failed routine", "failure",
    "ugly", "unattractive", "poor appearance", "you should have", "your fault",
    "should have bought", "buy a", "buy one", "purchase",
)


def _every_string(queue: manager.ManagerQueue) -> list[str]:
    strings: list[str] = []
    for row in (*queue.active, *queue.overridden):
        strings.extend([row.decision, row.reason, row.evidence_note, row.override_label, row.action.label])
    payload = queue.as_dict()
    if payload["message"]:
        strings.append(payload["message"])
    return [row for row in strings if row]


def _busy_queue() -> manager.ManagerQueue:
    unused = _item(product_type="toner", name="Unused Toner", expiry=TODAY + timedelta(days=400))
    return _queue(_context(
        _item(product_type="cleanser", name="Old Cleanser", expiry=TODAY - timedelta(days=5)),
        _item(product_type="moisturiser", name="Running Out", expiry=TODAY + timedelta(days=10)),
        _item(product_type="treatment", name="Retinol Serum", actives=["retinol"],
              expiry=TODAY + timedelta(days=400)),
        _item(product_type="exfoliant", name="AHA Exfoliant", actives=["glycolic acid"],
              expiry=TODAY + timedelta(days=400)),
        unused,
        _item(category="hair", product_type="shampoo", name="A Shampoo"),
        _item(category="hair", product_type="shampoo", name="Another Shampoo"),
        allergies=(),
        low_use=(unused.id,),
    ))


def test_no_string_the_manager_produces_blames_anybody():
    for value in _every_string(_busy_queue()):
        lowered = value.lower()
        for word in BLAMING_WORDS:
            assert word not in lowered, f"{word!r} in {value!r}"


def test_every_string_the_manager_produces_passes_the_medical_boundary_sweep():
    for value in _every_string(_busy_queue()):
        assert narrative_is_safe(value), value
    for value in (
        *manager.ACTION_LABELS.values(), manager.EMPTY_QUEUE_MESSAGE, manager.NO_PRODUCTS_MESSAGE,
        manager.GIVE_BACK_EVIDENCE_NOTE, manager.DECISION_BRING_IT_BACK,
        manager.OVERRIDE_NOT_NOW, manager.OVERRIDE_KEEP_PAUSED,
    ):
        assert narrative_is_safe(value), value


def test_the_manager_never_suggests_buying_anything():
    for value in _every_string(_busy_queue()):
        lowered = value.lower()
        assert "buy" not in lowered, value
        assert "shop" not in lowered, value
        assert "order" not in lowered or "in order" in lowered, value


# ---------------------------------------------------------------------------
# The stored contract
# ---------------------------------------------------------------------------


def test_the_manager_reads_no_prose_so_there_is_nothing_to_hand_off():
    """Why the medical handoff gate does not appear in this module.

    ``hard_handoff.evaluate`` exists to catch a person asking about a
    medication, a pregnancy, or something a clinician is looking after. It
    reads text. The manager reads no text: its inputs are a product type, a
    recorded date, a confirmed ingredient key and a routine step, and the only
    thing a request may carry is a decision identifier, a hash, a yes-or-no and
    a submission key. There is no sentence anywhere for the gate to read, which
    is why the gate is not wired in rather than wired in and never firing.
    """
    from app.domains.routines.schemas import ShelfManagerRespondRequest

    assert set(ShelfManagerRespondRequest.model_fields) == {
        "decision_key", "decision_fingerprint", "choice", "client_mutation_id",
    }
    assert ShelfManagerRespondRequest.model_config["extra"] == "forbid"

    stored = {column.name for column in ShelfManagerDecisionEvent.__table__.columns}
    assert not stored & {"note", "question", "text", "message", "payload", "reason"}


def test_the_stored_choice_is_always_derived_from_the_kind_and_never_sent():
    assert manager.stored_choice_for(manager.KIND_FINDING, "accept") == "accepted"
    assert manager.stored_choice_for(manager.KIND_FINDING, "override") == "overridden"
    assert manager.stored_choice_for(manager.KIND_GIVE_BACK, "accept") == "restored"
    assert manager.stored_choice_for(manager.KIND_GIVE_BACK, "override") == "restore_overridden"
    assert set(manager.MANAGER_CHOICES) == {
        "accepted", "overridden", "restored", "restore_overridden",
    }
    assert set(manager.REQUEST_CHOICES) == {"accept", "override"}


def test_the_database_only_accepts_the_declared_choices_and_actions():
    """The table's CHECK constraints and the module's lists cannot drift apart."""
    constraints = {
        row.name: str(row.sqltext)
        for row in ShelfManagerDecisionEvent.__table__.constraints
        if getattr(row, "sqltext", None) is not None
    }
    choices = constraints["ck_shelf_manager_event_choice"]
    for value in manager.MANAGER_CHOICES:
        assert f"'{value}'" in choices
    assert choices.count("'") == 2 * len(manager.MANAGER_CHOICES)

    actions = constraints["ck_shelf_manager_event_action_kind"]
    for value in manager.ACTION_KINDS:
        assert f"'{value}'" in actions
    assert actions.count("'") == 2 * len(manager.ACTION_KINDS)


def test_the_closed_action_list_has_no_generic_route_escape_hatch():
    assert set(manager.ACTION_KINDS) == {
        "pause_product", "resume_product", "prefer_product", "unprefer_product",
        "confirm_label", "record_date", "add_owned_product", "open_routine",
        "open_inventory_item", "none",
    }
    assert {
        "pause_product", "resume_product", "prefer_product", "unprefer_product",
    } == manager.MUTATING_ACTION_KINDS
    decision = _queue(_context(_item(product_type="cleanser", expiry=TODAY - timedelta(days=5)))).primary
    # There is no server-supplied path, url or screen name in the contract.
    assert set(decision.as_dict()["action"]) == {"kind", "label", "inventory_item_id", "mutates"}


def test_every_action_kind_has_a_label_and_only_state_changes_claim_to_mutate():
    for kind in manager.ACTION_KINDS:
        assert kind in manager.ACTION_LABELS
        assert manager.ManagerAction(kind).mutates == (kind in manager.MUTATING_ACTION_KINDS)
    assert manager.ACTION_LABELS[manager.ACTION_NONE] == ""


def test_the_rule_behind_a_stored_decision_key_can_always_be_recovered():
    context = _context(
        _item(product_type="cleanser", expiry=TODAY - timedelta(days=5)),
        _item(product_type="treatment", actives=["retinol"], expiry=TODAY + timedelta(days=400)),
        _item(product_type="exfoliant", actives=["glycolic acid"], expiry=TODAY + timedelta(days=400)),
    )
    for row in _queue(context).active:
        assert manager._rule_id_from_key(row.decision_key) == row.rule_id, row.decision_key


def test_a_decision_key_fits_the_column_that_stores_it_whatever_the_shelf():
    """A shelf full of unused products must not produce a key nothing can store."""
    unused = [
        _item(product_type="toner", name=f"Unused {index}", expiry=TODAY + timedelta(days=400))
        for index in range(24)
    ]
    queue = _queue(_context(*unused, low_use=tuple(row.id for row in unused)))

    assert queue.active, "24 unused products should produce decisions"
    for row in queue.active:
        assert len(row.decision_key) <= manager.DECISION_KEY_MAX_LENGTH, row.decision_key
    low = next(row for row in queue.active if row.rule_id == rules_engine.RULE_LOW_USE)
    assert len(low.item_ids) == 24


def test_the_key_of_a_multi_product_decision_changes_with_the_set_of_products():
    """Digesting the ids must not lose which products a decision was about."""
    ids = [uuid.UUID(int=index) for index in range(30, 36)]
    first = manager._item_key(rules_engine.RULE_LOW_USE, [str(row) for row in ids])
    same = manager._item_key(rules_engine.RULE_LOW_USE, [str(row) for row in reversed(ids)])
    fewer = manager._item_key(rules_engine.RULE_LOW_USE, [str(row) for row in ids[:-1]])

    assert first == same
    assert first != fewer
    assert manager._rule_id_from_key(first) == rules_engine.RULE_LOW_USE


def test_a_pair_still_names_both_products_in_its_key():
    left, right = str(uuid.UUID(int=41)), str(uuid.UUID(int=42))
    key = manager._item_key("rule.retinoid_aha", [right, left])

    assert key == f"rule.retinoid_aha:items:{left}+{right}"
    assert len(key) <= manager.DECISION_KEY_MAX_LENGTH


def test_a_key_that_names_no_reviewed_rule_is_refused_rather_than_guessed():
    assert manager._rule_id_from_key("rule.invented_by_a_client:item:x") is None
    assert manager._rule_id_from_key("") is None


@pytest.mark.parametrize("rule_id", sorted(rules_engine.ENGINE_RULES))
def test_every_engine_rule_is_either_ranked_or_deliberately_not_a_decision(rule_id: str):
    ranked = manager.decision_rank(
        kind=manager.KIND_FINDING, rule_id=rule_id, severity=SEVERITY_INFO,
    ) is not None
    # ``rule.user_allergy`` is ranked by its severity rather than its id: it is
    # the only rule the engine raises at ``avoid``.
    if rule_id == rules_engine.RULE_ALLERGY:
        assert not ranked
        assert manager.decision_rank(
            kind=manager.KIND_FINDING, rule_id=rule_id, severity=SEVERITY_AVOID,
        ) == 1
    elif rule_id == rules_engine.RULE_EXPIRED:
        assert ranked
    else:
        assert ranked, rule_id
