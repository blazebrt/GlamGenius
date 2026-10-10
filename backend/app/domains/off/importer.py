"""Bounded canonical OFF recovery into an empty, separate LOCAL Store A.

Validate the entire export before acquiring a Store A session. Provenance of
the original fetch is absent from the canonical export, so it remains NULL.
Run ``python -m app.domains.off.importer EXPORT_DIRECTORY`` locally only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app import config
from app.domains.off.attribution import ATTRIBUTION_TEXT, LICENSE_NAME, LICENSE_URL, SOURCE_URL
from app.domains.off.export import DATA_FILE, LICENSE_FILE, LICENSE_NOTICE, MANIFEST_FILE
from app.domains.off.local_recovery import LocalRecoveryAuthorityError, local_url, restored_store_b_authority
from app.domains.off.models import OffProduct
from app.domains.off.store import get_off_sessionmaker
from app.domains.off.wall import OFF_FIELDS, ProprietaryFieldError

MAX_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
CANONICAL_FIELDS = OFF_FIELDS - {"fetched_at"}


class InvalidOffExport(ValueError):
    """Rejected before any import transaction; never include row values."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidOffExport("Duplicate JSON field")
        result[key] = value
    return result


def _read_bounded(directory: Path, name: str, limit: int) -> bytes:
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise InvalidOffExport("Missing regular export artifact")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise InvalidOffExport("Export size limit exceeded")
    return data


def read_export(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return only validated OFF fields; never silently discard unknown ones."""
    directory = directory.resolve(strict=True)
    try:
        manifest = json.loads(_read_bounded(directory, MANIFEST_FILE, 16_384), object_pairs_hook=_unique_object)
        license_text = _read_bounded(directory, LICENSE_FILE, 16_384).decode("utf-8").replace("\r\n", "\n")
        payload = _read_bounded(directory, DATA_FILE, MAX_BYTES)
        expected = {
            "dataset": DATA_FILE, "format": "JSON Lines, one product per line, UTF-8",
            "attribution": ATTRIBUTION_TEXT, "license": LICENSE_NAME,
            "license_url": LICENSE_URL, "source_url": SOURCE_URL,
            "fields": sorted(CANONICAL_FIELDS), "contains_proprietary_data": False,
        }
        if not isinstance(manifest, dict) or set(manifest) != set(expected) | {"record_count", "sha256", "generated_at"}:
            raise InvalidOffExport("Noncanonical manifest")
        if any(manifest[key] != value for key, value in expected.items()) or license_text != LICENSE_NOTICE:
            raise InvalidOffExport("License, attribution or canonical fields mismatch")
        if isinstance(manifest["record_count"], bool) or not isinstance(manifest["record_count"], int) or not 0 <= manifest["record_count"] <= MAX_RECORDS:
            raise InvalidOffExport("Record limit exceeded")
        if hashlib.sha256(payload).hexdigest() != manifest["sha256"]:
            raise InvalidOffExport("Checksum mismatch")
        rows = []
        previous = ""
        for line in payload.decode("utf-8").splitlines(keepends=True):
            if len(rows) >= MAX_RECORDS:
                raise InvalidOffExport("Record limit exceeded")
            row = json.loads(line, object_pairs_hook=_unique_object)
            if not isinstance(row, dict) or set(row) != CANONICAL_FIELDS:
                raise ProprietaryFieldError("Recovery accepts canonical OFF fields only; fetched_at is unknown")
            canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            if line != canonical:
                raise InvalidOffExport("Noncanonical JSON Lines")
            barcode = row["barcode"]
            if not isinstance(barcode, str) or not previous < barcode or len(barcode) > 64:
                raise InvalidOffExport("Invalid, duplicate or unsorted barcode")
            previous = barcode
            rows.append(row)
        if len(rows) != manifest["record_count"]:
            raise InvalidOffExport("Record count mismatch")
        return manifest, rows
    except (UnicodeError, json.JSONDecodeError, TypeError, RecursionError, OverflowError) as error:
        raise InvalidOffExport("Malformed canonical export") from error


def _assert_local_separate_target() -> None:
    if config.APP_ENV not in {"test", "development"} or not config.OFF_DATABASE_URL:
        raise InvalidOffExport("Recovery requires an explicit local Store A URL")
    try:
        off = local_url(config.OFF_DATABASE_URL)
        main = make_url(config.POSTGRES_URL)
        def endpoint(url):
            host = 'loopback' if url.host in {'localhost', '127.0.0.1', '::1'} else url.host
            return host, url.port or 5432, url.database
        # Conservative early refusal only. The independent connected-cluster
        # comparison below remains the physical-separation authority.
        if endpoint(off) == endpoint(main):
            raise LocalRecoveryAuthorityError('Same application endpoint')
    except LocalRecoveryAuthorityError:
        raise InvalidOffExport("Recovery requires a physically separate local Store A database") from None


async def import_export(directory: Path) -> dict[str, Any]:
    """Compare actual local clusters using a separate read-only restored-B connection."""
    manifest, rows = read_export(directory)
    _assert_local_separate_target()
    try:
        store_b = await restored_store_b_authority()
    except LocalRecoveryAuthorityError:
        raise InvalidOffExport("Verified local Store B cluster authority is required") from None
    factory = get_off_sessionmaker()
    if factory.kw["bind"].url != make_url(config.OFF_DATABASE_URL):
        raise InvalidOffExport("Cached Store A engine does not match the recovery target")
    async with factory() as session, session.begin():
        try:
            connected = (await session.execute(text(
                "SELECT system_identifier::text FROM pg_catalog.pg_control_system()"
            ))).scalar_one()
        except Exception:
            raise InvalidOffExport("Connected Store A cluster authority unavailable") from None
        if not isinstance(connected, str) or not re.fullmatch(r"[1-9][0-9]{9,19}", connected) or connected == store_b["system_identifier"]:
            raise InvalidOffExport("Recovery requires physically separate PostgreSQL clusters")
        # Excludes concurrent writers between the emptiness check and insert.
        await session.execute(text("LOCK TABLE off_data.off_products IN ACCESS EXCLUSIVE MODE"))
        if (await session.execute(select(OffProduct.barcode).limit(1))).first() is not None:
            raise InvalidOffExport("Restore target must be empty")
        session.add_all(OffProduct(**row, fetched_at=None) for row in rows)
        await session.flush()
    return {"record_count": len(rows), "sha256": manifest["sha256"], "fetched_at": "NULL/unknown",
            "attribution": manifest["attribution"], "license": manifest["license"],
            "local_store_b_authority": store_b, "local_store_a_system_identifier": connected}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    asyncio.run(import_export(args.directory))


if __name__ == "__main__":
    main()
