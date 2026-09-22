"""Whether a regulatory change comparison has official sources a customer could open.

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
register, and :func:`revision_source` returns ``None`` for every revision. The
comparison is internally derivable and not publishable, and that is the correct,
fail-closed result rather than a reason to relax the rule.

Two independent refusals
------------------------
The gate needs *both* of these to pass for every observation a claim rests on,
and today the first one never does:

1. :func:`revision_source` must produce a locator for that exact revision. It
   is hard-coded to ``None``. There is deliberately no list of field names it
   reads, because a field name cannot turn a URL into revision evidence:
   storing the generic register page in a column called ``archive_url`` would
   change nothing about what that page proves. Making this return anything is
   a **provenance** change — persisting a locator bound to the revision's own
   fetch and content hash — reviewed on its own terms.
2. :func:`is_official_revision_locator` must accept that locator under an
   explicit, narrow official-host policy. It exists so that the day (1) is
   built, an arbitrary external URL, a URL on a look-alike host, or the generic
   register page itself still cannot open the gate. URL *shape* is a necessary
   condition here, never a sufficient one: no URL policy can prove on its own
   that a page is revision-specific, which is exactly why (1) stays closed.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import OfficialRecord, OfficialRecordRevision
from .source import SOURCE_URL

#: The only official host this module accepts revision evidence from — the one
#: authority it supports. An exact match, never a suffix match, never "any
#: government domain", and never "anything that is not ours": a look-alike such
#: as ``foscos.fssai.gov.in.example`` or ``evil.foscos.fssai.gov.in`` is not
#: the regulator. Adding a host is a reviewed provenance decision, not a
#: configuration change.
OFFICIAL_REVISION_HOSTS: frozenset[str] = frozenset({"foscos.fssai.gov.in"})

#: The generic register page every record and every revision shares. Official,
#: openable, and precisely what cannot evidence a historical value — so it is
#: refused as revision evidence in every spelling: with or without a trailing
#: slash, a query string, a fragment, an explicit port or different case.
_GENERIC_REGISTER_PATH = urlsplit(SOURCE_URL).path.rstrip("/")

#: A revision locator must be a page *beneath* the register, not the register.
_REVISION_PATH_PREFIX = f"{_GENERIC_REGISTER_PATH}/"

#: Unreserved characters and ``/`` only. No percent-encoding, no ``;`` or
#: ``@`` tricks, nothing a reader has to decode before knowing what it points
#: at. Narrower than a real archive might need, on purpose: widening it belongs
#: to the change that introduces a real locator.
_SAFE_PATH = re.compile(r"[A-Za-z0-9._~/-]+")


def is_official_revision_locator(candidate: object) -> bool:
    """Whether ``candidate`` is shaped like revision evidence from the regulator.

    Every condition must hold:

    * a string with no whitespace;
    * the ``https`` scheme — not ``http``, not ``file``, not relative;
    * no user information, and no explicit port;
    * a host in :data:`OFFICIAL_REVISION_HOSTS`, exactly;
    * a path strictly beneath the generic register page, made only of safe
      characters, with no ``.`` or ``..`` segment;
    * no query string and no fragment.

    Necessary, never sufficient: passing this proves a URL is *on the official
    register and not its landing page*. It cannot prove the page shows one
    historical revision, which is why :func:`revision_source` stays closed.
    """
    if not isinstance(candidate, str) or not candidate or any(ch.isspace() for ch in candidate):
        return False
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() != "https" or port is not None:
        return False
    if parts.username is not None or parts.password is not None:
        return False
    if parts.hostname not in OFFICIAL_REVISION_HOSTS:
        return False
    if parts.query or parts.fragment or "#" in candidate or "?" in candidate:
        return False
    path = parts.path
    if not _SAFE_PATH.fullmatch(path or "-") or not path.startswith(_REVISION_PATH_PREFIX):
        return False
    if any(segment in {".", ".."} for segment in path.split("/")):
        return False
    return path.rstrip("/") != _GENERIC_REGISTER_PATH


def revision_source(revision: OfficialRecordRevision | None) -> str | None:
    """The openable official source for what this exact revision said: ``None``.

    ``None`` for every revision, by construction, until a reviewed provenance
    design persists a locator bound to the revision's own fetch and content
    hash. It deliberately does not read ``OfficialRecord.source_url`` or
    ``OfficialSourceFetch.source_url`` — the one generic register page — and it
    deliberately does not read a configurable list of field names, because
    moving that page into a differently named field would prove nothing new.
    """
    del revision
    return None


def regulatory_change_is_publishable(
    *,
    record: OfficialRecord,
    current: OfficialRecordRevision | None,
    previous: OfficialRecordRevision | None,
) -> bool:
    """Whether a change claim about these revisions may be shown to a customer.

    Every observation the claim rests on needs its own official revision
    locator, and each locator must independently satisfy
    :func:`is_official_revision_locator`. A comparison with one sourced side is
    not half-publishable: the sentence a customer reads is about both.

    ``record`` is accepted so that a future per-record official permalink can be
    consulted here without changing any caller. It is deliberately unused today
    for the reason in the module docstring — its ``source_url`` is a constant.
    """
    del record
    observations = [row for row in (current, previous) if row is not None]
    if not observations:
        return False
    return all(is_official_revision_locator(revision_source(row)) for row in observations)


__all__ = [
    "OFFICIAL_REVISION_HOSTS",
    "is_official_revision_locator",
    "regulatory_change_is_publishable",
    "revision_source",
]
