from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

AUTHORITY_FSSAI_FOSCOS = "fssai_foscos"
RECORD_TYPE_FOOD_RECALL = "food_recall"
SOURCE_ADAPTER_VERSION = "fssai-foscos-food-recall.xlsx.v1"
SOURCE_URL = "https://foscos.fssai.gov.in/food-recall"
SOURCE_FORMAT = "xlsx"
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_ROWS = 10_000
SHEET_NAME = "data"
HEADERS = (
    "Sr.No", "Recall Id", "FBO Name", "Brand Name", "Batch / Lot No.", "Product",
    "Reason for Recall", "Recall Start Date", "Recall Status", "Recall Termination Date",
    "License / Registration No.", "License Type [Central/State/Registration]", "Nature of Recall",
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _date(value: Any, *, optional: bool = False) -> date | None:
    text = _text(value)
    if text is None or (optional and text.casefold() == "na"):
        return None
    try:
        return datetime.strptime(text, "%d-%m-%Y").date()
    except ValueError as exc:
        raise ValueError("official recall date must be DD-MM-YYYY") from exc


def canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    recall_id = _text(row.get("Recall Id"))
    if recall_id is None:
        raise ValueError("official recall row is missing Recall Id")
    return {
        "external_record_id": recall_id, "fbo_name": _text(row.get("FBO Name")),
        "brand_name": _text(row.get("Brand Name")), "batch_lot": _text(row.get("Batch / Lot No.")),
        "product_name": _text(row.get("Product")), "reason": _text(row.get("Reason for Recall")),
        "recall_start_date": _date(row.get("Recall Start Date")),
        "recall_status": _text(row.get("Recall Status")),
        "recall_termination_date": _date(row.get("Recall Termination Date"), optional=True),
        "licence": _text(row.get("License / Registration No.")),
        "license_type": _text(row.get("License Type [Central/State/Registration]")),
        "nature_of_recall": _text(row.get("Nature of Recall")),
    }


def parse_recall_xlsx(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Parse the public FoSCoS Export to excel artifact without executing content."""
    if path.suffix.casefold() != ".xlsx" or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("unsupported_official_export")
    source_bytes = path.read_bytes()
    if not source_bytes.startswith(b"PK\\x03\\x04"):
        raise ValueError("unsupported_official_export")
    try:
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    except (InvalidFileException, OSError, ValueError) as exc:
        raise ValueError("unsupported_official_export") from exc
    if workbook.sheetnames != [SHEET_NAME] or workbook.vba_archive is not None:
        raise ValueError("unsupported_official_export")
    rows = list(workbook[SHEET_NAME].iter_rows(values_only=False))
    if not rows or len(rows) - 1 > MAX_SOURCE_ROWS:
        raise ValueError("invalid_official_export")
    if tuple(cell.value for cell in rows[0]) != HEADERS:
        raise ValueError("unexpected_official_export_schema")
    parsed = []
    for cells in rows[1:]:
        if any(cell.data_type == "f" for cell in cells):
            raise ValueError("invalid_official_export")
        row = {HEADERS[index]: cell.value for index, cell in enumerate(cells)}
        if any(value is not None for value in row.values()):
            parsed.append(canonical_row(row))
    return parsed, hashlib.sha256(source_bytes).hexdigest()


def stable_content_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def normalise_licence(value: Any) -> str | None:
    text = _text(value)
    return "".join(char for char in unicodedata.normalize("NFKC", text) if char.isdigit()) if text else None


def normalise_batch(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    normalized = unicodedata.normalize("NFKC", text).casefold()
    if normalized in {"na", "n/a", "nil", "none", "not applicable", "not available", "other", "others", "-"}:
        return None
    return None if set(normalized) == {"0"} else normalized
