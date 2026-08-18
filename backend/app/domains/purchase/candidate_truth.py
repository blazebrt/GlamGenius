"""Trustworthy, prospective Care purchase facts.

This module is intentionally a facts boundary, not a purchase decision engine.
It projects a candidate into the same Care slot and ingredient authorities used
for owned products while keeping prospective products separate from inventory.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domains.inventory.taxonomy import validate_details
from app.domains.purchase.contract import (
    CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_CATEGORY_LABELS,
)
from app.domains.recommendation.models import ShoppingCandidate
from app.domains.routines import parser
from app.domains.routines.ontology import slot_for_product_type

CARE_CANDIDATE_DETAIL_KEYS = frozenset({
    "product_type",
    "size",
    "purpose",
    "ingredients_text",
    "active_ingredients",
})
# Explicit aliases keep the contract discoverable under both the concise and
# fully qualified names used by review tooling.
CARE_PURCHASE_CANDIDATE_DETAIL_KEYS = CARE_CANDIDATE_DETAIL_KEYS

_CARE_CATEGORIES = frozenset({"beauty", "hair"})


def validate_care_candidate_details(category: str, details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the narrow prospective Care subset using inventory semantics."""
    if category not in _CARE_CATEGORIES:
        raise ValueError("Care purchase candidates must be Skin Care or Hair Care.")
    incoming = dict(details or {})
    unknown = set(incoming) - CARE_CANDIDATE_DETAIL_KEYS
    if unknown:
        raise ValueError(f"Unsupported Care purchase detail: {sorted(unknown)[0]}")
    # Inventory taxonomy owns the value conventions (lists, dates, bounds and
    # text normalization).  The key check above keeps owned-product fields out.
    return validate_details(category, incoming)


def resolve_care_slot(category: str, details: Mapping[str, Any], subcategory: str | None, display_name: str) -> str | None:
    """Resolve a Care role through the canonical routines ontology only."""
    for value in (
        details.get("product_type"),
        subcategory,
        display_name,
    ):
        slot = slot_for_product_type(value, category)
        if slot:
            return slot
    return None


@dataclass(frozen=True, slots=True)
class CarePurchaseCandidateTruth:
    truth_version: str
    candidate_id: uuid.UUID
    category: str
    customer_category_label: str
    display_name: str
    brand: str | None
    product_type: str | None
    care_slot: str | None
    verification_state: str
    source: str
    facts_trusted: bool
    review_required: bool
    recognised_ingredient_keys: tuple[str, ...]
    recognised_ingredient_families: tuple[str, ...]
    missing_information: tuple[str, ...]


def build_care_candidate_truth(candidate: ShoppingCandidate) -> CarePurchaseCandidateTruth:
    """Build a no-score truth projection for one account-owned Care candidate."""
    details = validate_care_candidate_details(candidate.category, candidate.details)
    trusted = candidate.verification_state in {"user_declared", "confirmed"}
    parsed = parser.parse_product(details) if trusted else []
    recognised_keys = tuple(sorted({row.key for row in parsed}))
    recognised_families = tuple(sorted({row.family for row in parsed}))
    missing: list[str] = []
    if not details.get("product_type"):
        missing.append("product_type")
    if not details.get("purpose"):
        missing.append("purpose")
    if not details.get("ingredients_text") and not details.get("active_ingredients"):
        missing.append("ingredients")
    for term in parser.unmatched_terms(details.get("ingredients_text")):
        missing.append(f"unrecognised_ingredient:{term}")
    if not resolve_care_slot(candidate.category, details, candidate.subcategory, candidate.display_name):
        missing.append("care_slot")
    return CarePurchaseCandidateTruth(
        truth_version=PURCHASE_CANDIDATE_TRUTH_VERSION,
        candidate_id=candidate.id,
        category=candidate.category,
        customer_category_label=PURCHASE_CATEGORY_LABELS[candidate.category],
        display_name=candidate.display_name,
        brand=candidate.brand,
        product_type=details.get("product_type"),
        care_slot=resolve_care_slot(candidate.category, details, candidate.subcategory, candidate.display_name),
        verification_state=candidate.verification_state,
        source=candidate.source,
        facts_trusted=trusted,
        review_required=not trusted,
        recognised_ingredient_keys=recognised_keys,
        recognised_ingredient_families=recognised_families,
        missing_information=tuple(missing),
    )


def serialize_care_candidate_truth(candidate: ShoppingCandidate) -> dict[str, Any]:
    truth = build_care_candidate_truth(candidate)
    details = validate_care_candidate_details(candidate.category, candidate.details)
    return {
        "candidate_truth_version": truth.truth_version,
        "care_purchase_candidate_schema_version": CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
        "candidate": {
            "id": str(candidate.id),
            "source": candidate.source,
            "category": candidate.category,
            "subcategory": candidate.subcategory,
            "display_name": candidate.display_name,
            "brand": candidate.brand,
            "details": details,
            "price": float(candidate.price) if candidate.price is not None else None,
            "currency": candidate.currency,
            "product_url": candidate.product_url,
            "media_asset_id": str(candidate.media_asset_id) if candidate.media_asset_id else None,
            "verification_state": candidate.verification_state,
            "uncertain_fields": candidate.uncertain_fields,
            "extraction_confidence": candidate.extraction_confidence,
            "ai_run_id": str(candidate.ai_run_id) if candidate.ai_run_id else None,
            "model_version": candidate.model_version,
            "prompt_version": candidate.prompt_version,
            "schema_version": candidate.schema_version,
            "in_inventory": False,
        },
        "review_required": truth.review_required,
        "facts_trusted": truth.facts_trusted,
        "care_slot": truth.care_slot,
        "missing_information": list(truth.missing_information),
        "recognised_ingredient_keys": list(truth.recognised_ingredient_keys),
        "recognised_ingredient_families": list(truth.recognised_ingredient_families),
        "note": "This is a prospective purchase candidate, not an owned product or Care routine input.",
    }


__all__ = [
    "CARE_CANDIDATE_DETAIL_KEYS",
    "CARE_PURCHASE_CANDIDATE_DETAIL_KEYS",
    "CARE_PURCHASE_CANDIDATE_SCHEMA_VERSION",
    "CarePurchaseCandidateTruth",
    "PURCHASE_CANDIDATE_TRUTH_VERSION",
    "build_care_candidate_truth",
    "resolve_care_slot",
    "serialize_care_candidate_truth",
    "validate_care_candidate_details",
]
