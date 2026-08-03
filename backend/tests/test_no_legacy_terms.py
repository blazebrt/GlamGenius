"""Absence regression — the Supabase cutover must not silently regress.

This module enumerates strings and identifiers that were deliberately
removed by the Supabase cutover (Mongo runtime, V1 identity bridge,
payment stack). If any of them reappears in active source, the test
fails with a per-file, per-line offender list.

The check is intentionally string-based. A grep for ``motor`` catching
an unrelated variable named ``motor`` is far cheaper than a real MongoDB
import creeping back in unnoticed.

Explicit exclusions:

- ``docs/`` — the audit and cutover reports name what was removed by design.
- ``docs/stabilisation/SUPABASE_HARDENING_AUDIT.md`` in particular is expected to contain most of these terms.
- This test file itself.
- ``docs/architecture/*.md`` — some files reference what was removed for the same reason.
- ``memory/`` — progress trackers narrate the removal.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories to scan
SCAN_ROOTS = [REPO_ROOT / "backend" / "app", REPO_ROOT / "backend" / "tests"]

# Files this scan touches (source only — not compiled artifacts, not vendored libs).
FILE_PATTERN = re.compile(r"\.(py)$")

# Exclude the test files whose whole purpose is to name removed terms in
# absence-of assertions.
EXCLUDE_FILES = {
    Path(__file__).resolve(),
    (REPO_ROOT / "backend" / "tests" / "test_schema_regression.py").resolve(),
}

# Substrings that must not appear in any active source line.
# Each is a plain substring; case-insensitive.
FORBIDDEN = [
    # Mongo runtime
    "motor.motor_asyncio",
    "import pymongo",
    "from pymongo",
    "MONGO_URL",
    # V1 identity bridge
    "account_links",
    "AccountLink",
    "v1_user_id",
    "get_or_create_account",
    # V1 route surface
    "backend.routes",
    "/api/auth/",
    "/api/users",
    "/api/subscription",
    # Payment stack (extended §4 hardening — every remnant term)
    "app.domains.billing",
    "razorpay",
    "SUBSCRIPTIONS_AVAILABLE",
    "SUBSCRIPTIONS_UNAVAILABLE",
    "SubscriptionsUnavailable",
    "BILLING_PROVIDER",
    "PAYMENTS_ENABLED",
    "plus_monthly",
    "plus_yearly",
    "event_pass",
    "paywall",
    "checkout",
    "billingAvailable",
    "billing_available",
]


def _iter_source_files():
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if not FILE_PATTERN.search(path.name):
                continue
            if path.resolve() in EXCLUDE_FILES:
                continue
            # Test files legitimately name removed terms in absence assertions
            # (``assert "razorpay" not in body``). The scan targets active code,
            # not the tests that police it.
            if path.name.startswith("test_"):
                continue
            yield path


@pytest.mark.parametrize("needle", FORBIDDEN)
def test_forbidden_term_absent(needle: str) -> None:
    """Every forbidden term must be absent from active backend source."""
    lowered = needle.lower()
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lowered in line.lower():
                offenders.append((path, lineno, line.strip()))
    if offenders:
        rendered = "\n".join(
            f"  {path.relative_to(REPO_ROOT)}:{lineno}  →  {line}"
            for path, lineno, line in offenders
        )
        raise AssertionError(
            f"Forbidden term {needle!r} appeared in active source:\n"
            f"{rendered}\n"
            f"See docs/stabilisation/SUPABASE_HARDENING_AUDIT.md for what was removed."
        )
