from __future__ import annotations

from typing import Any

from app.domains.product.fssai import is_valid_licence

from .source import normalise_batch, normalise_identity_text, normalise_licence


def _identity_conflict(pack: dict[str, Any], record: dict[str, Any], keys: tuple[str, ...], field: str) -> bool:
    """True only when both sides state the same kind of identity and they differ.

    Missing text on either side is missing information, not a disagreement, so it
    never manufactures a conflict — and it never establishes a match either.
    """
    record_side = normalise_identity_text(record.get(field))
    if record_side is None:
        return False
    for key in keys:
        pack_side = normalise_identity_text(pack.get(key))
        if pack_side is not None and pack_side != record_side:
            return True
    return False


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
    # Licence and batch alone decide eligibility. Brand and product are a guard
    # against a real conflict, never evidence for a match on their own.
    if licence != record_licence or batch != record_batch:
        return "identity_mismatch"
    if _identity_conflict(pack, record, ("brand", "brand_name"), "brand_name"):
        return "identity_mismatch"
    if _identity_conflict(pack, record, ("product_name", "name"), "product_name"):
        return "identity_mismatch"
    return "matched"
