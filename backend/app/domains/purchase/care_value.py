"""Pure V3-05.4 financial context for trusted Care purchase candidates.

This module reports recorded candidate spend beside role coverage and the
existing owned-inventory ``Value to Recover`` estimates.  It never decides
whether a candidate should be purchased.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domains.purchase.contract import (
    CARE_PURCHASE_ASSESSMENT_VERSION,
    CARE_PURCHASE_VALUE_SCHEMA_VERSION,
    CARE_PURCHASE_VALUE_VERSION,
)

FINANCIAL_CONTEXT_AVAILABLE = "financial_context_available"
FINANCIAL_CONTEXT_PARTIAL = "financial_context_partial"
FINANCIAL_CONTEXT_UNAVAILABLE = "financial_context_unavailable"

RECOVERY_NONE = "no_low_use_eligible_owned_same_slot"
RECOVERY_ESTIMATED = "low_use_recovery_estimated"
RECOVERY_PARTIAL = "low_use_recovery_partially_estimated"
RECOVERY_UNQUANTIFIED = "low_use_recovery_unquantified"

CURRENCY_SAME = "same_currency_context"
CURRENCY_PRICE_MISSING = "candidate_price_missing"
CURRENCY_NONE = "no_quantified_recovery"
CURRENCY_MIXED = "mixed_currency_no_conversion"


def _value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _public_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        rows = [_json_value(item) for item in value]
        return sorted(rows, key=_canonical)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (uuid.UUID, date, datetime)):
        return str(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _recovery_row(row: Any) -> dict[str, Any]:
    estimated = _decimal(_value(row, "estimated_value"))
    missing = tuple(sorted(str(item) for item in (_value(row, "missing_inputs", ()) or ())))
    inputs = _json_value(_value(row, "inputs", {}) or {})
    return {
        "owned_item_id": str(_value(row, "owned_item_id", _value(row, "item_id"))),
        "display_name": _value(row, "display_name"),
        "low_use": True,
        "metric_version": _value(row, "metric_version"),
        "is_estimate": bool(_value(row, "is_estimate", True)),
        "estimated_value": _public_decimal(estimated),
        "currency": _value(row, "currency"),
        "missing_inputs": list(missing),
        "inputs": inputs,
        "explanation": _value(row, "explanation"),
        "_estimated_decimal": estimated,
    }


def _strip_internal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _role_context(assessment: Any) -> dict[str, Any]:
    dimensions = _value(assessment, "dimensions", {}) or {}
    role = dimensions.get("role_utility", {}) if isinstance(dimensions, Mapping) else {}
    return {
        "status": role.get("status", "role_unresolved"),
        "care_slot": role.get("care_slot"),
        "required": role.get("required", False),
        "is_gap": role.get("is_gap", False),
    }


def _redundancy_context(assessment: Any) -> dict[str, Any]:
    dimensions = _value(assessment, "dimensions", {}) or {}
    redundancy = dimensions.get("redundancy", {}) if isinstance(dimensions, Mapping) else {}
    return {
        "status": redundancy.get("status", "role_unresolved"),
        "eligible_owned_same_slot_count": int(redundancy.get("eligible_owned_same_slot_count", 0) or 0),
        "selected_owned_item_id": redundancy.get("selected_owned_item_id"),
    }


@dataclass(frozen=True, slots=True)
class CarePurchaseValueContext:
    value_version: str
    schema_version: str
    account_id: uuid.UUID | str
    candidate_id: uuid.UUID | str
    category: str
    plan_date: date
    candidate_truth_version: str
    care_purchase_assessment_version: str
    assessment_fingerprint: str
    value_context: Mapping[str, Any]
    value_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        assessment_category = self.category
        if assessment_category == "beauty":
            category_label = "Skin Care"
        elif assessment_category == "hair":
            category_label = "Hair Care"
        else:
            raise ValueError("Care value context category must be beauty or hair.")
        return {
            "care_purchase_value_version": self.value_version,
            "care_purchase_value_schema_version": self.schema_version,
            "strategy": "care_purchase",
            "account_id": str(self.account_id),
            "candidate_id": str(self.candidate_id),
            "category": assessment_category,
            "category_label": category_label,
            "plan_date": self.plan_date.isoformat(),
            "candidate_truth_version": self.candidate_truth_version,
            "care_purchase_assessment_version": self.care_purchase_assessment_version,
            "assessment_fingerprint": self.assessment_fingerprint,
            "value_fingerprint": self.value_fingerprint,
            "value_context": dict(self.value_context),
            "boundary": (
                "This is financial context from the price you provided and eligible products you already own. "
                "It is informational context, not a purchase recommendation."
            ),
        }


def project_care_purchase_value(
    assessment: Any,
    *,
    candidate_price: Any = None,
    candidate_currency: str | None = None,
    recovery_rows: Iterable[Any] = (),
    candidate_truth_version: str = "v3-05.1",
) -> CarePurchaseValueContext:
    """Project value context from existing assessment and inventory estimates."""
    category = _value(assessment, "category")
    if category not in {"beauty", "hair"}:
        raise ValueError("Care value context category must be beauty or hair.")
    for key in ("account_id", "candidate_id", "plan_date", "assessment_fingerprint"):
        if _value(assessment, key) in (None, ""):
            raise ValueError(f"Care value context requires usable {key}.")
    rows = tuple(sorted((_recovery_row(row) for row in recovery_rows), key=lambda row: row["owned_item_id"]))
    price = _decimal(candidate_price)
    candidate_spend = {
        "status": "recorded" if price is not None else "missing",
        "amount": _public_decimal(price),
        "currency": candidate_currency if price is not None else None,
    }
    quantified = tuple(row for row in rows if row["_estimated_decimal"] is not None)
    if not rows:
        recovery_status = RECOVERY_NONE
    elif len(quantified) == len(rows):
        recovery_status = RECOVERY_ESTIMATED
    elif quantified:
        recovery_status = RECOVERY_PARTIAL
    else:
        recovery_status = RECOVERY_UNQUANTIFIED

    if price is None and not quantified:
        financial_status = FINANCIAL_CONTEXT_UNAVAILABLE
    elif price is None or recovery_status in {RECOVERY_PARTIAL, RECOVERY_UNQUANTIFIED}:
        financial_status = FINANCIAL_CONTEXT_PARTIAL
    else:
        financial_status = FINANCIAL_CONTEXT_AVAILABLE

    if price is None:
        currency_status = CURRENCY_PRICE_MISSING
        comparison_available = False
    elif not quantified:
        currency_status = CURRENCY_NONE
        comparison_available = False
    elif any(row["currency"] != candidate_currency for row in quantified):
        currency_status = CURRENCY_MIXED
        comparison_available = False
    else:
        currency_status = CURRENCY_SAME
        comparison_available = True

    total = None
    if (
        price is not None
        and rows
        and len(quantified) == len(rows)
        and all(row["currency"] == candidate_currency for row in quantified)
    ):
        total = sum((row["_estimated_decimal"] for row in quantified), Decimal("0"))

    role = _role_context(assessment)
    redundancy = _redundancy_context(assessment)
    currency_context = {
        "status": currency_status,
        "comparison_available": comparison_available,
    }
    recovery = {
        "status": recovery_status,
        "items": [_strip_internal(row) for row in rows],
    }
    recovery_material = [
        {
            "owned_item_id": row["owned_item_id"],
            "metric_version": row["metric_version"],
            "estimated_value": row["_estimated_decimal"],
            "currency": row["currency"],
            "missing_inputs": row["missing_inputs"],
            "inputs": row["inputs"],
        }
        for row in rows
    ]
    value_context = {
        "status": financial_status,
        "candidate_spend": candidate_spend,
        "role_context": role,
        "redundancy_context": redundancy,
        "owned_value_recovery": recovery,
        "currency_context": currency_context,
        "estimated_recoverable_total": (
            {"amount": _public_decimal(total), "currency": candidate_currency, "is_estimate": True}
            if total is not None
            else None
        ),
    }
    material = {
        "value_version": CARE_PURCHASE_VALUE_VERSION,
        "schema_version": CARE_PURCHASE_VALUE_SCHEMA_VERSION,
        "account_id": str(_value(assessment, "account_id")),
        "candidate_id": str(_value(assessment, "candidate_id")),
        "plan_date": _value(assessment, "plan_date"),
        "assessment_fingerprint": _value(assessment, "assessment_fingerprint"),
        "candidate_price": price,
        "candidate_currency": candidate_currency,
        "role_context": role,
        "redundancy_context": redundancy,
        "recovery": recovery_material,
        "currency_context": currency_context,
        "estimated_recoverable_total": total,
    }
    fingerprint = hashlib.sha256(_canonical(material).encode()).hexdigest()
    plan_date = _value(assessment, "plan_date")
    if isinstance(plan_date, str):
        plan_date = date.fromisoformat(plan_date)
    return CarePurchaseValueContext(
        value_version=CARE_PURCHASE_VALUE_VERSION,
        schema_version=CARE_PURCHASE_VALUE_SCHEMA_VERSION,
        account_id=_value(assessment, "account_id"),
        candidate_id=_value(assessment, "candidate_id"),
        category=_value(assessment, "category"),
        plan_date=plan_date,
        candidate_truth_version=candidate_truth_version,
        care_purchase_assessment_version=_value(
            assessment, "care_purchase_assessment_version", CARE_PURCHASE_ASSESSMENT_VERSION
        ),
        assessment_fingerprint=_value(assessment, "assessment_fingerprint"),
        value_context=value_context,
        value_fingerprint=fingerprint,
    )


__all__ = [
    "CURRENCY_MIXED",
    "CURRENCY_NONE",
    "CURRENCY_PRICE_MISSING",
    "CURRENCY_SAME",
    "CarePurchaseValueContext",
    "FINANCIAL_CONTEXT_AVAILABLE",
    "FINANCIAL_CONTEXT_PARTIAL",
    "FINANCIAL_CONTEXT_UNAVAILABLE",
    "RECOVERY_ESTIMATED",
    "RECOVERY_NONE",
    "RECOVERY_PARTIAL",
    "RECOVERY_UNQUANTIFIED",
    "project_care_purchase_value",
]
