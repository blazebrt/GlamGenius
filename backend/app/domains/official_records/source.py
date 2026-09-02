from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime
from typing import Any

AUTHORITY_FSSAI_FOSCOS = "fssai_foscos"
RECORD_TYPE_FOOD_RECALL = "food_recall"
SOURCE_ADAPTER_VERSION = "fssai-foscos-food-recall.v1"
SOURCE_URL = "https://foscos.fssai.gov.in/food-recall"

# These are the column labels shown by the public FoSCoS Food Recall page.
_FIELDS = {
    "recall_id": "external_record_id", "fbo_name": "fbo_name", "brand_name": "brand_name",
    "batch_lot_no": "batch_lot", "product": "product_name", "reason_for_recall": "reason",
    "recall_start_date": "recall_start_date", "recall_status": "recall_status",
    "recall_termination_date": "recall_termination_date", "license_no": "licence",
    "license_type": "license_type", "nature_of_recall": "nature_of_recall",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return date.fromisoformat(str(value)) if fmt == "%Y-%m-%d" else datetime.strptime(str(value), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source_key, target_key in _FIELDS.items():
        value = row.get(source_key, row.get(target_key))
        result[target_key] = _date(value) if target_key.endswith("date") else _text(value)
    if not result.get("external_record_id"):
        raise ValueError("official recall row is missing recall_id")
    return result


def parse_recall_rows(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse a reviewed FoSCoS export/fixture without scraping or fuzzy logic."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        rows = payload["rows"]
    else:
        raise ValueError("official recall payload must contain a rows list")
    if not isinstance(rows, list):
        raise ValueError("official recall payload must contain rows")
    return [canonical_row(row) for row in rows if isinstance(row, dict)]


def stable_content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalise_licence(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    return "".join(character for character in unicodedata.normalize("NFKC", text) if character.isdigit()) or None


def normalise_batch(value: Any) -> str | None:
    text = _text(value)
    return unicodedata.normalize("NFKC", text).casefold() if text else None
