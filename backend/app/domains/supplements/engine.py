"""Pure, deterministic owned-supplement utility decisions."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from app.domains.routines.safety import needs_professional

SUPPLEMENT_UTILITY_VERSION = "vc-07-v1"
SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION = "vc-07-r1"
COMING_UP_DAYS = 90

# Deliberately small, explicit, reviewed identities. Unknown terms retain only
# their deterministic normalized spelling; no runtime synonym invention occurs.
# Label spellings that resolve to a canonical component key. The two original
# entries are kept verbatim; the rest come from the absorption knowledge base,
# which owns the compound forms and their Indian label spellings, so there is
# one place to add a form rather than two that can drift apart.
REVIEWED_ALIASES: dict[str, tuple[str, str]] = {
    "ascorbic acid": ("vitamin c", "Vitamin C"),
    "l ascorbic acid": ("vitamin c", "Vitamin C"),
}



def normalize_component(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def component_identity(value: str) -> tuple[str, str]:
    normalized = normalize_component(value)
    return REVIEWED_ALIASES.get(normalized, (normalized, value.strip() or normalized))
def _load_knowledge_aliases() -> None:
    """Fold the knowledge base's aliases in, without letting it shadow these two."""
    from app.domains.supplements.knowledge import raw_aliases  # noqa: PLC0415 - deferred by design

    for alias, key, nutrient in raw_aliases():
        REVIEWED_ALIASES.setdefault(normalize_component(alias), (key, nutrient))

_load_knowledge_aliases()


def _amount_text(value: Any) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def expiry_state(expiry: date | None, today: date) -> str:
    if expiry is None:
        return "unknown"
    days = (expiry - today).days
    if days < 0:
        return "past"
    if days <= COMING_UP_DAYS:
        return "coming_up"
    return "current"


def _fact_payload(fact: Any) -> dict[str, Any]:
    key = fact.canonical_component_key or fact.normalized_name
    return {
        "id": str(fact.id),
        "raw_name": fact.raw_name,
        "normalized_name": fact.normalized_name,
        "canonical_component_key": key,
        "amount": _amount_text(fact.amount),
        "unit": fact.unit,
        "serving_text": fact.serving_text,
        "source": fact.source,
        "verification_state": fact.verification_state,
        "confidence": fact.confidence,
    }


def build_utility(items: list[dict[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    """Build stable customer-safe utility output from owned item facts."""
    now = today or date.today()
    summaries: list[dict[str, Any]] = []
    confirmed_groups: dict[str, dict[str, Any]] = {}
    for item in sorted(items, key=lambda row: (row["display_name"].casefold(), row["id"])):
        expiry = item.get("expiry_date")
        if isinstance(expiry, str):
            expiry = date.fromisoformat(expiry)
        facts = sorted(item.get("facts", []), key=lambda row: (row.normalized_name, str(row.id)))
        confirmed = [fact for fact in facts if fact.verification_state == "confirmed" and item.get("verification_state") == "confirmed"]
        missing: list[str] = []
        if expiry is None:
            missing.append("expiry_date")
        if not facts:
            missing.append("label_components")
        if any(fact.verification_state != "confirmed" for fact in facts):
            missing.append("confirmation")
        if any(fact.amount is None for fact in confirmed):
            missing.append("amount")
        if any(fact.unit is None for fact in confirmed):
            missing.append("unit")
        if any(fact.serving_text is None for fact in confirmed):
            missing.append("serving_text")
        if not item.get("user_entered_purpose"):
            missing.append("purpose")
        component_rows = [_fact_payload(fact) for fact in facts]
        for fact in confirmed:
            # The canonical key is established at validated write time. Do not
            # derive a competing identity from customer-entered raw text here.
            key = fact.canonical_component_key or fact.normalized_name
            group = confirmed_groups.setdefault(
                key, {"component_key": key, "display_name": fact.raw_name, "items": {}},
            )
            product = group["items"].setdefault(
                item["id"],
                {"item_id": item["id"], "product_name": item["display_name"], "fact": _fact_payload(fact), "facts": []},
            )
            product["facts"].append(_fact_payload(fact))
        summaries.append({
            "inventory_item_id": item["id"],
            "display_name": item["display_name"],
            "brand": item.get("brand"),
            "user_entered_purpose": item.get("user_entered_purpose"),
            "use_frequency": item.get("use_frequency"),
            "expiry_date": expiry.isoformat() if expiry else None,
            "expiry_state": expiry_state(expiry, now),
            "days_to_expiry": (expiry - now).days if expiry else None,
            "label_facts": component_rows,
            "missing_information": sorted(set(missing)),
            "professional_boundary": bool(needs_professional(item.get("user_entered_purpose"))),
            "flags": [
                {"flag": "no_expiry_date", "message": "Expiry date not added."}
                for _ in [0] if expiry is None
            ] + [
                {"flag": "expired", "message": "Past the date you recorded."}
                for _ in [0] if expiry is not None and (expiry - now).days < 0
            ] + [
                {"flag": "expiring_soon", "message": "Date coming up."}
                for _ in [0] if expiry is not None and 0 <= (expiry - now).days <= COMING_UP_DAYS
            ] + [
                {"flag": "professional_question", "message": "This question is best discussed with a qualified professional."}
                for _ in [0] if needs_professional(item.get("user_entered_purpose"))
            ],
        })
    overlaps = []
    for group in confirmed_groups.values():
        group["items"] = sorted(group["items"].values(), key=lambda row: (row["product_name"].casefold(), row["item_id"]))
        if len(group["items"]) > 1:
            group["product_count"] = len(group["items"])
            overlaps.append(group)
    overlaps.sort(key=lambda row: row["component_key"])
    payload: dict[str, Any] = {
        "utility_version": SUPPLEMENT_UTILITY_VERSION,
        "normalization_version": SUPPLEMENT_COMPONENT_NORMALIZATION_VERSION,
        "supplements": summaries,
        "count": len(summaries),
        "flag_counts": {
            "expired": sum(1 for row in summaries for flag in row["flags"] if flag["flag"] == "expired"),
            "expiring_soon": sum(1 for row in summaries for flag in row["flags"] if flag["flag"] == "expiring_soon"),
            "no_expiry_date": sum(1 for row in summaries for flag in row["flags"] if flag["flag"] == "no_expiry_date"),
        },
        "tracked_fields": ["name", "brand", "your note", "expiry date", "label facts"],
        "we_do_not": [
            "Tell you how much to take",
            "Tell you to start or stop anything",
            "Say what a supplement will do for you",
            "Advise on interactions with medicines",
        ],
        "disclaimer": "Label tracking only. GlamGenius does not provide supplement dosage or medical advice.",
        "message": None if summaries else "No supplements recorded. Add one and we will keep the label facts you enter.",
        "overlaps": overlaps,
        "confirmation_needed": [
            {"inventory_item_id": row["inventory_item_id"], "display_name": row["display_name"], "label_facts": [fact for fact in row["label_facts"] if fact["verification_state"] != "confirmed"]}
            for row in summaries if any(fact["verification_state"] != "confirmed" for fact in row["label_facts"])
        ],
        "boundaries": [
            "Package label facts only; no instructions about how much to take, treatment, medical assessment, or interaction conclusions.",
            "Questions that need health guidance belong with a qualified professional.",
            "Amounts are shown per product and are never added into intake totals.",
            "Upper-limit, RDA, EAR, and deficiency comparisons are not active in this utility.",
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    return payload
