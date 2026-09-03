"""How long a cached Open Food Facts record stays trustworthy.

One definition, in one place, because two would drift. Their contributors
correct records and manufacturers reformulate packs, so a copy kept forever
pins a product to whatever its label said the first time anybody scanned it.

Two callers read this, and they read it for different purposes:

* **The product lookup** uses it to decide whether to *try* a refresh. It is
  deliberately forgiving afterwards — when their API is slow, down, or the
  phone is offline, a stale copy is a better answer than a blank screen, and
  the response already carries the confidence level that says how far to trust
  it.
* **The comparable alternative** uses it as a hard gate. Showing a product is
  not the same act as making a fresh comparative claim about two products, and
  a claim resting on an expired copy of somebody else's database is one we
  cannot stand behind. There the honest answer is that we do not know.

The difference is in the callers, not in the policy. The window is the same
number for both, which is the point of this module.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: How long a cached Open Food Facts record is served before we look again.
OFF_CACHE_TTL = timedelta(days=30)


def is_fresh(fetched_at: datetime | None, *, now: datetime | None = None) -> bool:
    """Is this cached copy inside the window?

    ``None`` is not fresh. A record we cannot date is a record we cannot vouch
    for, and treating an unknown age as recent is the failure this exists to
    prevent.

    ``now`` is a parameter so a test can control the clock; production passes
    nothing and the server's own time is used. A client-supplied timestamp
    never reaches here.
    """
    if fetched_at is None:
        return False
    moment = now or datetime.now(UTC)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - fetched_at) < OFF_CACHE_TTL


def is_stale(fetched_at: datetime | None, *, now: datetime | None = None) -> bool:
    """The inverse, for the lookup path that reads it that way round."""
    return not is_fresh(fetched_at, now=now)


__all__ = ["OFF_CACHE_TTL", "is_fresh", "is_stale"]
