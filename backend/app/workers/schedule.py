"""What the schedulers are expected to do, in one place.

Three facts kept drifting apart: how often each worker should run, what name
it writes its heartbeat under, and how late is too late. Before this module
they lived as literals in ``api/v2/admin.py``, in ``api/v2/config.py`` and
inside each worker, which meant readiness and the admin endpoint could — and
did — disagree about the same worker.

Nothing here installs a schedule. This repository cannot: the schedule belongs
to Supabase Cron, described in ``docs/OPERATIONS.md``. What this module does is
name the expectation, so that a run which never happened is *visible* rather
than merely absent.

**Why the worker names are stable constants.** The long-running deletion daemon
writes its heartbeat under ``account_deletion_worker_<hostname>``, which is
correct for a daemon: two pods are two workers and you want to see both. It is
wrong for the scheduled model, where every Render container restart would mint
a new row and the *previous* row would sit there looking permanently stale. A
scheduled worker is one logical worker however many containers serve it, so it
gets one deterministic name.
"""

from __future__ import annotations

import os
from typing import Final

#: The scheduled account-deletion worker. One row, not one per container.
ACCOUNT_DELETION_WORKER_NAME: Final = "account_deletion_worker"

#: The notification worker's existing name, unchanged. Re-exported here so
#: both scheduled workers are named from one place.
NOTIFICATION_WORKER_NAME: Final = "notification_worker"

#: Every five minutes. Someone who asked to be deleted is waiting, so this is
#: the tighter of the two.
ACCOUNT_DELETION_INTERVAL_SECONDS: Final = 300

#: Hourly, on the hour. The Today compiler behind it never sends late
#: catch-ups, so a missed hour is a missed hour rather than a burst.
NOTIFICATION_INTERVAL_SECONDS: Final = 3600

#: How many intervals late a run may be before it counts as missed or stale.
#: One spare interval absorbs a slow run or a scheduler that fires a little
#: late; two would hide a worker that has genuinely stopped.
MISSED_GRACE_MULTIPLIER: Final = 2

#: Name → expected interval, for every worker a scheduler must invoke.
SCHEDULED_WORKERS: Final[dict[str, int]] = {
    ACCOUNT_DELETION_WORKER_NAME: ACCOUNT_DELETION_INTERVAL_SECONDS,
    NOTIFICATION_WORKER_NAME: NOTIFICATION_INTERVAL_SECONDS,
}


def stale_after_seconds(interval_seconds: int) -> int:
    """How old a heartbeat may get before it is stale, for a given interval.

    Derived from the interval rather than stated separately, so the two can
    never drift into disagreeing about the same worker.
    """
    return interval_seconds * MISSED_GRACE_MULTIPLIER


#: The deletion heartbeat is stale once it is older than this.
ACCOUNT_DELETION_STALE_AFTER_SECONDS: Final = stale_after_seconds(
    ACCOUNT_DELETION_INTERVAL_SECONDS
)


def service_version() -> str:
    """Which build wrote this heartbeat.

    ``COMMIT_SHA`` first because the image sets it deliberately at build time.
    ``RENDER_GIT_COMMIT`` next because Render injects it for free and is right
    when the image did not set one. ``APP_VERSION`` after that, and
    ``"unknown"`` rather than an empty string, so an operations screen never
    shows a blank where a build should be.

    Operational only. It is reported by the admin worker endpoint and must not
    reach a customer response.
    """
    for name in ("COMMIT_SHA", "RENDER_GIT_COMMIT", "APP_VERSION"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return "unknown"


__all__ = [
    "ACCOUNT_DELETION_INTERVAL_SECONDS",
    "ACCOUNT_DELETION_STALE_AFTER_SECONDS",
    "ACCOUNT_DELETION_WORKER_NAME",
    "MISSED_GRACE_MULTIPLIER",
    "NOTIFICATION_INTERVAL_SECONDS",
    "NOTIFICATION_WORKER_NAME",
    "SCHEDULED_WORKERS",
    "service_version",
    "stale_after_seconds",
]
