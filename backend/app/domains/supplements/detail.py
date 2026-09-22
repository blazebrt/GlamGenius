"""One owned supplement, as a customer reads it. Pure and deterministic.

Step 13's customer surface. It says what the label lists as it was recorded,
who recorded it, what is still missing, which exact form is printed when the
printed name establishes one, calculated package chemistry when the form fixes
a formula, reviewed and published knowledge only when the governed reader
allows it, and which of the customer's other products list the same component.

What it never says, by construction rather than by wording:

* No amount is ever added to another, converted, or restated per day. Printed
  amounts are returned as printed, per component, with their printed unit.
* Serving text is returned under ``printed.serving_text`` exactly as recorded.
  Nothing here rewrites it into an instruction.
* Overlap is "the same component is listed on more than one product you
  recorded". It carries names, never amounts, and never a judgement.
* Unconfirmed facts are shown as unconfirmed and drive nothing: no overlap, no
  form, no chemistry, no knowledge.
* No identifier beyond the customer's own item and fact ids: no account id, no
  AI run id, no knowledge row id, no evidence claim id, no reviewer.

The payload carries stable codes; the words live in the app's keyed strings.
The only free text is the customer's own (names, notes, printed text) and the
published knowledge a reviewer approved, both returned verbatim.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from app.domains.supplements import boundary as supplement_boundary
from app.domains.supplements.engine import REVIEWED_ALIASES, SUPPLEMENT_UTILITY_VERSION, expiry_state
from app.domains.supplements.forms import (
    FORM_IDENTITY_VERSION,
    FormResolution,
    FormStatus,
    package_chemistry,
    resolve_form,
)
from app.domains.supplements.knowledge import COMPOUNDS
from app.domains.supplements.knowledge_reader import READER_VERSION, FormKnowledge, KnowledgeStatus

DETAIL_CONTRACT_VERSION = "step-13-v1"

#: A component counts for overlap, form, chemistry and knowledge only when both
#: the item and the fact are confirmed. Anything else is awaiting confirmation.
AWAITING_CONFIRMATION = "awaiting_confirmation"


def _nutrient_display_names() -> dict[str, str]:
    names: dict[str, str] = {compound.key: compound.nutrient for compound in COMPOUNDS}
    for key, display in REVIEWED_ALIASES.values():
        names.setdefault(key, display)
    return names


NUTRIENT_DISPLAY_NAMES: dict[str, str] = _nutrient_display_names()


def _amount_text(value: Any) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def provenance(source: str | None, verification_state: str | None) -> str:
    """Who put this fact here, and whether a person confirmed it.

    Stated plainly so manual entry never reads as scanned, official or
    machine-verified. There is no "verified" here: GlamGenius does not verify a
    supplement label against anything.
    """
    confirmed = verification_state == "confirmed"
    if source == "user_declared":
        return "you_entered" if confirmed else "you_entered_not_confirmed"
    if source == "photo_extracted":
        return "read_from_photo_confirmed_by_you" if confirmed else "read_from_photo_not_confirmed"
    return "unknown_source"


def _fact_key(fact: Any) -> str:
    return fact.canonical_component_key or fact.normalized_name


def counts(item: dict[str, Any], fact: Any) -> bool:
    """Whether a fact may drive anything beyond being displayed."""
    return item.get("verification_state") == "confirmed" and fact.verification_state == "confirmed"


def form_for(fact: Any) -> FormResolution:
    return resolve_form(fact.raw_name, canonical_component_key=fact.canonical_component_key)


def knowledge_pairs(item: dict[str, Any], facts: list[Any]) -> set[tuple[str, str]]:
    """The exact (nutrient, form) pairs knowledge may be read for on this item."""
    pairs: set[tuple[str, str]] = set()
    for fact in facts:
        if not counts(item, fact):
            continue
        pair = form_for(fact).knowledge_key
        if pair is not None:
            pairs.add(pair)
    return pairs


def _component(item: dict[str, Any], fact: Any, knowledge: dict[tuple[str, str], FormKnowledge]) -> dict[str, Any]:
    printed = {
        "name": fact.raw_name,
        "amount": _amount_text(fact.amount),
        "unit": fact.unit,
        "serving_text": fact.serving_text,
    }
    missing = [field for field, value in (
        ("amount", fact.amount), ("unit", fact.unit), ("serving_text", fact.serving_text),
    ) if value is None]
    row: dict[str, Any] = {
        "id": str(fact.id),
        "printed": printed,
        "provenance": provenance(fact.source, fact.verification_state),
        "confirmed": fact.verification_state == "confirmed",
        "counts_for_overlap": counts(item, fact),
        "missing_information": missing,
    }
    if not counts(item, fact):
        row["nutrient"] = {"status": AWAITING_CONFIRMATION}
        row["form"] = {"status": AWAITING_CONFIRMATION}
        row["package_chemistry"] = {"status": AWAITING_CONFIRMATION}
        row["published_knowledge"] = {"status": AWAITING_CONFIRMATION}
        return row

    key = _fact_key(fact)
    display = NUTRIENT_DISPLAY_NAMES.get(key)
    row["nutrient"] = (
        {"status": "identified", "key": key, "display_name": display}
        if display else {"status": "not_identified", "key": key, "display_name": None}
    )
    resolution = form_for(fact)
    row["form"] = {
        "status": resolution.status.value,
        "name": resolution.form.compound_form if resolution.status is FormStatus.EXACT and resolution.form else None,
    }
    row["package_chemistry"] = package_chemistry(resolution).as_dict()
    pair = resolution.knowledge_key
    decided = knowledge.get(pair) if pair is not None else None
    row["published_knowledge"] = (
        decided.customer_dict() if decided is not None and decided.status is KnowledgeStatus.PUBLISHED
        else {"status": KnowledgeStatus.NOT_ENOUGH_INFORMATION.value}
    )
    return row


def _overlaps(item: dict[str, Any], facts: list[Any], others: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which of the customer's other products list a component this one lists.

    Counted per product, never per row: one bottle listing "Vitamin C" and
    "Ascorbic acid" is one product. Names only; no amounts.
    """
    mine: dict[str, list[str]] = {}
    for fact in facts:
        if counts(item, fact):
            mine.setdefault(_fact_key(fact), []).append(fact.raw_name)
    groups: list[dict[str, Any]] = []
    for key in sorted(mine):
        other_products = []
        for other in sorted(others, key=lambda row: (row["display_name"].casefold(), row["id"])):
            if other["id"] == item["id"]:
                continue
            names = sorted({fact.raw_name for fact in other.get("facts", []) if counts(other, fact) and _fact_key(fact) == key})
            if names:
                other_products.append({
                    "inventory_item_id": other["id"], "product_name": other["display_name"], "printed_names": names,
                })
        if other_products:
            groups.append({
                "component_key": key,
                "nutrient_display_name": NUTRIENT_DISPLAY_NAMES.get(key),
                "printed_names_here": sorted(set(mine[key])),
                "other_products": other_products,
                "product_count": 1 + len(other_products),
            })
    return groups


def build_detail(
    item: dict[str, Any],
    *,
    others: list[dict[str, Any]],
    knowledge: dict[tuple[str, str], FormKnowledge],
    today: date | None = None,
) -> dict[str, Any]:
    """The customer detail for one owned supplement."""
    now = today or date.today()
    expiry = item.get("expiry_date")
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry)
    facts = sorted(item.get("facts", []), key=lambda fact: (fact.raw_name.casefold(), str(fact.id)))
    components = [_component(item, fact, knowledge) for fact in facts]

    missing: list[str] = []
    if expiry is None:
        missing.append("expiry_date")
    if not facts:
        missing.append("label_components")
    if item.get("verification_state") != "confirmed" or any(fact.verification_state != "confirmed" for fact in facts):
        missing.append("confirmation")

    decided = supplement_boundary.evaluate(item.get("user_entered_purpose"), question=False)
    payload: dict[str, Any] = {
        "contract_version": DETAIL_CONTRACT_VERSION,
        "utility_version": SUPPLEMENT_UTILITY_VERSION,
        "form_identity_version": FORM_IDENTITY_VERSION,
        "knowledge_reader_version": READER_VERSION,
        "item": {
            "inventory_item_id": item["id"],
            "display_name": item["display_name"],
            "brand": item.get("brand"),
            "user_entered_purpose": item.get("user_entered_purpose"),
            "provenance": provenance(item.get("source"), item.get("verification_state")),
            "confirmed": item.get("verification_state") == "confirmed",
        },
        "expiry": {
            "state": expiry_state(expiry, now),
            "date": expiry.isoformat() if expiry else None,
            "days_to_expiry": (expiry - now).days if expiry else None,
        },
        "components": components,
        "overlaps": _overlaps(item, facts, others),
        "missing_information": missing,
        "professional_boundary": {
            "boundary": decided.boundary,
            "reason": decided.rule,
            "message": decided.message if decided.boundary else None,
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload


__all__ = [
    "AWAITING_CONFIRMATION",
    "DETAIL_CONTRACT_VERSION",
    "NUTRIENT_DISPLAY_NAMES",
    "build_detail",
    "counts",
    "form_for",
    "knowledge_pairs",
    "provenance",
]
