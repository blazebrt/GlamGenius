#!/usr/bin/env python3
"""Historical cleanup for the V1 `image_base64` prefix on scan records.

Fix 12 (Work Package 2) — V1 previously stored the first 80 characters of the
base64 payload on every scan record as a "receipt that a photo was involved".
That receipt is a slice of the user's photo bytes; it rides along in every
`scan_history` and `trends` response and leaks user pixels through log lines
and admin queries. New writes have been fixed at source (`routes/scan.py`);
this script cleans up the existing records.

The script is deliberately conservative:

    * Idempotent. Running it twice is the same as running it once.
    * Dry-run by default. `--apply` is required for any write.
    * Counts what would change without printing the content.
    * Never logs or prints the payload it is removing.
    * Rollback limits documented below.

Usage
-----

    # From /app/backend, with MONGO_URL and DB_NAME set in the environment.
    python -m scripts.cleanup_v1_scan_image_prefixes            # dry-run
    python -m scripts.cleanup_v1_scan_image_prefixes --apply    # write

Rollback
--------

The write step is `$unset: {image_base64: ""}`. The removed field held at most
83 characters (80 payload characters + the literal "..."), the same slice
already present in the original scan record. **The slice is not recoverable
from other tables** — the ScanResult row is the only place it ever lived. If
the operator needs to prove a scan happened after the cleanup, use
`analysis.meta.provenance.ai_run_id` (still present) instead of the removed
prefix.

If a rollback is genuinely required (e.g. a customer support ticket that
depends on the prefix), the operator restores from the latest MongoDB backup
taken **before** running this script. The script itself provides no undo — it
is a one-way clean-up.

Exit codes
----------

    0 — success (dry-run always exits 0 unless the DB is unreachable).
    1 — connection failure, misconfiguration, or unexpected error.

The script prints its counts and a JSON summary line at the end so a CI
pipeline can parse the result without teasing apart the human-readable output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict

from motor.motor_asyncio import AsyncIOMotorClient


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"error: {name} must be an integer, got {raw!r}", file=sys.stderr)
        raise SystemExit(1)


async def _run(apply: bool, batch: int) -> Dict[str, int]:
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        print(
            "error: MONGO_URL and DB_NAME must be set in the environment.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # The filter matches any scan document that still has a non-null
    # `image_base64` field. New writes store `None` (see `routes/scan.py` after
    # Fix 12); a `None` value here would be a scan that already conforms.
    match_filter = {
        "image_base64": {"$ne": None, "$exists": True},
    }

    client = AsyncIOMotorClient(mongo_url)
    try:
        db = client[db_name]
        # We intentionally do not $exists:false-only match — some corrupt legacy
        # records store an empty string. Treat empty string as already-clean.
        total = await db.scans.count_documents({})
        to_clean = await db.scans.count_documents(match_filter)
        already_clean = total - to_clean

        summary: Dict[str, int] = {
            "scans_total": total,
            "scans_already_clean": already_clean,
            "scans_to_clean": to_clean,
            "scans_updated": 0,
        }

        if apply and to_clean > 0:
            # Batched update to avoid a long transaction on a large collection.
            # `$unset` is idempotent: running twice removes zero records the
            # second time. We do not log the removed value.
            updated_total = 0
            while True:
                cursor = db.scans.find(match_filter, projection={"_id": 1}).limit(batch)
                ids = [doc["_id"] async for doc in cursor]
                if not ids:
                    break
                result = await db.scans.update_many(
                    {"_id": {"$in": ids}},
                    {
                        "$unset": {"image_base64": ""},
                        "$set": {
                            "image_base64_cleaned_at": datetime.now(timezone.utc),
                        },
                    },
                )
                updated_total += result.modified_count
            summary["scans_updated"] = updated_total
        return summary
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove the V1 image_base64 prefix from scan records (Fix 12)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually write. Without this flag the script only counts what "
            "would change (dry-run)."
        ),
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=_int_env("CLEANUP_BATCH_SIZE", 500),
        help="Number of records updated per Mongo write (default 500).",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[image-prefix-cleanup] mode={mode} batch={args.batch}")

    summary = asyncio.run(_run(apply=args.apply, batch=args.batch))

    # Human-readable
    print(
        f"[image-prefix-cleanup] total={summary['scans_total']} "
        f"already_clean={summary['scans_already_clean']} "
        f"to_clean={summary['scans_to_clean']} "
        f"updated={summary['scans_updated']}"
    )
    # Machine-readable — the last line, so CI can parse it deterministically.
    print(json.dumps({"cleanup": "v1_scan_image_prefix", **summary, "mode": mode}))


if __name__ == "__main__":
    main()
