"""Publish Store A as a downloadable ODbL dataset.

ODbL's share-alike clause applies to a derived database. We do not build one —
see ``join.py`` — but we do hold a copy of Open Food Facts data, and offering
it back openly is both the licence's spirit and the cleanest possible proof
that nothing proprietary has leaked into it.

The export reads Store A and nothing else. It cannot reach Store B: it has no
session for it, and every record is checked against the allowlist on the way
out, so a column that slipped past the other defences is caught here too.

Run with ``python -m app.domains.off.export`` from ``backend/``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app import config
from app.domains.off.attribution import (
    ATTRIBUTION_TEXT,
    LICENSE_NAME,
    LICENSE_URL,
    SOURCE_URL,
)
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.off.wall import OFF_FIELDS, ProprietaryFieldError

logger = logging.getLogger(__name__)

DATA_FILE = "off_products.jsonl"
LICENSE_FILE = "LICENSE.txt"
MANIFEST_FILE = "manifest.json"

LICENSE_NOTICE = f"""{ATTRIBUTION_TEXT}.

This dataset is made available under the {LICENSE_NAME}.
Licence: {LICENSE_URL}
Source:  {SOURCE_URL}

You are free to copy, distribute and adapt this data, provided you attribute
Open Food Facts, keep any adapted database open under the same licence, and do
not use technical measures that restrict others from using it.

This file contains data derived from Open Food Facts only. It contains no
GlamGenius scores, verdicts, thresholds or customer data of any kind: those
live in a separate database and are never combined with this one.
"""


def _record(product: OffProduct) -> dict[str, Any]:
    """One row as it will be published."""
    return {
        "barcode": product.barcode,
        "product_name": product.product_name,
        "brands": product.brands,
        "ingredients_text": product.ingredients_text,
        "nutriments": product.nutriments,
        "categories": product.categories,
        "image_url": product.image_url,
        "quantity": product.quantity,
        "countries": product.countries,
        # The taxonomy arrays and their canonical encodings. Published like
        # everything else here: they are Open Food Facts data, and an export
        # that quietly withheld part of Store A would be a worse proof that
        # Store A holds only their data.
        "categories_hierarchy": product.categories_hierarchy,
        "countries_tags": product.countries_tags,
        "off_category_key": product.off_category_key,
        "off_listed_for_india": product.off_listed_for_india,
        "off_last_modified_t": product.off_last_modified_t,
    }


def _assert_publishable(row: dict[str, Any]) -> dict[str, Any]:
    """Check a row at the boundary, whatever produced it.

    Deliberately separate from ``_record``. A check that lives inside the
    serialiser only protects that serialiser: replace it and the check leaves
    with it. This runs on every row on its way to the file, so it holds however
    the row was built.
    """
    leaked = set(row) - OFF_FIELDS
    if leaked:
        raise ProprietaryFieldError(
            f"The export tried to publish fields that are not Open Food Facts data: "
            f"{sorted(leaked)}. Publishing this would put proprietary data under ODbL."
        )
    return row


async def export(destination: Path | None = None) -> dict[str, Any]:
    """Write the dataset, its licence and a manifest. Returns the manifest."""
    target = Path(destination or config.OFF_EXPORT_DIR)
    target.mkdir(parents=True, exist_ok=True)

    data_path = target / DATA_FILE
    digest = hashlib.sha256()
    count = 0

    factory = get_off_sessionmaker()
    async with factory() as session:
        rows = (await session.execute(select(OffProduct).order_by(OffProduct.barcode))).scalars()
        # newline="\n" so the bytes on disk are the bytes hashed on every
        # platform; the digest has to cover the record separators too, or the
        # published sha256 never matches the file anybody downloads.
        with data_path.open("w", encoding="utf-8", newline="\n") as handle:
            for product in rows:
                row = _assert_publishable(_record(product))
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                handle.write(payload)
                digest.update(payload.encode("utf-8"))
                count += 1

    (target / LICENSE_FILE).write_text(LICENSE_NOTICE, encoding="utf-8")

    manifest = {
        "dataset": DATA_FILE,
        "format": "JSON Lines, one product per line, UTF-8",
        "record_count": count,
        "sha256": digest.hexdigest(),
        "generated_at": datetime.now(UTC).isoformat(),
        "attribution": ATTRIBUTION_TEXT,
        "license": LICENSE_NAME,
        "license_url": LICENSE_URL,
        "source_url": SOURCE_URL,
        "fields": sorted(OFF_FIELDS - {"fetched_at"}),
        "contains_proprietary_data": False,
    }
    (target / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.info("off_export_complete records=%s path=%s", count, target)
    return manifest


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    manifest = await export()
    logger.info("off_export_manifest %s", manifest)


if __name__ == "__main__":
    asyncio.run(main())
