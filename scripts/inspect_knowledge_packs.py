"""Offline inventory and contract check for the version-controlled knowledge packs.

Run it from anywhere in the repository:

    python scripts/inspect_knowledge_packs.py
    python scripts/inspect_knowledge_packs.py --json

Exit code is the whole answer: ``0`` means every pack in the repository
satisfies the structural contract, non-zero means at least one does not and
the report names which file and which field.

This tool reads committed source and nothing else. Candidate pack files are
read as text and parsed statically; **no pack module is imported or executed**,
so a pack that has grown an import-time side effect is inventoried without
that side effect happening. It opens no database connection, makes no network
call, reads no credential and needs no environment configuration — running it
with the production environment variables absent, empty or wrong produces
exactly the same output as running it with them set, and there is a test that
holds that. It needs none of the backend's runtime dependencies either.

It also decides nothing about customers. It never compiles a manifest, never
prepares, publishes, activates or deactivates a release, and never reaches
the Step 8H release lifecycle. Inventory is not activation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.knowledge_packs.inspection import (  # noqa: E402
    InspectionResult,
    as_json_payload,
    inspect_packs,
)


def _render_human(result: InspectionResult) -> str:
    lines: list[str] = []
    if result.packs:
        lines.append(f"{len(result.packs)} knowledge pack(s):")
        for descriptor in result.packs:
            lines.append("")
            lines.append(f"  {descriptor.pack_id}")
            lines.append(f"    module     {descriptor.module}")
            lines.append(f"    domain     {descriptor.domain}")
            lines.append(f"    category   {descriptor.category}")
            lines.append(f"    reason key {descriptor.reason_key}")
            lines.append(f"    compiler   {descriptor.compiler_name}")
    else:
        lines.append("No knowledge packs found.")

    lines.append("")
    if result.ok:
        lines.append("Contract check: OK")
    else:
        lines.append(f"Contract check: FAILED ({len(result.errors)} finding(s))")
        for error in result.errors:
            lines.append(f"  {error.code}  {error.module}  ({error.field})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List the knowledge packs committed to this repository and check that each "
            "one still satisfies the structural contract the release workflow relies on. "
            "Parses source statically: no pack is imported, no database, no network, "
            "no credentials."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the machine-readable report instead of the human one.",
    )
    args = parser.parse_args(argv)

    result = inspect_packs()

    if args.as_json:
        print(json.dumps(as_json_payload(result), indent=2, sort_keys=True))
    else:
        print(_render_human(result))

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
