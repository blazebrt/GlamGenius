"""The deterministic rules engine.

Everything here is a pure function of the user's own inventory, their own
declared allergies, and the reviewed rows in ``ontology.py``. No model is
consulted, nothing is random, and — the point of the whole phase — **every
warning carries the ``rule_id`` of the rule that produced it**.

That is not a convention, it is the shape of the data: a :class:`Finding`
cannot be constructed without one. A warning with no reviewed rule behind it is
not expressible.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.domains.inventory import service as inventory_service
from app.domains.recommendation.context import OwnedItem
from app.domains.routines import parser
from app.domains.routines.ontology import (
    CLIMATE_RULES,
    COMPATIBILITY_RULES,
    HAIR_SLOTS,
    INGREDIENT_BY_KEY,
    SEVERITY_AVOID,
    SEVERITY_CAUTION,
    SEVERITY_INFO,
    SKIN_SLOTS,
    SLOT_BY_KEY,
    CompatibilityRule,
    slot_for_product_type,
)
from app.domains.routines.parser import ParsedIngredient

# Rules that are generated rather than looked up still need a reviewed id, so
# the ones the engine itself raises are declared here.
RULE_ALLERGY = "rule.user_allergy"
RULE_DUPLICATE_SLOT = "rule.duplicate_slot"
RULE_MISSING_SLOT = "rule.missing_slot"
RULE_EXPIRED = "rule.product_expired"
RULE_EXPIRING = "rule.product_expiring"
RULE_NO_EXPIRY = "rule.no_expiry_recorded"
RULE_LOW_USE = "rule.low_use_product"
RULE_UNCONFIRMED = "rule.unconfirmed_ingredient"

ENGINE_RULES: dict[str, str] = {
    RULE_ALLERGY: "An ingredient you told us to avoid appears in a product you own.",
    RULE_DUPLICATE_SLOT: "More than one product covers the same routine step.",
    RULE_MISSING_SLOT: "A routine step has nothing in it.",
    RULE_EXPIRED: "A product is past the date recorded for it.",
    RULE_EXPIRING: "A product is close to the date recorded for it.",
    RULE_NO_EXPIRY: "A product has no expiry or opened date recorded.",
    RULE_LOW_USE: "A product is not being used.",
    RULE_UNCONFIRMED: "An ingredient was read at low confidence and needs confirming.",
}

EXPIRING_SOON_DAYS = 60


@dataclass
class Finding:
    """One thing worth telling the user.

    ``rule_id`` is required and is always a reviewed rule — either a row from
    ``ontology.py`` or one of :data:`ENGINE_RULES`.
    """

    rule_id: str
    severity: str
    headline: str
    detail: str
    evidence_note: str = ""
    item_ids: list[str] = field(default_factory=list)
    slot: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id, "severity": self.severity,
            "headline": self.headline, "detail": self.detail,
            "evidence_note": self.evidence_note,
            "item_ids": self.item_ids, "slot": self.slot,
        }


@dataclass
class ShelfProduct:
    """One owned beauty or hair product, with everything the engine needs."""

    item: OwnedItem
    slot: Optional[str]
    ingredients: list[ParsedIngredient] = field(default_factory=list)
    effective_expiry: Optional[date] = None
    low_use: bool = False

    @property
    def id(self) -> str:
        return str(self.item.id)

    @property
    def families(self) -> set[str]:
        return {row.family for row in self.ingredients}

    @property
    def confirmed_families(self) -> set[str]:
        return {row.family for row in self.ingredients if not row.needs_confirmation}

    @property
    def unconfirmed(self) -> list[ParsedIngredient]:
        return [row for row in self.ingredients if row.needs_confirmation]

    def days_to_expiry(self, today: date) -> Optional[int]:
        return None if self.effective_expiry is None else (self.effective_expiry - today).days


def build_products(
    items: Sequence[OwnedItem],
    category: str,
    low_use_ids: Optional[set[uuid.UUID]] = None,
) -> list[ShelfProduct]:
    """Turn owned inventory into what the engine reasons over.

    ``low_use_ids`` is passed in rather than computed here. Phase 3's
    :func:`inventory_service.is_low_use` needs the stored row — it reads the
    date the item was catalogued — and an :class:`OwnedItem` is a flattened
    view that does not carry it. Recomputing the rule from the flattened view
    would mean two definitions of "low use" that could drift apart.
    """
    unused = low_use_ids or set()
    products: list[ShelfProduct] = []
    for item in items:
        if item.category != category:
            continue
        details = item.details or {}
        products.append(ShelfProduct(
            item=item,
            slot=slot_for_product_type(details.get("product_type"), category)
                 or slot_for_product_type(item.subcategory, category)
                 or slot_for_product_type(item.display_name, category),
            ingredients=parser.parse_product(details),
            effective_expiry=inventory_service.effective_expiry_from_details(details),
            low_use=item.id in unused,
        ))
    return products


# --- Allergies ---------------------------------------------------------------


def _allergen_keys(allergies: Sequence[str]) -> dict[str, str]:
    """Map the user's own allergy words onto ingredient keys where we can."""
    resolved: dict[str, str] = {}
    for value in allergies or []:
        for row in parser.parse_declared([value], source=parser.SOURCE_USER):
            resolved[row.key] = str(value)
    return resolved


def allergy_findings(products: Sequence[ShelfProduct], allergies: Sequence[str]) -> list[Finding]:
    """A user-declared allergen found in a product they own.

    This is the only ``avoid`` severity the engine produces, and it is not a
    judgement of any kind — it is the user's own instruction, applied.
    """
    resolved = _allergen_keys(allergies)
    if not resolved:
        return []
    findings: list[Finding] = []
    for product in products:
        hits = [row for row in product.ingredients if row.key in resolved]
        if not hits:
            continue
        names = ", ".join(sorted({row.display_name for row in hits}))
        low_confidence = all(row.needs_confirmation for row in hits)
        findings.append(Finding(
            rule_id=RULE_ALLERGY,
            severity=SEVERITY_AVOID,
            headline=f"{product.item.display_name} contains {names}",
            detail=(
                "You listed this as something to avoid. We have left this product out of your routine."
                + (" We read this from the label at low confidence, so check the packet." if low_confidence else "")
            ),
            evidence_note="Matched against the allergies you entered on your profile.",
            item_ids=[product.id],
            slot=product.slot,
        ))
    return findings


def excluded_by_allergy(products: Sequence[ShelfProduct], allergies: Sequence[str]) -> set[str]:
    resolved = _allergen_keys(allergies)
    if not resolved:
        return set()
    return {
        product.id for product in products
        if any(row.key in resolved for row in product.ingredients)
    }


# --- Ingredient overlap and conflicts ----------------------------------------


def compatibility_findings(products: Sequence[ShelfProduct]) -> list[Finding]:
    """Pairs of owned products that the reviewed rules have something to say about."""
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()

    for index, first in enumerate(products):
        for second in products[index + 1:]:
            for rule in COMPATIBILITY_RULES:
                pairs = _rule_applies(rule, first, second)
                if not pairs:
                    continue
                key = (rule.rule_id, *sorted([first.id, second.id]))
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    headline=rule.headline,
                    detail=(
                        f"{first.item.display_name} and {second.item.display_name}. {rule.guidance}"
                    ),
                    evidence_note=rule.evidence_note,
                    item_ids=[first.id, second.id],
                ))
    return findings


def _rule_applies(rule: CompatibilityRule, first: ShelfProduct, second: ShelfProduct) -> bool:
    """Does this pair trigger this rule?

    Only **confirmed** ingredients trigger a rule. A low-confidence read is
    reported separately as something to confirm, never as a warning — a wrong
    warning is worse than a missing one.
    """
    left, right = first.confirmed_families, second.confirmed_families
    if rule.family_a == rule.family_b:
        # A same-family rule needs the family in both products.
        return rule.family_a in left and rule.family_b in right
    return (rule.family_a in left and rule.family_b in right) or (
        rule.family_b in left and rule.family_a in right
    )


def unconfirmed_findings(products: Sequence[ShelfProduct]) -> list[Finding]:
    """Ingredients read at low confidence, which the user should confirm."""
    findings: list[Finding] = []
    for product in products:
        rows = product.unconfirmed
        if not rows:
            continue
        names = ", ".join(sorted({row.display_name for row in rows})[:4])
        findings.append(Finding(
            rule_id=RULE_UNCONFIRMED,
            severity=SEVERITY_INFO,
            headline=f"Confirm what is in {product.item.display_name}",
            detail=(
                f"We think we read {names} on this label, but not confidently enough to act on it. "
                "Confirm or correct it and we will use it properly."
            ),
            evidence_note="Low-confidence label reading. Nothing is warned about until you confirm it.",
            item_ids=[product.id],
        ))
    return findings


def ingredient_overlap(products: Sequence[ShelfProduct]) -> list[dict[str, Any]]:
    """Which ingredients you already own more than once.

    Not a warning — most people have three products with glycerin and that is
    completely fine. It answers "do I already own this?" before buying again.
    """
    by_key: dict[str, list[ShelfProduct]] = {}
    for product in products:
        for row in product.ingredients:
            by_key.setdefault(row.key, []).append(product)
    overlaps = []
    for key, rows in by_key.items():
        if len(rows) < 2:
            continue
        ingredient = INGREDIENT_BY_KEY[key]
        overlaps.append({
            "ingredient_key": key,
            "display_name": ingredient.display_name,
            "family": ingredient.family,
            "product_count": len(rows),
            "item_ids": [row.id for row in rows],
            "note": f"You already own {len(rows)} products containing {ingredient.display_name}.",
        })
    return sorted(overlaps, key=lambda row: -row["product_count"])


# --- Routine shape -----------------------------------------------------------


def duplicate_slot_findings(products: Sequence[ShelfProduct]) -> list[Finding]:
    by_slot: dict[str, list[ShelfProduct]] = {}
    for product in products:
        if product.slot:
            by_slot.setdefault(product.slot, []).append(product)
    findings: list[Finding] = []
    for slot, rows in by_slot.items():
        if len(rows) < 2:
            continue
        spec = SLOT_BY_KEY[slot]
        findings.append(Finding(
            rule_id=RULE_DUPLICATE_SLOT,
            severity=SEVERITY_INFO,
            headline=f"{len(rows)} products for one step: {spec.label}",
            detail=(
                ", ".join(row.item.display_name for row in rows)
                + ". Nothing wrong with that — worth knowing before you buy another."
            ),
            evidence_note="Counted from the product types you recorded.",
            item_ids=[row.id for row in rows],
            slot=slot,
        ))
    return findings


def missing_slot_findings(products: Sequence[ShelfProduct], category: str) -> list[Finding]:
    """Only *required* steps count as a gap.

    A missing face oil is not a gap, it is a preference. Suggesting products for
    every empty slot would turn the app into a shop.
    """
    filled = {product.slot for product in products if product.slot}
    slots = SKIN_SLOTS if category == "beauty" else HAIR_SLOTS
    findings: list[Finding] = []
    for spec in slots:
        if not spec.required or spec.key in filled:
            continue
        findings.append(Finding(
            rule_id=RULE_MISSING_SLOT,
            severity=SEVERITY_INFO,
            headline=f"No {spec.label.lower()} recorded",
            detail=f"{spec.why} Add one you already own if there is one — we only mention the steps that matter.",
            evidence_note="Required routine step with nothing recorded against it.",
            slot=spec.key,
        ))
    return findings


# --- Expiry ------------------------------------------------------------------


def expiry_findings(products: Sequence[ShelfProduct], today: Optional[date] = None) -> list[Finding]:
    now = today or date.today()
    findings: list[Finding] = []
    for product in products:
        days = product.days_to_expiry(now)
        if days is None:
            findings.append(Finding(
                rule_id=RULE_NO_EXPIRY, severity=SEVERITY_INFO,
                headline=f"No date recorded for {product.item.display_name}",
                detail="Add the expiry date or the date you opened it and we can tell you when it is running out.",
                evidence_note="No expiry date and no opened date with a period-after-opening.",
                item_ids=[product.id],
            ))
        elif days < 0:
            findings.append(Finding(
                rule_id=RULE_EXPIRED, severity=SEVERITY_CAUTION,
                headline=f"{product.item.display_name} is past its date",
                detail=f"It was dated {product.effective_expiry.isoformat()}. Replace it rather than using it.",
                evidence_note="Computed from the expiry date, or the opened date plus period-after-opening, that you recorded.",
                item_ids=[product.id],
            ))
        elif days <= EXPIRING_SOON_DAYS:
            findings.append(Finding(
                rule_id=RULE_EXPIRING, severity=SEVERITY_INFO,
                headline=f"{product.item.display_name} runs out in {days} days",
                detail="Worth using it up rather than letting it go to waste.",
                evidence_note="Computed from the dates you recorded.",
                item_ids=[product.id],
            ))
    return findings


def low_use_findings(products: Sequence[ShelfProduct]) -> list[Finding]:
    rows = [product for product in products if product.low_use]
    if not rows:
        return []
    return [Finding(
        rule_id=RULE_LOW_USE, severity=SEVERITY_INFO,
        headline=f"{len(rows)} product{'s' if len(rows) > 1 else ''} you are not using",
        detail=", ".join(row.item.display_name for row in rows[:5]) + ". Using them beats replacing them.",
        evidence_note="Active for at least 30 days, used no more than twice, and not used in the last 30 days.",
        item_ids=[row.id for row in rows],
    )]


# --- Climate -----------------------------------------------------------------


def climate_notes(condition: Optional[str], slots_present: Iterable[str]) -> list[dict[str, Any]]:
    """Adjustments for the weather, keyed to steps the user actually has."""
    if not condition:
        return []
    present = set(slots_present)
    return [
        {"rule_id": rule.rule_id, "slot": rule.slot, "note": rule.note}
        for rule in CLIMATE_RULES
        if rule.condition == condition and rule.slot in present
    ]


# --- Ranking -----------------------------------------------------------------


def rank_for_slot(products: Sequence[ShelfProduct], slot: str, today: Optional[date] = None) -> list[ShelfProduct]:
    """Which owned product should fill a step.

    Owned-first is the whole premise, so this only ever ranks things the user
    has. Expired products drop to the bottom rather than being hidden, and
    something running out soon is *preferred* — using it up is the point.
    """
    now = today or date.today()
    rows = [product for product in products if product.slot == slot]

    def score(product: ShelfProduct) -> tuple:
        days = product.days_to_expiry(now)
        expired = days is not None and days < 0
        running_out = days is not None and 0 <= days <= EXPIRING_SOON_DAYS
        remaining = product.item.details.get("remaining_percent")
        return (
            0 if not expired else 1,          # expired last
            0 if running_out else 1,          # use up what is running out
            0 if product.low_use else 1,      # then what is going unused
            -(remaining if isinstance(remaining, int) else 50),
            product.item.display_name,
        )

    return sorted(rows, key=score)


def all_rule_ids() -> set[str]:
    """Every rule id the engine can emit. Used to prove no warning invents one."""
    return set(ENGINE_RULES) | {rule.rule_id for rule in COMPATIBILITY_RULES} | {
        rule.rule_id for rule in CLIMATE_RULES
    }
