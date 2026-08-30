"""Scanning a packaged product: barcode, lookup, label capture, confirmation.

Store B's half of a scanned product. It deliberately holds **no** Open Food
Facts field — no product name, no ingredient list, no nutrition value. Those
live in Store A and are paired in at query time, because copying them here
would create the derived database ODbL's share-alike clause acts on. See
``docs/architecture/ODBL_DATA_WALL.md``.

What this domain owns: the barcode as a key, how much we trust the record, the
FSSAI licence read off the pack, and the link to whatever a person confirmed.
"""
from app.domains.product.confidence import CONFIDENCE_LEVELS, ProductConfidence
from app.domains.product.models import ProductRecord, ScanDevice, ScanEvent

__all__ = [
    "ProductConfidence",
    "CONFIDENCE_LEVELS",
    "ProductRecord",
    "ScanDevice",
    "ScanEvent",
]
