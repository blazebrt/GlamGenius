"""The release manifest: strict schema, canonical form, and content hash.

A manifest is the whole reviewed bundle -- Step 8C semantic rules, Step 8E
policy rules and Step 8F explanation rules -- in one immutable structured
document. The three sets travel together because they are one decision chain:
a semantic rule with no policy changes what Step 8C matches while no reviewer
ever decided what should follow from it, and a policy with no explanation can
select an action that can never be shown. Activating them separately would let
production sit in a state nobody reviewed.

Three properties are load-bearing here.

**The schema is closed.** Every key is named, every type is checked, and an
unknown key is a parse failure rather than something carried along. A manifest
is read back out of a JSONB column and turned into the rules that decide what
a person is told, so "we did not recognise that field" must never mean "we
ignored it".

**Canonical form is independent of input order.** Two manifests with the same
reviewed contents produce the same bytes and therefore the same hash, whatever
order an author happened to type them in. Without that, re-uploading an
identical bundle would look like a different release.

**The hash is over the canonical form.** It is an immutability guard, not a
checksum for transport: a stored manifest that no longer hashes to its stored
hash has been edited outside the reviewed path, and the release is refused
rather than repaired.

Nothing in this module decides anything. It parses, orders, serialises and
hashes; the reviewed direction, action and reason are copied through exactly
as an author wrote them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.domains.personal_applicability import PersonalApplicabilityCategory
from app.domains.personal_decision_aggregation import PersonalSignalSet
from app.domains.personal_decision_explanation import (
    PersonalDecisionExplanationRule,
    build_explanation_index,
)
from app.domains.personal_decision_policy import (
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
    PersonalDecisionPolicyRule,
    build_policy_index,
)
from app.domains.personal_decision_semantics import (
    PersonalDecisionSemanticRule,
    PersonalDecisionSignal,
    build_rule_index,
)

#: The only manifest schema this code understands. A persisted manifest
#: carrying anything else is refused rather than guessed at -- an older shape
#: may mean something subtly different, and reinterpreting it under today's
#: rules is exactly how a reviewed bundle quietly changes meaning.
PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION = 1

#: Bounds, so a mistake cannot become an unbounded release. These are not
#: capacity limits reached by normal governed authoring; they are the point at
#: which a bundle has stopped being something a human reviewed line by line.
MAX_SEMANTIC_RULES = 512
MAX_POLICY_RULES = 512
MAX_EXPLANATION_RULES = 512
MAX_CANONICAL_MANIFEST_BYTES = 1024 * 1024

_MANIFEST_KEYS = frozenset({
    "schema_version",
    "semantic_rules",
    "policy_rules",
    "explanation_rules",
})

_SEMANTIC_KEYS = frozenset({
    "rule_id",
    "rule_version",
    "category",
    "substance_key",
    "claim_key",
    "claim_version",
    "signal",
})

_POLICY_KEYS = frozenset({
    "policy_id",
    "policy_version",
    "category",
    "semantic_rule_identities",
    "signal_set",
    "has_identity_unresolved",
    "has_identity_ambiguous",
    "has_personal_evidence_gap",
    "action",
})

_SEMANTIC_IDENTITY_KEYS = frozenset({"rule_id", "rule_version"})

_EXPLANATION_KEYS = frozenset({
    "explanation_id",
    "explanation_version",
    "policy_id",
    "policy_version",
    "action",
    "semantic_rule_id",
    "semantic_rule_version",
    "substance_key",
    "claim_key",
    "claim_version",
    "source_key",
    "source_locator",
    "reason_key",
})


class PersonalDecisionReleaseManifestError(ValueError):
    """The manifest is not a manifest. Nothing is built from it."""


@dataclass(frozen=True, slots=True)
class PersonalDecisionReleaseManifest:
    """One reviewed bundle, already in canonical order.

    The rule objects are the real Step 8C, 8E and 8F dataclasses rather than
    release-local copies, so a change to any of those contracts breaks this
    parser loudly instead of letting a stale shape through.
    """

    schema_version: int
    semantic_rules: tuple[PersonalDecisionSemanticRule, ...]
    policy_rules: tuple[PersonalDecisionPolicyRule, ...]
    explanation_rules: tuple[PersonalDecisionExplanationRule, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.semantic_rules or self.policy_rules or self.explanation_rules)


def _mapping(value: Any, *, where: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PersonalDecisionReleaseManifestError(f"{where} is not an object")
    keys = set(value)
    unknown = keys - allowed
    if unknown:
        raise PersonalDecisionReleaseManifestError(
            f"{where} carries unknown field(s) {sorted(unknown)}"
        )
    missing = allowed - keys
    if missing:
        raise PersonalDecisionReleaseManifestError(
            f"{where} is missing field(s) {sorted(missing)}"
        )
    return value


def _text(value: Any, *, where: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonalDecisionReleaseManifestError(f"{where} has a blank or non-string {field}")
    return value


def _positive_int(value: Any, *, where: str, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PersonalDecisionReleaseManifestError(f"{where} has a non-integer {field}")
    if value <= 0:
        raise PersonalDecisionReleaseManifestError(f"{where} has {field} {value}; versions start at 1")
    return value


def _flag(value: Any, *, where: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise PersonalDecisionReleaseManifestError(f"{where} has a non-boolean {field}")
    return value


def _member(enum_type: Any, value: Any, *, where: str, field: str) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise PersonalDecisionReleaseManifestError(
            f"{where} has an unrecognised {field} {value!r}"
        ) from error


def _sequence(value: Any, *, where: str, limit: int) -> Sequence[Any]:
    # `str` and `bytes` are sequences, and a bare string here would otherwise
    # be silently iterated character by character.
    if not isinstance(value, list):
        raise PersonalDecisionReleaseManifestError(f"{where} is not a list")
    if len(value) > limit:
        raise PersonalDecisionReleaseManifestError(
            f"{where} holds {len(value)} entries; at most {limit} may be reviewed as one release"
        )
    return value


def _locator(value: Any, *, where: str) -> str | None:
    """``None``, or the reviewer's exact string. Never trimmed, never coerced.

    A locator is how a reader finds the passage inside a document, and
    whitespace can be part of it. Normalising here would mean the citation a
    customer sees is not the one that was reviewed, so a blank-but-present
    locator is refused rather than tidied into ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PersonalDecisionReleaseManifestError(f"{where} has a malformed source_locator")
    return value


def _semantic_rule(value: Any, *, position: int) -> PersonalDecisionSemanticRule:
    where = f"semantic rule at position {position}"
    block = _mapping(value, where=where, allowed=_SEMANTIC_KEYS)
    return PersonalDecisionSemanticRule(
        rule_id=_text(block["rule_id"], where=where, field="rule_id"),
        rule_version=_text(block["rule_version"], where=where, field="rule_version"),
        category=_member(
            PersonalApplicabilityCategory, block["category"], where=where, field="category"
        ),
        substance_key=_text(block["substance_key"], where=where, field="substance_key"),
        claim_key=_text(block["claim_key"], where=where, field="claim_key"),
        claim_version=_positive_int(block["claim_version"], where=where, field="claim_version"),
        signal=_member(PersonalDecisionSignal, block["signal"], where=where, field="signal"),
    )


def _semantic_identities(value: Any, *, where: str) -> frozenset[tuple[str, str]]:
    entries = _sequence(value, where=f"{where} semantic_rule_identities", limit=MAX_SEMANTIC_RULES)
    identities: set[tuple[str, str]] = set()
    for position, entry in enumerate(entries):
        inner = f"{where} semantic identity at position {position}"
        block = _mapping(entry, where=inner, allowed=_SEMANTIC_IDENTITY_KEYS)
        identity = (
            _text(block["rule_id"], where=inner, field="rule_id"),
            _text(block["rule_version"], where=inner, field="rule_version"),
        )
        if identity in identities:
            raise PersonalDecisionReleaseManifestError(
                f"{where} names {identity[0]}@{identity[1]} more than once"
            )
        identities.add(identity)
    return frozenset(identities)


def _policy_rule(value: Any, *, position: int) -> PersonalDecisionPolicyRule:
    where = f"policy rule at position {position}"
    block = _mapping(value, where=where, allowed=_POLICY_KEYS)
    return PersonalDecisionPolicyRule(
        policy_id=_text(block["policy_id"], where=where, field="policy_id"),
        policy_version=_text(block["policy_version"], where=where, field="policy_version"),
        category=_member(
            PersonalDecisionPolicyCategory, block["category"], where=where, field="category"
        ),
        semantic_rule_identities=_semantic_identities(
            block["semantic_rule_identities"], where=where
        ),
        signal_set=_member(
            PersonalSignalSet, block["signal_set"], where=where, field="signal_set"
        ),
        has_identity_unresolved=_flag(
            block["has_identity_unresolved"], where=where, field="has_identity_unresolved"
        ),
        has_identity_ambiguous=_flag(
            block["has_identity_ambiguous"], where=where, field="has_identity_ambiguous"
        ),
        has_personal_evidence_gap=_flag(
            block["has_personal_evidence_gap"], where=where, field="has_personal_evidence_gap"
        ),
        action=_member(PersonalDecisionAction, block["action"], where=where, field="action"),
    )


def _explanation_rule(value: Any, *, position: int) -> PersonalDecisionExplanationRule:
    where = f"explanation rule at position {position}"
    block = _mapping(value, where=where, allowed=_EXPLANATION_KEYS)
    return PersonalDecisionExplanationRule(
        explanation_id=_text(block["explanation_id"], where=where, field="explanation_id"),
        explanation_version=_text(
            block["explanation_version"], where=where, field="explanation_version"
        ),
        policy_id=_text(block["policy_id"], where=where, field="policy_id"),
        policy_version=_text(block["policy_version"], where=where, field="policy_version"),
        action=_member(PersonalDecisionAction, block["action"], where=where, field="action"),
        semantic_rule_id=_text(block["semantic_rule_id"], where=where, field="semantic_rule_id"),
        semantic_rule_version=_text(
            block["semantic_rule_version"], where=where, field="semantic_rule_version"
        ),
        substance_key=_text(block["substance_key"], where=where, field="substance_key"),
        claim_key=_text(block["claim_key"], where=where, field="claim_key"),
        claim_version=_positive_int(block["claim_version"], where=where, field="claim_version"),
        source_key=_text(block["source_key"], where=where, field="source_key"),
        source_locator=_locator(block["source_locator"], where=where),
        reason_key=_text(block["reason_key"], where=where, field="reason_key"),
    )


def parse_release_manifest(value: Any) -> PersonalDecisionReleaseManifest:
    """Parse a manifest document into canonically ordered reviewed rules.

    Strict on every axis: the schema version must be the supported one, every
    object must carry exactly its declared fields, every type must be right,
    and each collection must be within bounds. The result is already sorted,
    so a manifest parsed from any input order compares and hashes identically.

    This runs on persisted JSONB too, not only on admin input. A row can be
    edited directly in the database, so nothing here may trust that the API
    wrote it.
    """
    block = _mapping(value, where="manifest", allowed=_MANIFEST_KEYS)

    schema_version = block["schema_version"]
    if schema_version != PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION or isinstance(
        schema_version, bool
    ):
        raise PersonalDecisionReleaseManifestError(
            f"manifest schema_version {schema_version!r} is not supported"
        )

    semantic_rules = tuple(
        _semantic_rule(entry, position=position)
        for position, entry in enumerate(
            _sequence(block["semantic_rules"], where="semantic_rules", limit=MAX_SEMANTIC_RULES)
        )
    )
    policy_rules = tuple(
        _policy_rule(entry, position=position)
        for position, entry in enumerate(
            _sequence(block["policy_rules"], where="policy_rules", limit=MAX_POLICY_RULES)
        )
    )
    explanation_rules = tuple(
        _explanation_rule(entry, position=position)
        for position, entry in enumerate(
            _sequence(
                block["explanation_rules"], where="explanation_rules", limit=MAX_EXPLANATION_RULES
            )
        )
    )

    manifest = PersonalDecisionReleaseManifest(
        schema_version=PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
        semantic_rules=tuple(sorted(semantic_rules, key=lambda rule: (rule.rule_id, rule.rule_version))),
        policy_rules=tuple(
            sorted(policy_rules, key=lambda rule: (rule.policy_id, rule.policy_version))
        ),
        explanation_rules=tuple(
            sorted(
                explanation_rules,
                key=lambda rule: (rule.explanation_id, rule.explanation_version),
            )
        ),
    )

    encoded = canonical_json(manifest)
    size = len(encoded.encode("utf-8"))
    if size > MAX_CANONICAL_MANIFEST_BYTES:
        raise PersonalDecisionReleaseManifestError(
            f"canonical manifest is {size} bytes; at most {MAX_CANONICAL_MANIFEST_BYTES} "
            "may be reviewed as one release"
        )
    return manifest


def canonical_manifest(manifest: PersonalDecisionReleaseManifest) -> dict[str, Any]:
    """The manifest as plain JSON data, in canonical order.

    Enum members are written as their values, and every collection is sorted
    by its reviewed identity -- including the identity set inside each policy,
    which is a ``frozenset`` and therefore has no order of its own to preserve.
    """
    return {
        "schema_version": manifest.schema_version,
        "semantic_rules": [
            {
                "rule_id": rule.rule_id,
                "rule_version": rule.rule_version,
                "category": rule.category.value,
                "substance_key": rule.substance_key,
                "claim_key": rule.claim_key,
                "claim_version": rule.claim_version,
                "signal": rule.signal.value,
            }
            for rule in sorted(
                manifest.semantic_rules, key=lambda rule: (rule.rule_id, rule.rule_version)
            )
        ],
        "policy_rules": [
            {
                "policy_id": rule.policy_id,
                "policy_version": rule.policy_version,
                "category": rule.category.value,
                "semantic_rule_identities": [
                    {"rule_id": rule_id, "rule_version": rule_version}
                    for rule_id, rule_version in sorted(rule.semantic_rule_identities)
                ],
                "signal_set": rule.signal_set.value,
                "has_identity_unresolved": rule.has_identity_unresolved,
                "has_identity_ambiguous": rule.has_identity_ambiguous,
                "has_personal_evidence_gap": rule.has_personal_evidence_gap,
                "action": rule.action.value,
            }
            for rule in sorted(
                manifest.policy_rules, key=lambda rule: (rule.policy_id, rule.policy_version)
            )
        ],
        "explanation_rules": [
            {
                "explanation_id": rule.explanation_id,
                "explanation_version": rule.explanation_version,
                "policy_id": rule.policy_id,
                "policy_version": rule.policy_version,
                "action": rule.action.value,
                "semantic_rule_id": rule.semantic_rule_id,
                "semantic_rule_version": rule.semantic_rule_version,
                "substance_key": rule.substance_key,
                "claim_key": rule.claim_key,
                "claim_version": rule.claim_version,
                "source_key": rule.source_key,
                "source_locator": rule.source_locator,
                "reason_key": rule.reason_key,
            }
            for rule in sorted(
                manifest.explanation_rules,
                key=lambda rule: (rule.explanation_id, rule.explanation_version),
            )
        ],
    }


def canonical_json(manifest: PersonalDecisionReleaseManifest) -> str:
    """Deterministic UTF-8 JSON: sorted keys, stable separators, no escaping."""
    return json.dumps(
        canonical_manifest(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def manifest_content_hash(manifest: PersonalDecisionReleaseManifest) -> str:
    """Lowercase 64-character SHA-256 hex over the canonical manifest."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def assert_registries_valid(manifest: PersonalDecisionReleaseManifest) -> None:
    """Run the three existing registry validators over the bundle.

    Delegated rather than re-implemented. Step 8C, 8E and 8F each own what an
    internally consistent registry means -- duplicate identities, two rules
    targeting one state, two explanations for one decision -- and a second
    copy of those rules here would eventually disagree with the real ones,
    which is the only kind of disagreement that matters.
    """
    build_rule_index(manifest.semantic_rules)
    build_policy_index(manifest.policy_rules)
    build_explanation_index(manifest.explanation_rules)
