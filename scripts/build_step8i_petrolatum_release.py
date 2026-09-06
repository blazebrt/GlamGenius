"""Compile an exact published Step 8G JSON export into the reviewed Step 8I release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.knowledge_packs.petrolatum_dry_skin_v1 import (
    build_release_manifest_from_published_entry,
)
from app.domains.personal_decision_release.manifest import (
    manifest_content_hash,
    parse_release_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the reviewed Step 8I petrolatum release from published Step 8G JSON."
    )
    parser.add_argument("input", type=Path, help="Published Step 8G entry JSON file")
    parser.add_argument("--output", type=Path, help="Optional canonical manifest output file")
    args = parser.parse_args()

    entry = json.loads(args.input.read_text(encoding="utf-8"))
    manifest = build_release_manifest_from_published_entry(entry)
    encoded = json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    print(f"content_hash={manifest_content_hash(parse_release_manifest(manifest))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
