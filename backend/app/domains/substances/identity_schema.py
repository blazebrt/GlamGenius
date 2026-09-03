"""The structured value an identity claim carries, and its strict parser.

An identity claim's ``structured_value`` holds the names a reviewed source
records for one entity, under a single ``substance_identity`` key:

    {
      "substance_identity": {
        "schema_version": "substance-identity.v1",
        "entity_kind": "defined_substance",
        "names": [
          {"name": "Niacinamide", "namespace": "inci",
           "language_tag": "und", "is_preferred": true}
        ]
      },
      "publication_verification": { ... }
    }

The payload is namespaced under its own key on purpose: the existing authoring
tool writes ``publication_verification`` into the same ``structured_value``, and
the two must coexist without either having to know about the other. Strictness
therefore applies *inside* ``substance_identity`` — unknown keys there are
rejected — while sibling top-level keys are left alone.

**Parsing fails closed and is never partial.** A payload that is malformed in
any way yields ``None``, and every caller treats that as "this claim cannot
support an identity", not as "use the parts that parsed". Half a classification
is not a classification: dropping a name we could not read would quietly widen
what the remaining names appear to establish.

What may never appear in this schema: function, benefit, risk, safety, efficacy,
concentration, dose, regulatory judgement, interaction. Each is a claim about a
substance in a context and needs its own evidence with its own applicability.
Smuggling one in here would make it a global unsourced property of the entity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.substances.enums import ENTITY_KINDS, NAME_NAMESPACES
from app.domains.substances.normalization import MAX_NAME_LENGTH, normalize_name

#: Bumped when the meaning of any field here changes. A claim written under an
#: older version is not silently reinterpreted: the parser rejects a version it
#: does not know, so an unreadable payload can never resolve.
SUBSTANCE_IDENTITY_SCHEMA_VERSION = "substance-identity.v1"

#: The top-level key inside ``EvidenceClaim.structured_value``.
IDENTITY_PAYLOAD_KEY = "substance_identity"

#: Keys permitted inside the payload, and inside one name entry. Anything else
#: is a rejection rather than something to ignore — an unexpected key means the
#: writer believed something this reader does not implement.
_PAYLOAD_KEYS = frozenset({"schema_version", "entity_kind", "names"})
_NAME_KEYS = frozenset({"name", "namespace", "language_tag", "is_preferred"})

#: One claim records the names a single source establishes for one entity, not a
#: catalogue. The bound keeps a malformed or pasted payload from becoming an
#: unbounded write, and keeps resolution's per-claim work constant.
MAX_NAMES_PER_CLAIM = 32

MAX_LANGUAGE_TAG_LENGTH = 32


@dataclass(frozen=True)
class IdentityName:
    """One name as a claim records it, with its server-computed lookup key."""

    name: str
    normalized_name: str
    namespace: str
    language_tag: str | None
    is_preferred: bool


@dataclass(frozen=True)
class SubstanceIdentity:
    """A fully parsed, structurally valid identity payload."""

    schema_version: str
    entity_kind: str
    names: tuple[IdentityName, ...]

    @property
    def preferred(self) -> IdentityName:
        """The single preferred name. V1 guarantees exactly one exists."""
        return next(name for name in self.names if name.is_preferred)


def _parse_name(raw: Any) -> IdentityName | None:
    if not isinstance(raw, dict):
        return None
    if set(raw) - _NAME_KEYS:
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if len(name) > MAX_NAME_LENGTH:
        return None
    # The lookup key is computed here, by the same function the resolver uses.
    # A caller-supplied key is never trusted: it is the one field that decides
    # what matches what, so letting it in would let a writer choose its own
    # matches.
    normalized = normalize_name(name)
    if normalized is None:
        return None
    namespace = raw.get("namespace")
    if namespace not in NAME_NAMESPACES:
        return None
    language_tag = raw.get("language_tag")
    if language_tag is not None:
        if not isinstance(language_tag, str) or not language_tag.strip():
            return None
        if len(language_tag) > MAX_LANGUAGE_TAG_LENGTH:
            return None
    is_preferred = raw.get("is_preferred", False)
    if not isinstance(is_preferred, bool):
        return None
    return IdentityName(
        name=name,
        normalized_name=normalized,
        namespace=namespace,
        language_tag=language_tag,
        is_preferred=is_preferred,
    )


def parse_identity(structured_value: Any) -> SubstanceIdentity | None:
    """Parse the identity payload out of a claim's ``structured_value``.

    Returns ``None`` — never a partial result — for any of: a non-object value,
    a missing or non-object payload, an unknown or missing schema version, an
    unknown key inside the payload, an unrecognised ``entity_kind``, no names, too
    many names, a malformed name entry, two names that normalise to the same key,
    or anything other than exactly one preferred name.
    """
    if not isinstance(structured_value, dict):
        return None
    payload = structured_value.get(IDENTITY_PAYLOAD_KEY)
    if not isinstance(payload, dict):
        return None
    if set(payload) - _PAYLOAD_KEYS:
        return None
    if payload.get("schema_version") != SUBSTANCE_IDENTITY_SCHEMA_VERSION:
        return None
    entity_kind = payload.get("entity_kind")
    if entity_kind not in ENTITY_KINDS:
        return None

    raw_names = payload.get("names")
    if not isinstance(raw_names, list) or not raw_names:
        return None
    if len(raw_names) > MAX_NAMES_PER_CLAIM:
        return None

    names: list[IdentityName] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_names:
        parsed = _parse_name(raw)
        if parsed is None:
            return None
        # Two entries that normalise to the same key in the same namespace would
        # make the claim's own name set ambiguous with itself, and would let one
        # row satisfy a consistency check meant for another.
        key = (parsed.normalized_name, parsed.namespace)
        if key in seen:
            return None
        seen.add(key)
        names.append(parsed)

    # Exactly one preferred name in V1. Zero leaves display with no answer;
    # more than one makes "the" preferred name a choice nobody reviewed.
    if sum(1 for name in names if name.is_preferred) != 1:
        return None

    return SubstanceIdentity(
        schema_version=SUBSTANCE_IDENTITY_SCHEMA_VERSION,
        entity_kind=entity_kind,
        names=tuple(names),
    )


def build_identity_payload(
    *, entity_kind: str, names: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a payload for authoring, validating it by parsing it back.

    Constructed and then re-parsed rather than trusted, so the writer and the
    reader can never disagree about what is valid.
    """
    payload = {
        IDENTITY_PAYLOAD_KEY: {
            "schema_version": SUBSTANCE_IDENTITY_SCHEMA_VERSION,
            "entity_kind": entity_kind,
            "names": names,
        },
    }
    if parse_identity(payload) is None:
        raise ValueError("identity payload is not valid under substance-identity.v1")
    return payload


__all__ = [
    "IDENTITY_PAYLOAD_KEY",
    "MAX_NAMES_PER_CLAIM",
    "SUBSTANCE_IDENTITY_SCHEMA_VERSION",
    "IdentityName",
    "SubstanceIdentity",
    "build_identity_payload",
    "parse_identity",
]
