"""Import a manually downloaded public FoSCoS Food Recall XLSX export."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.domains.official_records import service
from app.shared.database.sql import get_sessionmaker


def parse_checked_at(value: str) -> datetime:
    try:
        checked_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source checked time must be ISO-8601") from exc
    if checked_at.tzinfo is None or checked_at > datetime.now(UTC).astimezone(checked_at.tzinfo):
        raise argparse.ArgumentTypeError("source checked time must be timezone-aware and not future")
    return checked_at


async def run(path: Path, source_checked_at: datetime) -> dict[str, object]:
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            fetch, counts = await service.ingest_recall_xlsx(session, path, source_checked_at=source_checked_at)
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await service.record_fetch_failure(session, source_checked_at=source_checked_at, error_code=str(exc))
            await session.commit()
            raise
    return {"source_fetch_id": str(fetch.id), "records_in_export": fetch.row_count,
        **counts, "source_checked_at": fetch.source_checked_at.isoformat(),
        "source_file_sha256": fetch.source_file_sha256}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a public FoSCoS Food Recall XLSX export")
    parser.add_argument("official_export", type=Path)
    parser.add_argument("--source-checked-at", required=True, type=parse_checked_at)
    args = parser.parse_args()
    try:
        sys.stdout.write(json.dumps(asyncio.run(run(args.official_export, args.source_checked_at)), sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(json.dumps({"error": type(exc).__name__}) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
