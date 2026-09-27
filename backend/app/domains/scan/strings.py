"""The customer copy the photo-check route adds for a repeated request, keyed.

``LEGAL_RULES.md``: never hard-code a user-facing string. The older sentences
in ``app/api/v2/scan.py`` predate this module and are left where they are; the
two below are new, so they live here under stable keys and the route resolves
them with :func:`text`.

Wording rules: state what happened and what was counted, nothing more.
"""
from __future__ import annotations

#: Bump whenever any sentence below changes.
SCAN_COPY_VERSION = "scan-copy.v1"

SCAN_COPY: dict[str, str] = {
    # A second request with the same key while the first is still running.
    "scan.idempotency.in_progress": (
        "This photo check is already running. Its result will appear in your scan "
        "history when it finishes. This request was not counted against your allowance."
    ),
    # The key's check succeeded and was counted, but its result is no longer
    # stored (it predates replay, or it was removed). Nothing is analysed again.
    "scan.idempotency.result_unavailable": (
        "This photo check was already completed and counted, and its result is no "
        "longer available to show again. It was not run or counted a second time."
    ),
}


def text(key: str) -> str:
    return SCAN_COPY[key]
