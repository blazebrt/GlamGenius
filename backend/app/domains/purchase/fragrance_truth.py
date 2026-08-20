"""Prospective Fragrance facts and the existing fragrance ontology boundary."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domains.purchase.contract import (
    FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
    PURCHASE_CANDIDATE_TRUTH_VERSION,
    PURCHASE_CATEGORY_LABELS,
)
from app.domains.recommendation.models import ShoppingCandidate
from app.domains.routines.ontology import normalise_fragrance_family

FRAGRANCE_CANDIDATE_DETAIL_KEYS = frozenset({
    "fragrance_family", "concentration", "season", "occasion", "longevity_user_reported",
})
FRAGRANCE_OWNED_ONLY_KEYS = frozenset({"usage_frequency", "remaining_percent"})


def validate_fragrance_candidate_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    incoming = dict(details or {})
    unknown = set(incoming) - FRAGRANCE_CANDIDATE_DETAIL_KEYS
    if unknown:
        raise ValueError(f"Unsupported Fragrance purchase detail: {sorted(unknown)[0]}")
    cleaned: dict[str, Any] = {}
    for key, value in incoming.items():
        if value is None or value == "":
            cleaned[key] = None
        elif key in {"season", "occasion"}:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must be a list of text values.")
            cleaned[key] = [item.strip()[:120] for item in value if item.strip()][:30]
        elif isinstance(value, str):
            cleaned[key] = value.strip()[:4000]
        else:
            cleaned[key] = value
    return cleaned


@dataclass(frozen=True, slots=True)
class FragrancePurchaseCandidateTruth:
    truth_version: str
    candidate_schema_version: str
    candidate_id: uuid.UUID
    category: str
    customer_category_label: str
    display_name: str
    brand: str | None
    details: dict[str, Any]
    normalized_fragrance_family: str | None
    verification_state: str
    source: str
    facts_trusted: bool
    review_required: bool
    missing_information: tuple[str, ...]


def build_fragrance_candidate_truth(candidate: ShoppingCandidate) -> FragrancePurchaseCandidateTruth:
    if candidate.category != "perfumes":
        raise ValueError("Fragrance candidate truth requires a perfume candidate.")
    details = validate_fragrance_candidate_details(candidate.details)
    trusted = candidate.verification_state in {"user_declared", "confirmed"}
    missing: list[str] = []
    if not details.get("fragrance_family"):
        missing.append("fragrance_family")
    return FragrancePurchaseCandidateTruth(
        truth_version=PURCHASE_CANDIDATE_TRUTH_VERSION,
        candidate_schema_version=FRAGRANCE_PURCHASE_CANDIDATE_SCHEMA_VERSION,
        candidate_id=candidate.id,
        category=candidate.category,
        customer_category_label=PURCHASE_CATEGORY_LABELS[candidate.category],
        display_name=candidate.display_name,
        brand=candidate.brand,
        details=details,
        normalized_fragrance_family=normalise_fragrance_family(details.get("fragrance_family")),
        verification_state=candidate.verification_state,
        source=candidate.source,
        facts_trusted=trusted,
        review_required=not trusted,
        missing_information=tuple(missing),
    )


def serialize_fragrance_candidate_truth(candidate: ShoppingCandidate) -> dict[str, Any]:
    truth = build_fragrance_candidate_truth(candidate)
    return {
        "candidate_truth_version": truth.truth_version,
        "fragrance_purchase_candidate_schema_version": truth.candidate_schema_version,
        "candidate": {
            "id": str(candidate.id),
            "source": candidate.source,
            "category": candidate.category,
            "subcategory": candidate.subcategory,
            "display_name": candidate.display_name,
            "brand": candidate.brand,
            "details": truth.details,
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
        "normalised_fragrance_family": truth.normalized_fragrance_family,
        "missing_information": list(truth.missing_information),
        "note": "This is a prospective purchase candidate, not an owned product or inventory input.",
    }


__all__ = [
    "FRAGRANCE_CANDIDATE_DETAIL_KEYS",
    "FRAGRANCE_OWNED_ONLY_KEYS",
    "FragrancePurchaseCandidateTruth",
    "build_fragrance_candidate_truth",
    "serialize_fragrance_candidate_truth",
    "validate_fragrance_candidate_details",
]
