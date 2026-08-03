"""Shelf intelligence: what you already own, read honestly.

This is the layer that answers "what is on my shelf and what does it mean"
without ever becoming a shop. Three rules shape everything here:

* **Owned-first.** Nothing is suggested that the user does not already own,
  except a plain category description for a genuinely missing *required* step.
* **Every warning has a rule.** Findings come from ``rules.py`` and carry a
  ``rule_id`` pointing at a reviewed row. A warning without one is not
  expressible — :class:`~app.domains.routines.rules.Finding` requires the field.
* **Low confidence never warns.** An ingredient read off a photo below the
  confirmation threshold is surfaced as "please confirm", never as a conflict.

Supplements deliberately get their own, much narrower treatment: expiry status
and a professional boundary, and nothing else. See :mod:`.safety`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory import service as inventory_service
from app.domains.inventory.models import InventoryItem
from app.domains.profile import service as profile_service
from app.domains.profile.models import AppearanceProfile
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import parser
from app.domains.routines import rules as rules_engine
from app.domains.routines.models import ProductIngredient
from app.domains.routines.ontology import SEVERITY_AVOID, SEVERITY_CAUTION, SLOT_BY_KEY
from app.domains.routines.rules import Finding, ShelfProduct
from app.domains.routines.safety import (
    SUPPLEMENT_DISCLAIMER, SUPPLEMENT_FLAG_EXPIRED, SUPPLEMENT_FLAG_EXPIRING,
    SUPPLEMENT_FLAG_NO_EXPIRY, SUPPLEMENT_FLAG_PROFESSIONAL, SUPPLEMENT_FLAG_TEXT,
    needs_professional,
)

# Categories the routine engine reasons over. Perfumes and supplements are
# inventory with their own, narrower handling.
ROUTINE_CATEGORIES = ("beauty", "hair")

SUPPLEMENT_EXPIRING_DAYS = 45


@dataclass
class ShelfContext:
    """Everything the shelf and routine engines are allowed to read.

    Drafts are counted but never used. An item the user has not confirmed is
    not something they own, and Phase 3 drew that line — this reads the same
    side of it.
    """

    account_id: uuid.UUID
    today: date
    owned: List[OwnedItem] = field(default_factory=list)
    draft_count: int = 0
    allergies: List[str] = field(default_factory=list)
    climate: Optional[str] = None
    diet: Optional[str] = None
    # Ingredient confirmations the user has already made, keyed item -> keys.
    confirmed_ingredients: Dict[str, set] = field(default_factory=dict)
    # Computed once from the stored rows, because Phase 3's low-use rule reads
    # the date an item was catalogued and an OwnedItem does not carry it.
    low_use_ids: Set[uuid.UUID] = field(default_factory=set)

    def by_category(self, category: str) -> List[OwnedItem]:
        return [item for item in self.owned if item.category == category]


# Profile attributes this engine is allowed to read. Allergies are the reason
# this list is not the one in ``recommendation.context`` — a styling run has no
# business reading them, and a shelf run cannot work without them.
SHELF_ATTRIBUTES = ("allergies", "climate", "city", "hydration_habits", "preferred_style")


async def shelf_attributes(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, Any]:
    """Confirmed attributes only.

    An unconfirmed observation must never silently decide that a product is
    left out of somebody's routine.
    """
    profile = (await session.execute(
        select(AppearanceProfile).where(AppearanceProfile.account_id == account_id)
    )).scalar_one_or_none()
    if profile is None:
        return {}
    rows = await profile_service.attributes_for(session, profile.id)
    return {
        row.key: row.value
        for row in rows
        if row.key in SHELF_ATTRIBUTES and row.verification_state == "confirmed"
    }


async def _confirmed_ingredient_keys(session: AsyncSession, account_id: uuid.UUID) -> Dict[str, set]:
    rows = (await session.execute(
        select(ProductIngredient.item_id, ProductIngredient.ingredient_key).where(
            ProductIngredient.account_id == account_id,
            ProductIngredient.confirmed_at.is_not(None),
        )
    )).all()
    out: Dict[str, set] = {}
    for item_id, key in rows:
        out.setdefault(str(item_id), set()).add(key)
    return out


async def gather(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    climate: Optional[str] = None,
    today: Optional[date] = None,
) -> ShelfContext:
    """Read every confirmed fact the shelf engine may use."""
    rows = (await session.execute(
        select(InventoryItem).where(
            InventoryItem.account_id == account_id,
            InventoryItem.status == "active",
        )
    )).scalars().all()

    owned: List[OwnedItem] = []
    low_use_ids: Set[uuid.UUID] = set()
    drafts = 0
    for row in rows:
        if row.verification_state != "confirmed":
            drafts += 1
            continue
        if inventory_service.is_low_use(row, today):
            low_use_ids.add(row.id)
        owned.append(OwnedItem(
            id=row.id, category=row.category, subcategory=row.subcategory,
            display_name=row.display_name, brand=row.brand,
            details=await inventory_service.details_for(session, row),
            condition=row.condition, usage_count=row.usage_count, last_used_at=row.last_used_at,
            purchase_price=float(row.purchase_price) if row.purchase_price is not None else None,
            currency=row.currency,
        ))

    attributes = await shelf_attributes(session, account_id)
    allergies = attributes.get("allergies") or []
    if isinstance(allergies, str):
        allergies = [allergies]

    return ShelfContext(
        account_id=account_id,
        today=today or date.today(),
        owned=owned,
        draft_count=drafts,
        allergies=[str(row) for row in allergies],
        climate=climate or attributes.get("climate"),
        confirmed_ingredients=await _confirmed_ingredient_keys(session, account_id),
        low_use_ids=low_use_ids,
    )


def build(context: ShelfContext, category: str) -> List[ShelfProduct]:
    """Parse one category of the user's shelf into what the engine reasons over.

    Confirmations already recorded are applied here: an ingredient the user has
    confirmed is promoted to full confidence, so it starts driving rules.
    """
    products = rules_engine.build_products(context.owned, category, context.low_use_ids)
    for product in products:
        confirmed = context.confirmed_ingredients.get(product.id)
        if not confirmed:
            continue
        for row in product.ingredients:
            if row.key in confirmed:
                row.confidence = 1.0
                row.source = parser.SOURCE_USER
    return products


# --- The reports the API serves ----------------------------------------------


def findings_for(
    products: Sequence[ShelfProduct], category: str, context: ShelfContext
) -> List[Finding]:
    """Everything worth telling the user about one category of their shelf."""
    return (
        rules_engine.allergy_findings(products, context.allergies)
        + rules_engine.compatibility_findings(products)
        + rules_engine.duplicate_slot_findings(products)
        + rules_engine.missing_slot_findings(products, category)
        + rules_engine.expiry_findings(products, context.today)
        + rules_engine.low_use_findings(products)
        + rules_engine.unconfirmed_findings(products)
    )


def _product_row(product: ShelfProduct, today: date) -> Dict[str, Any]:
    spec = SLOT_BY_KEY.get(product.slot or "")
    return {
        "inventory_item_id": product.id,
        "display_name": product.item.display_name,
        "brand": product.item.brand,
        "category": product.item.category,
        "slot": product.slot,
        "slot_label": spec.label if spec else None,
        "effective_expiry": product.effective_expiry.isoformat() if product.effective_expiry else None,
        "days_to_expiry": product.days_to_expiry(today),
        "low_use": product.low_use,
        "usage_count": product.item.usage_count,
        "remaining_percent": product.item.details.get("remaining_percent"),
        "ingredients": [row.as_dict() for row in product.ingredients],
        "needs_confirmation": [row.as_dict() for row in product.unconfirmed],
    }


def category_report(context: ShelfContext, category: str) -> Dict[str, Any]:
    """One category of the shelf, end to end."""
    products = build(context, category)
    findings = findings_for(products, category, context)
    filled = {product.slot for product in products if product.slot}

    return {
        "category": category,
        "product_count": len(products),
        "products": [_product_row(product, context.today) for product in products],
        "warnings": [row.as_dict() for row in findings],
        "ingredient_overlap": rules_engine.ingredient_overlap(products),
        "slots_filled": sorted(row for row in filled if row),
        "unidentified_products": [
            {"inventory_item_id": product.id, "display_name": product.item.display_name,
             "note": "We could not work out which routine step this belongs to. Setting a product type fixes it."}
            for product in products if product.slot is None
        ],
        "climate_notes": rules_engine.climate_notes(context.climate, filled),
    }


def summary(context: ShelfContext) -> Dict[str, Any]:
    """The whole shelf at a glance, counted rather than scored.

    No overall number. A single "shelf score" would be a judgement of somebody's
    choices, and there is no honest way to compute one.
    """
    categories = {category: category_report(context, category) for category in ROUTINE_CATEGORIES}

    all_warnings: List[Dict[str, Any]] = []
    for report in categories.values():
        all_warnings.extend(report["warnings"])

    by_severity = {
        SEVERITY_AVOID: [row for row in all_warnings if row["severity"] == SEVERITY_AVOID],
        SEVERITY_CAUTION: [row for row in all_warnings if row["severity"] == SEVERITY_CAUTION],
    }
    needs_attention = [
        row for row in all_warnings
        if row["rule_id"] in (rules_engine.RULE_EXPIRED, rules_engine.RULE_EXPIRING, rules_engine.RULE_LOW_USE)
        or row["severity"] in (SEVERITY_AVOID, SEVERITY_CAUTION)
    ]

    return {
        "categories": {key: {
            "product_count": report["product_count"],
            "slots_filled": report["slots_filled"],
            "warning_count": len(report["warnings"]),
        } for key, report in categories.items()},
        "reports": categories,
        "counts": {
            "products": sum(report["product_count"] for report in categories.values()),
            "avoid": len(by_severity[SEVERITY_AVOID]),
            "caution": len(by_severity[SEVERITY_CAUTION]),
            "needs_attention": len(needs_attention),
            "awaiting_confirmation": sum(
                len(row["needs_confirmation"]) for report in categories.values() for row in report["products"]
            ),
            "drafts": context.draft_count,
        },
        "needs_attention": needs_attention,
        "draft_note": (
            f"{context.draft_count} item{'s' if context.draft_count != 1 else ''} still to confirm. "
            "We do not count those as owned yet."
        ) if context.draft_count else None,
    }


def expiring(context: ShelfContext, days: int = 60) -> Dict[str, Any]:
    """Products running out, and products with no date recorded.

    A missing date is reported as missing rather than guessed. Inventing an
    expiry would put a confident number on something we simply do not know.
    """
    soon: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    undated: List[Dict[str, Any]] = []

    for category in ROUTINE_CATEGORIES:
        for product in build(context, category):
            row = _product_row(product, context.today)
            remaining = product.days_to_expiry(context.today)
            if remaining is None:
                row["rule_id"] = rules_engine.RULE_NO_EXPIRY
                undated.append(row)
            elif remaining < 0:
                row["rule_id"] = rules_engine.RULE_EXPIRED
                expired.append(row)
            elif remaining <= days:
                row["rule_id"] = rules_engine.RULE_EXPIRING
                soon.append(row)

    expired.sort(key=lambda row: row["days_to_expiry"])
    soon.sort(key=lambda row: row["days_to_expiry"])
    return {
        "window_days": days,
        "expired": expired,
        "expiring_soon": soon,
        "no_date_recorded": undated,
        "note": "Worked out from the expiry date, or the opened date plus period-after-opening, that you recorded.",
    }


def low_use(context: ShelfContext) -> Dict[str, Any]:
    """Products sitting unused. Named plainly, never framed as waste."""
    rows: List[Dict[str, Any]] = []
    for category in ROUTINE_CATEGORIES:
        rows.extend(
            _product_row(product, context.today)
            for product in build(context, category) if product.low_use
        )
    return {
        "rule_id": rules_engine.RULE_LOW_USE,
        "products": rows,
        "count": len(rows),
        "definition": "Recorded at least 30 days ago, used no more than twice, and not used in the last 30 days.",
        "note": "Using these beats replacing them." if rows else "Nothing sitting unused right now.",
    }


def value_to_recover(context: ShelfContext, items: Sequence[InventoryItem]) -> Dict[str, Any]:
    """Shelf-only Value to Recover, reusing the Phase 3 estimator unchanged.

    Products with no purchase price are listed as missing that input rather than
    given an invented one.
    """
    estimates: List[Dict[str, Any]] = []
    total = 0.0
    for item in items:
        if item.category not in ROUTINE_CATEGORIES:
            continue
        owned = next((row for row in context.owned if row.id == item.id), None)
        if owned is None:
            continue
        estimate = inventory_service.value_to_recover(item, owned.details, context.today)
        estimates.append(estimate)
        if estimate["estimated_value"] is not None:
            total += float(estimate["estimated_value"])

    missing = [row for row in estimates if row["missing_inputs"]]
    return {
        "label": "Value to Recover",
        "scope": "Beauty and hair products only",
        "estimated_total": round(total, 2),
        "currency": "INR",
        "is_estimate": True,
        "metric_version": "v1",
        "items": sorted(estimates, key=lambda row: -(row["estimated_value"] or 0)),
        "items_missing_price": len(missing),
        "explanation": (
            "An estimate from the price you entered, how much is left, condition, how long since you used it "
            "and how close it is to expiry. Products with no price are excluded. It is never exact."
        ),
    }


def consistency(
    adherence_rows: Sequence[Any], expected_per_day: int, *, days: int = 14
) -> Dict[str, Any]:
    """How consistently the routine is being followed.

    No streaks. A streak turns a missed Tuesday into a failure, and this is a
    routine, not a game. Days followed out of days looked at, and that is all.
    """
    if expected_per_day <= 0 or days <= 0:
        return {
            "days_considered": days, "days_with_activity": 0, "steps_completed": 0,
            "note": "Nothing to measure yet.",
        }
    active_days = {row.done_on for row in adherence_rows if row.completed}
    completed = sum(1 for row in adherence_rows if row.completed)
    return {
        "days_considered": days,
        "days_with_activity": len(active_days),
        "steps_completed": completed,
        "note": (
            "Consistency over the last fortnight. Missing a day is not a problem — "
            "we do not count streaks."
        ),
    }


# --- Supplements --------------------------------------------------------------


def supplement_flags(context: ShelfContext) -> List[Dict[str, Any]]:
    """The only things this app is allowed to say about a supplement.

    Expiry status, a missing date, and a pointer at a professional when the
    user's own recorded purpose reads like a health question. No dosage, no
    effect, no interaction — the set is closed, and it is closed here.
    """
    rows: List[Dict[str, Any]] = []
    for item in context.by_category("supplements"):
        details = item.details or {}
        expiry = inventory_service.effective_expiry_from_details(details)
        days = (expiry - context.today).days if expiry else None

        flags: List[str] = []
        if days is None:
            flags.append(SUPPLEMENT_FLAG_NO_EXPIRY)
        elif days < 0:
            flags.append(SUPPLEMENT_FLAG_EXPIRED)
        elif days <= SUPPLEMENT_EXPIRING_DAYS:
            flags.append(SUPPLEMENT_FLAG_EXPIRING)

        purpose = details.get("user_entered_purpose") or ""
        if needs_professional(purpose):
            flags.append(SUPPLEMENT_FLAG_PROFESSIONAL)

        rows.append({
            "inventory_item_id": str(item.id),
            "display_name": item.display_name,
            "brand": item.brand,
            # Recorded verbatim and never interpreted.
            "user_entered_purpose": purpose or None,
            "use_frequency": details.get("use_frequency"),
            "expiry_date": expiry.isoformat() if expiry else None,
            "opened_date": details.get("opened_date"),
            "days_to_expiry": days,
            "flags": [{"flag": flag, "message": SUPPLEMENT_FLAG_TEXT[flag]} for flag in flags],
        })
    return rows


def supplement_summary(context: ShelfContext) -> Dict[str, Any]:
    """Supplement inventory. Tracking only, and it says so."""
    rows = supplement_flags(context)
    return {
        "supplements": rows,
        "count": len(rows),
        "flag_counts": {
            SUPPLEMENT_FLAG_EXPIRED: sum(1 for row in rows for flag in row["flags"] if flag["flag"] == SUPPLEMENT_FLAG_EXPIRED),
            SUPPLEMENT_FLAG_EXPIRING: sum(1 for row in rows for flag in row["flags"] if flag["flag"] == SUPPLEMENT_FLAG_EXPIRING),
            SUPPLEMENT_FLAG_NO_EXPIRY: sum(1 for row in rows for flag in row["flags"] if flag["flag"] == SUPPLEMENT_FLAG_NO_EXPIRY),
        },
        "tracked_fields": [
            "name", "brand", "what you said it is for", "expiry date",
            "opened date", "how often you take it", "label text",
        ],
        "we_do_not": [
            "Tell you how much to take",
            "Tell you to start or stop anything",
            "Say what a supplement will do for you",
            "Advise on interactions with medicines",
        ],
        "disclaimer": SUPPLEMENT_DISCLAIMER,
        "message": None if rows else "No supplements recorded. Add one and we will track its dates for you.",
    }
