"""Whether a regulatory change comparison has sources a customer could open.

The Product Constitution is unconditional: *the app never makes a claim in its
own voice; it reports what a named, openable source says. No source, no claim.*

A sentence like

    "FSSAI changed the recall status from Initiated to Completed."

is **two** claims, not one. It asserts what the register says now, and it
asserts what the register said before. Each needs its own openable official
source. Publishing it with evidence for only the current value would be citing
today's page for yesterday's words.

Why the answer is currently "no"
--------------------------------
Walk the provenance the importer actually records
(:mod:`app.domains.official_records.service`):

* ``OfficialRecord.source_url`` and ``OfficialSourceFetch.source_url`` are both
  the module constant ``SOURCE_URL`` — ``https://foscos.fssai.gov.in/food-recall``.
  One string, identical for every record and every revision ever written. It is
  a genuine, openable, external official page, and it proves exactly nothing
  about *which* revision said what: it shows whatever the register shows today.
* ``OfficialSourceFetch.source_file_sha256`` is a digest of the downloaded
  workbook. A SHA-256 is **integrity metadata, not a locator**. It proves bytes
  did not change; nobody can open it.
* ``original_filename`` names a file on an operator's machine. It is not a URL,
  and the artifact itself is deliberately not retained — ingestion is manual
  and provenance-preserving, not an archive service.
* ``OfficialRecordRevision.payload`` is the transcription the claim is *derived
  from*. A claim cannot be its own independent support.

So no stored field can locate a **specific historical revision** of the
register. The comparison is internally derivable and not publishable, and that
is the correct, fail-closed result rather than a reason to relax the rule.

Restoring publication is a **provenance** change — persisting a revision-
specific openable official locator, such as a durable public archive URL for
the exact artifact, or an official per-record permalink that exposes history —
reviewed on its own terms. It is not something this module can grant.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .models import OfficialRecord, OfficialRecordRevision

#: Schemes a customer's browser can actually open.
_OPENABLE_SCHEMES = frozenset({"http", "https"})

#: Path prefixes belonging to this application. A locator pointing back into our
#: own API is the app citing itself, never an independent official source.
_OUR_OWN_PATHS = ("/api/", "/media/", "/scan/", "/internal/")


def is_openable_official_source(candidate: object) -> bool:
    """Whether ``candidate`` is a source a customer could actually open.

    Deliberately strict, because the failure mode is a claim that merely looks
    sourced. An absolute ``http(s)`` URL with a host, not pointing back into
    this application. A bare identifier, a file name, a SHA-256, a relative
    path or a ``file://`` URL is not a source, and saying so here is what stops
    the gate being satisfied by a string somebody assembled.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    parsed = urlparse(candidate.strip())
    if parsed.scheme.lower() not in _OPENABLE_SCHEMES or not parsed.netloc:
        return False
    return not any(parsed.path.startswith(prefix) for prefix in _OUR_OWN_PATHS)


#: Where a revision-specific official locator would be read from, if one were
#: ever persisted. Empty, because no such field exists on the ingestion chain
#: today — see the module docstring.
#:
#: ``OfficialRecord.source_url`` is deliberately **not** listed. It is openable
#: and it is official, and it is still not evidence for a historical value: one
#: constant register page shared by every revision cannot say which revision it
#: is evidence for. Adding it here would satisfy the gate while proving nothing,
#: which is precisely the failure this module exists to prevent.
_REVISION_LOCATOR_FIELDS: tuple[str, ...] = ()


def revision_source(revision: OfficialRecordRevision | None) -> str | None:
    """The openable source for what this exact revision said, or ``None``.

    ``None`` today for every revision. Written as a lookup rather than as
    ``return None`` so that the day a revision-specific locator is persisted,
    the change is to read it here — and it still has to pass
    :func:`is_openable_official_source`.
    """
    if revision is None:
        return None
    return next(
        (
            value
            for value in (getattr(revision, name, None) for name in _REVISION_LOCATOR_FIELDS)
            if is_openable_official_source(value)
        ),
        None,
    )


def regulatory_change_is_publishable(
    *,
    record: OfficialRecord,
    current: OfficialRecordRevision | None,
    previous: OfficialRecordRevision | None,
) -> bool:
    """Whether a change claim about these revisions may be shown to a customer.

    Every observation the claim rests on needs its own openable source. A
    comparison with one sourced side is not half-publishable: the sentence a
    customer reads is about both.

    ``record`` is accepted so that a future per-record official permalink can be
    consulted here without changing any caller. It is deliberately unused today
    for the reason in the module docstring — its ``source_url`` is a constant.
    """
    del record
    observations = [row for row in (current, previous) if row is not None]
    if not observations:
        return False
    return all(revision_source(row) is not None for row in observations)


__all__ = [
    "is_openable_official_source",
    "regulatory_change_is_publishable",
    "revision_source",
]
