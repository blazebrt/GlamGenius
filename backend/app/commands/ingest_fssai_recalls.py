"""Import a reviewed FoSCoS Food Recall JSON export.

V1 intentionally has no live scrape: FoSCoS' public recall page is rendered
in the browser, and this command imports only a reviewed official export in
the documented ``{"rows": [...]}`` representation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.domains.official_records import service
from app.shared.database.sql import get_sessionmaker


async def run(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        factory = get_sessionmaker()
        async with factory() as session:
            await service.record_fetch_failure(session, error_code="invalid_operator_export")
            await session.commit()
        raise ValueError("official export is not valid JSON") from exc
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            fetch = await service.ingest_recall_export(session, payload)
            await session.commit()
        except ValueError:
            await session.rollback()
            await service.record_fetch_failure(session, error_code="invalid_operator_export")
            await session.commit()
            raise
    rows = payload.get("rows", payload) if isinstance(payload, dict) else payload
    return {"fetch_id": 1 if fetch.id else 0, "records_in_export": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a reviewed FoSCoS Food Recall JSON export")
    parser.add_argument("official_export", type=Path)
    args = parser.parse_args()
    try:
        sys.stdout.write(json.dumps(asyncio.run(run(args.official_export)), sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 - command must return non-zero on import failure
        sys.stderr.write(json.dumps({"error": type(exc).__name__}) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
