"""A bounded in-process rate limiter, shared by the unauthenticated routes.

Everything here exists because an endpoint that anyone can call has to be
bounded in two directions at once, and the obvious implementation is bounded in
neither.

**Bounded in requests.** That is the visible job: refuse a caller who is going
too fast.

**Bounded in memory.** That is the one that bites. The first version of this
was a ``defaultdict`` of timestamps, swept never: every distinct key left an
entry that outlived its window forever, and part of the key is caller-chosen.
A hundred thousand distinct keys retained about 86 MiB that nothing freed, on
an instance with 512 MiB — so an unauthenticated caller could walk the process
out of memory without ever holding a credential. The table is now swept and
has a ceiling.

Two decisions in here are worth keeping:

*The sweep is throttled.* Sweeping is O(table). Doing it on every insertion
once the table is full turns a memory problem into a CPU one that is *cheaper
to trigger* than the bug it replaced. It runs at most once per interval, which
makes the flood path O(1) per request.

*A full table refuses.* When the ceiling is reached and everything in it is
live, new keys are refused rather than admitted untracked. Failing open there
would be the bypass: flood the table with throwaway keys, and the key you
actually wanted to hammer would no longer get a bucket.

The state is per process. That is honest for this deployment — one web service,
no shared cache — and it is a limiter, not a quota: the database constraints
behind it are what actually guarantee correctness.
"""
from __future__ import annotations

import time
from collections import deque


class FixedWindowLimiter:
    """Counts hits per key in a sliding window, with a bounded key table."""

    def __init__(
        self,
        *,
        window_seconds: float,
        max_per_window: int,
        max_keys: int = 20_000,
        sweep_interval_seconds: float = 5.0,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_per_window = max_per_window
        self.max_keys = max_keys
        self.sweep_interval_seconds = sweep_interval_seconds
        #: Exposed so tests can inspect and reset it.
        self.state: dict[str, deque[float]] = {}
        self._last_sweep_at = 0.0

    def reset(self) -> None:
        self.state.clear()
        self._last_sweep_at = 0.0

    def _sweep_expired(self, now: float) -> None:
        """Drop every key whose whole window has passed."""
        self._last_sweep_at = now
        stale = [
            key
            for key, bucket in self.state.items()
            if not bucket or now - bucket[-1] > self.window_seconds
        ]
        for key in stale:
            del self.state[key]

    def hit(self, key: str) -> bool:
        """Record one request for ``key``. True means it should be refused."""
        now = time.monotonic()
        bucket = self.state.get(key)

        if bucket is not None:
            while bucket and now - bucket[0] > self.window_seconds:
                bucket.popleft()
            if bucket:
                if len(bucket) >= self.max_per_window:
                    return True
                bucket.append(now)
                return False
            # Nothing live left: let the key go rather than keep an empty deque
            # for the lifetime of the process.
            del self.state[key]

        # A new key. Reclaim before growing, but never more than once per
        # interval — see the module docstring.
        if (
            len(self.state) >= self.max_keys
            and now - self._last_sweep_at >= self.sweep_interval_seconds
        ):
            self._sweep_expired(now)

        if len(self.state) >= self.max_keys:
            # Full of live entries: refuse rather than grow, and rather than
            # stop tracking. Deliberately not logged per call — a line per
            # request under a flood is its own denial of service, on the disk.
            return True

        self.state[key] = deque((now,))
        return False


__all__ = ["FixedWindowLimiter"]
