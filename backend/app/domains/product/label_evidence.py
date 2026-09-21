"""Whether a confirmed observation has a source a customer could open.

The Product Constitution is unconditional: *the app never makes a claim in its
own voice; it reports what a named, openable source says. No source, no claim.*

Step 12A's comparison is a claim about two physical packs — that one said
something the other did not. Publishing it to a customer therefore requires an
openable source for **each** observation, not for the engine that compared
them. This module is the only place that decides whether such a source exists.

Why the answer is currently always "no"
---------------------------------------
Walk the chain a confirmed observation is actually built from:

``POST /scan/label/transcribe`` takes a ``media_asset_id``, reads that private
``MediaAsset``, and records an ``AIRun`` with an ``AIRunOutput``.
``POST /scan/label/confirm`` takes the ``ai_run_id``, writes a ``ScanEvent``
carrying it, and writes a ``LabelSnapshot`` carrying the ``scan_event_id``.

Not one of ``AIRun``, ``AIRunOutput``, ``ScanEvent`` or ``LabelSnapshot``
persists the ``media_asset_id``. The photograph is reachable only from the
request that created the run, and that request is gone. Even if it were
stored, every media route is account-private
(``app/domains/media/service.py::get_owned_asset``), so the photograph behind a
stranger's observation is not something this caller may open — and the Product
Result answers an anonymous device token.

What is deliberately **not** evidence
-------------------------------------
* a ``version_number`` — our own counter;
* a ``content_fingerprint`` — our own integrity hash;
* ``observed_at`` — when we recorded something, not what it said;
* the stored transcription — the thing the claim is derived *from*, which
  cannot also be the independent source that supports it.

Each of those is identity or integrity metadata. Offering any of them as a
source would be the app speaking in its own voice while pointing at itself.

So this module answers "no", and the Product Result withholds the comparison.
The deterministic engine keeps working internally and its result stays exactly
as correct as it was; what changes is that an unsourceable claim is no longer
published. Restoring publication is a schema question — persisting a locator on
the confirmed-observation chain and giving it a lawful public reader — not a
question this layer can answer by relaxing.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.domains.product.models import LabelSnapshot

#: Schemes a customer's browser can actually open.
_OPENABLE_SCHEMES = frozenset({"http", "https"})

#: Path prefixes that belong to this application. A locator pointing back into
#: our own API is not an independent source: it is the app citing itself, and
#: for the media routes it is also a private object the caller cannot open.
#:
#: Matched on any host, not only ours. There is no configured public origin to
#: compare against, and a gate like this fails in the right direction: refusing
#: a real third-party URL that happens to live under ``/api/`` costs nothing
#: today and can be relaxed under review, whereas accepting one of ours because
#: it wore an unfamiliar hostname publishes an unsourced claim.
_OUR_OWN_PATHS = ("/api/", "/media/", "/scan/", "/internal/")


def is_openable_customer_source(candidate: object) -> bool:
    """Whether ``candidate`` is a source a customer could actually open.

    Deliberately strict, because the failure mode is a claim that looks
    sourced. An absolute ``http(s)`` URL with a host, that does not point back
    into this application. A relative path, a bare identifier, a UUID, a
    storage key, a ``file://`` URL or anything aimed at our own API is not a
    source, and saying so here is what stops the publication gate being
    satisfied by a string somebody assembled.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    parsed = urlparse(candidate.strip())
    if parsed.scheme.lower() not in _OPENABLE_SCHEMES or not parsed.netloc:
        return False
    return not any(parsed.path.startswith(prefix) for prefix in _OUR_OWN_PATHS)


def observation_source(snapshot: LabelSnapshot | None) -> str | None:
    """The openable source for what this confirmed observation says, or ``None``.

    ``None`` today for every snapshot, and the module docstring says why: the
    confirmed-observation chain persists no customer-openable locator for the
    photograph an observation was read from.

    This is written as a lookup rather than as ``return None`` so that the day
    a locator is persisted, the change is to read it here and it still has to
    pass :func:`is_openable_customer_source`. A future field holding
    ``"/api/v2/media/<uuid>"`` does not become evidence by being stored.
    """
    if snapshot is None:
        return None
    # Nothing on the chain carries one. ``getattr`` rather than an attribute
    # access so this keeps working — and keeps returning None — against a model
    # that has not grown the column.
    return next(
        (
            value
            for value in (getattr(snapshot, name, None) for name in _LOCATOR_FIELDS)
            if is_openable_customer_source(value)
        ),
        None,
    )


#: Where a locator would be read from if one were ever persisted. Empty,
#: because no such field exists on the confirmed-observation chain today.
_LOCATOR_FIELDS: tuple[str, ...] = ()


def comparison_is_publishable(
    *, current: LabelSnapshot | None, previous: LabelSnapshot | None
) -> bool:
    """Whether a change claim about these observations may be shown to a customer.

    Every observation the claim rests on needs its own openable source. A
    comparison with one sourced side is not half-publishable: the sentence a
    customer reads is about both packs.
    """
    observations = [row for row in (current, previous) if row is not None]
    if not observations:
        return False
    return all(observation_source(row) is not None for row in observations)


__all__ = [
    "comparison_is_publishable",
    "is_openable_customer_source",
    "observation_source",
]
