"""Who the caller actually is, at the network level.

One implementation, used by every route that records or rate-limits by address.
It used to be two identical copies — one in ``deps.py``, one in
``supabase_auth.py`` — each returning ``request.client.host``.

``request.client.host`` is the *peer* that opened the socket. Direct from a
phone that is the caller; behind a platform router it is the router, the same
value for every user in the world. That is not a detail: it collapses the
invite rate limiter into one shared bucket (one attacker locks out everyone)
and makes the audit trail's address column identical on every row.

The header that carries the real caller, ``X-Forwarded-For``, is written by
whoever is in front of us — including the caller, who can put anything in it.
Reading it unconditionally is worse than ignoring it: it turns a shared bucket
into no bucket at all, because every request can claim a fresh address.

So it is read only when the deployment says how many proxies sit in front, via
``TRUSTED_PROXY_HOPS``:

* ``0`` (the default) — nothing in front, or nothing we trust. The peer
  address is used, exactly as before this module existed.
* ``1`` — one trusted proxy. That proxy *appends* the peer it saw to the
  header, so the last entry is the real caller, and a caller who sends a
  forged header only pushes their own address further right. Values the caller
  supplies end up to the LEFT of it and are never read.
* ``n`` — n trusted proxies, so the caller is n entries from the right.

Counting from the right is the whole security property. The left-most entry is
the one every naive implementation reads, and it is the one the caller writes.

Set it to the number of proxies your platform actually puts in front of the
container — for a single managed router (Render, Heroku, Fly, App Runner) that
is ``1``. Leave it at ``0`` anywhere the container can be reached directly,
because then the header proves nothing.
"""
from __future__ import annotations

import ipaddress

from fastapi import Request

from app.config import TRUSTED_PROXY_HOPS

FORWARDED_FOR_HEADER = "x-forwarded-for"


def _valid_address(value: str) -> str | None:
    """The value, if it really is an address.

    Entries in this header are attacker-influenced. A junk entry must not reach
    a rate-limit key or an audit row, where it would be indistinguishable from
    a real address.
    """
    candidate = value.strip()
    if not candidate:
        return None
    # A proxy may write "host:port" for IPv4, or "[v6]:port".
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1:candidate.index("]")]
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The caller's address, or ``None`` when it cannot be established."""
    peer = request.client.host if request.client else None

    if TRUSTED_PROXY_HOPS < 1:
        return peer

    forwarded = request.headers.get(FORWARDED_FOR_HEADER)
    if not forwarded:
        # Configured for a proxy, but the request did not come through one.
        return peer

    entries = [entry for entry in (part.strip() for part in forwarded.split(",")) if entry]
    if len(entries) < TRUSTED_PROXY_HOPS:
        # Fewer hops than configured: something is in front that we did not
        # expect. Trusting the left-most entry here is exactly the mistake this
        # module exists to avoid, so fall back to the peer.
        return peer

    return _valid_address(entries[-TRUSTED_PROXY_HOPS]) or peer


__all__ = ["client_ip"]
