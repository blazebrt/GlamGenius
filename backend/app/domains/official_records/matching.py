from __future__ import annotations

from typing import Any

from app.domains.product.fssai import is_valid_licence

from .source import normalise_batch, normalise_licence


def match_recall(pack: dict[str, Any], record: dict[str, Any]) -> str:
    """Return a closed match state; exact licence and batch are mandatory."""
    licence = normalise_licence(pack.get("fssai_licence") or pack.get("licence"))
    batch = normalise_batch(pack.get("batch_number") or pack.get("batch_lot") or pack.get("batch_lot_no"))
    record_licence = normalise_licence(record.get("licence") or record.get("license_no"))
    record_batch = normalise_batch(record.get("batch_lot") or record.get("batch_lot_no"))
    if not licence or not batch or not record_licence or not record_batch:
        return "not_matched"
    if not is_valid_licence(licence) or not is_valid_licence(record_licence):
        return "not_matched"
    if licence != record_licence or batch != record_batch:
        return "identity_mismatch"
    for key in ("brand", "brand_name"):
        if pack.get(key) and record.get("brand_name") and normalise_batch(pack[key]) != normalise_batch(record["brand_name"]):
            return "identity_mismatch"
    for key in ("product_name", "name"):
        if pack.get(key) and record.get("product_name") and normalise_batch(pack[key]) != normalise_batch(record["product_name"]):
            return "identity_mismatch"
    return "matched"
