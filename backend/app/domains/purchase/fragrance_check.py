"""Owned-first, deterministic V3-05.9 Fragrance Purchase check."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import InventoryItem, PerfumeDetail
from app.domains.purchase import service as purchase_service
from app.domains.purchase.contract import (
    FRAGRANCE_PURCHASE_CHECK_VERSION,
    FRAGRANCE_PURCHASE_VERDICT_VERSION,
)
from app.domains.purchase.fragrance_truth import (
    build_fragrance_candidate_truth,
    serialize_fragrance_candidate_truth,
)
from app.shared.errors.exceptions import ValidationFailedError


def _norm(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _fingerprint(material: dict[str, Any]) -> str:
    encoded = json.dumps(_json_safe(material), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnedFragrance:
    item: InventoryItem
    detail: PerfumeDetail

    @property
    def name_key(self) -> str:
        return _norm(self.item.display_name)

    @property
    def brand_key(self) -> str:
        return _norm(self.item.brand)

    def as_dict(self) -> dict[str, Any]:
        detail = self.detail
        return {
            "owned_item_id": str(self.item.id),
            "display_name": self.item.display_name,
            "brand": self.item.brand,
            "fragrance_family": detail.fragrance_family,
            "normalised_fragrance_family": _normalise_family(detail.fragrance_family),
            "concentration": detail.concentration,
            "season": list(detail.season or []),
            "occasion": list(detail.occasion or []),
            "remaining_percent": detail.remaining_percent,
            "usage_count": self.item.usage_count,
            "last_used_at": self.item.last_used_at.isoformat() if self.item.last_used_at else None,
        }


def _normalise_family(value: str | None) -> str | None:
    from app.domains.routines.ontology import normalise_fragrance_family
    return normalise_fragrance_family(value)


def _exact_match(candidate_name: str, candidate_brand: str | None, owned: OwnedFragrance) -> bool:
    if _norm(candidate_name) != owned.name_key:
        return False
    candidate_brand_key = _norm(candidate_brand)
    if not candidate_brand_key and not owned.brand_key:
        return True
    if not candidate_brand_key or not owned.brand_key:
        return False
    return candidate_brand_key == owned.brand_key


def _coverage(values: list[str], owned: list[OwnedFragrance], dimension: str) -> dict[str, list[str]]:
    covered: list[str] = []
    unknown: list[str] = []
    uncovered: list[str] = []
    for value in values:
        key = _norm(value)
        matches = [row for row in owned if any(_norm(item) == key for item in (getattr(row.detail, dimension) or []))]
        if matches:
            covered.append(value)
            continue
        if any(not (getattr(row.detail, dimension) or []) for row in owned):
            unknown.append(value)
        else:
            uncovered.append(value)
    return {"covered": covered, "unknown": unknown, "uncovered": uncovered}


def _headline(verdict: str) -> str:
    return {"buy": "This fills a fragrance gap.", "wait": "Hold this one for now.", "skip": "You can pass on this one."}[verdict]


def _explanation(reason: str) -> str:
    return {
        "candidate_untrusted": "Review and confirm the visible fragrance facts before deciding.",
        "multiple_exact_bottles_owned": "You already have more than one bottle of this exact fragrance.",
        "exact_bottle_available": "You already have this fragrance with plenty left to use.",
        "candidate_price_missing": "The candidate price is not recorded, so this cannot be compared safely.",
        "exact_replacement_ready": "Your confirmed exact bottle is nearly empty, so this can replace it.",
        "first_fragrance_gap": "You do not have a fragrance recorded yet, so this fills a real category gap.",
        "intended_use_missing": "Tell us where or when you would reach for this before deciding.",
        "owned_context_incomplete": "Some owned fragrance context is incomplete, so absence cannot safely be treated as a gap.",
        "declared_use_gap": "None of your confirmed fragrances currently covers one of the uses you identified for this.",
        "declared_use_already_covered": "You already have fragrances recorded for the occasions or seasons you have in mind.",
    }.get(reason, "This is a deterministic purchase decision from the facts you provided.")


def evaluate_fragrance_purchase(
    *, candidate: Any, owned: list[OwnedFragrance], draft_count: int = 0
) -> dict[str, Any]:
    truth = build_fragrance_candidate_truth(candidate)
    details = truth.details
    candidate_price = candidate.price
    exact = sorted((row for row in owned if _exact_match(candidate.display_name, candidate.brand, row)), key=lambda row: str(row.item.id))
    same_family = sorted([
        row for row in owned
        if truth.normalized_fragrance_family
        and _normalise_family(row.detail.fragrance_family) == truth.normalized_fragrance_family
    ], key=lambda row: str(row.item.id))
    occasions = list(details.get("occasion") or [])
    seasons = list(details.get("season") or [])
    occasion_coverage = _coverage(occasions, owned, "occasion")
    season_coverage = _coverage(seasons, owned, "season")
    covered = occasion_coverage["covered"] + season_coverage["covered"]
    unknown = occasion_coverage["unknown"] + season_coverage["unknown"]
    uncovered = occasion_coverage["uncovered"] + season_coverage["uncovered"]
    replacement_gap = bool(exact) and all(row.detail.remaining_percent is not None and row.detail.remaining_percent <= 15 for row in exact)
    if not truth.facts_trusted:
        verdict, reason = "wait", "candidate_untrusted"
    elif len(exact) >= 2 and any(row.detail.remaining_percent is None or row.detail.remaining_percent > 15 for row in exact):
        verdict, reason = "skip", "multiple_exact_bottles_owned"
    elif len(exact) == 1 and not replacement_gap:
        verdict, reason = "wait", "exact_bottle_available"
    elif candidate_price is None:
        verdict, reason = "wait", "candidate_price_missing"
    elif replacement_gap:
        verdict, reason = "buy", "exact_replacement_ready"
    elif not owned:
        verdict, reason = "buy", "first_fragrance_gap"
    elif not occasions and not seasons:
        verdict, reason = "wait", "intended_use_missing"
    elif unknown:
        verdict, reason = "wait", "owned_context_incomplete"
    elif uncovered:
        verdict, reason = "buy", "declared_use_gap"
    else:
        verdict, reason = "wait", "declared_use_already_covered"
    material = {
        "candidate_id": str(candidate.id),
        "candidate": {"display_name": candidate.display_name, "brand": candidate.brand, "details": details, "price": candidate_price, "currency": candidate.currency},
        "exact_owned": [{"id": str(row.item.id), "name": row.item.display_name, "brand": row.item.brand, "remaining_percent": row.detail.remaining_percent} for row in exact],
        "intended_use": {"occasion": sorted(occasions), "season": sorted(seasons)},
        "coverage": {"covered": sorted(covered), "unknown": sorted(unknown), "uncovered": sorted(uncovered)},
        "normalised_candidate_family": truth.normalized_fragrance_family,
        "same_family_owned": sorted(str(row.item.id) for row in same_family),
        "verdict_version": FRAGRANCE_PURCHASE_VERDICT_VERSION,
        "verdict": verdict,
        "primary_reason_code": reason,
    }
    fingerprint = _fingerprint(material)
    supporting = ["same_family_owned"] if same_family else []
    return {
        "fragrance_purchase_verdict_version": FRAGRANCE_PURCHASE_VERDICT_VERSION,
        "verdict": verdict,
        "headline": _headline(verdict),
        "explanation": _explanation(reason),
        "primary_reason_code": reason,
        "supporting_reason_codes": supporting,
        "decision_fingerprint": fingerprint,
        "normalised_candidate_family": truth.normalized_fragrance_family,
        "same_family_owned": [row.as_dict() for row in same_family],
        "replacement_gap": replacement_gap,
        "owned_options_to_use_first": [row.as_dict() for row in (exact if exact else same_family)],
        "missing_information": list(truth.missing_information) + (["draft_owned_context"] if draft_count else []),
    }


async def _owned_fragrances(session: AsyncSession, account_id: uuid.UUID) -> tuple[list[OwnedFragrance], int]:
    rows = (await session.execute(
        select(InventoryItem, PerfumeDetail)
        .join(PerfumeDetail, PerfumeDetail.item_id == InventoryItem.id)
        .where(InventoryItem.account_id == account_id, InventoryItem.category == "perfumes", InventoryItem.status == "active", InventoryItem.verification_state == "confirmed")
    )).all()
    draft_count = (await session.execute(
        select(InventoryItem.id).where(InventoryItem.account_id == account_id, InventoryItem.category == "perfumes", InventoryItem.status == "active", InventoryItem.verification_state != "confirmed")
    )).all()
    return [OwnedFragrance(item=item, detail=detail) for item, detail in rows], len(draft_count)


async def resolve_fragrance_purchase_check(
    session: AsyncSession,
    *, account_id: uuid.UUID, candidate_id: uuid.UUID,
) -> dict[str, Any]:
    candidate = await purchase_service.owned_purchase_candidate(session, account_id, candidate_id)
    if candidate.category != "perfumes":
        raise ValidationFailedError("This candidate is not eligible for the Fragrance purchase strategy.", field="category")
    try:
        truth = build_fragrance_candidate_truth(candidate)
    except ValueError as exc:
        raise ValidationFailedError(
            "This Fragrance candidate contains unsupported details and must be corrected before checking.",
            field="details",
        ) from exc
    if not truth.facts_trusted:
        raise ValidationFailedError(
            "Review and confirm the visible fragrance facts before checking this candidate.",
            field="verification_state",
        )
    owned, draft_count = await _owned_fragrances(session, account_id)
    verdict = evaluate_fragrance_purchase(candidate=candidate, owned=owned, draft_count=draft_count)
    details = truth.details
    exact = [row for row in owned if _exact_match(candidate.display_name, candidate.brand, row)]
    covered = []
    unknown = []
    uncovered = []
    for dimension in ("occasion", "season"):
        result = _coverage(list(details.get(dimension) or []), owned, dimension)
        covered.extend(result["covered"]); unknown.extend(result["unknown"]); uncovered.extend(result["uncovered"])
    collection = {
        "owned_perfume_count": len(owned),
        "draft_perfume_count": draft_count,
        "normalised_candidate_family": truth.normalized_fragrance_family,
        "exact_owned": [row.as_dict() for row in exact],
        "same_family_owned": verdict["same_family_owned"],
        "intended_use": {"occasion": list(details.get("occasion") or []), "season": list(details.get("season") or [])},
        "coverage": {"covered": covered, "unknown": unknown, "uncovered": uncovered},
        "owned_options_to_use_first": verdict["owned_options_to_use_first"],
    }
    return {
        "fragrance_purchase_check_version": FRAGRANCE_PURCHASE_CHECK_VERSION,
        "strategy": "fragrance_purchase",
        "candidate_truth": serialize_fragrance_candidate_truth(candidate),
        "collection_context": collection,
        "verdict": verdict,
    }


__all__ = ["OwnedFragrance", "evaluate_fragrance_purchase", "resolve_fragrance_purchase_check"]
