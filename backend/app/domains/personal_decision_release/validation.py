"""Cross-validation: does this reviewed bundle still describe reality?

Step 8H asks two different questions, and keeping them apart is the point of
this module.

**Is the bundle internally consistent?** Does every policy reference semantics
that are in this same release, does its declared direction set match those
exact semantics, is every semantic rule actually used, does every policy carry
exactly one explanation, and does every explanation anchor land on a real
policy, a real semantic rule and the exact claim that semantic rule names.
That is structural, needs no database, and is checked first.

**Is the evidence underneath it still published and eligible?** Does the exact
claim version each semantic rule names still exist, still published, still
non-AI, still the right tier, still about the right substance in the right
category; and does the exact source path each explanation cites still pass the
same public-knowledge test Step 8B applies at runtime.

What this module never does is judge the science. It never reads ``summary``,
``scope`` or ``strength_rationale``, and it never infers from them whether
SUPPORTING or CAUTIONARY is the sensible direction for a claim. A static test
enforces that. The direction is an explicit reviewed Step 8C rule, and the
human judgement behind it is attested by the founder / Claude / Codex /
adversarial review recorded on the release. Step 8H validates provenance and
structure; it does not second-guess a reviewer, and it must never be able to
substitute its own reading of a paragraph for one.

Every failure carries a deterministic code so an admin sees which link broke.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.enums import ClaimType, EvidenceTier, ReviewStatus
from app.domains.evidence.models import EvidenceClaim, EvidenceClaimSource, EvidenceSource
from app.domains.evidence.service import (
    claim_is_public_knowledge_path,
    source_path_is_public_knowledge,
)
from app.domains.personal_applicability import (
    evidence_domain_for_category,
    parse_personal_applicability_payload,
)
from app.domains.personal_applicability.service import (
    PERSONAL_APPLICABILITY_SOURCE_TYPES,
    PERSONAL_APPLICABILITY_STRENGTHS,
)
from app.domains.personal_decision_aggregation import PersonalSignalSet
from app.domains.personal_decision_policy import PersonalDecisionPolicyRule
from app.domains.personal_decision_release.enums import PersonalDecisionReleaseValidationCode
from app.domains.personal_decision_release.manifest import (
    PersonalDecisionReleaseManifest,
    assert_registries_valid,
)
from app.domains.personal_decision_semantics import (
    PersonalDecisionSemanticRule,
    PersonalDecisionSignal,
)
from app.shared.errors.exceptions import ValidationFailedError

#: The named human attestations a release must carry before approval. Recorded
#: by a person, never inferred: nothing in this repository can observe that a
#: founder read a rule or that two independent reviews agreed.
RELEASE_VERIFICATION_CHECKPOINTS: tuple[str, ...] = (
    "founder_review_completed",
    "claude_review_completed",
    "codex_review_completed",
    "independent_reviews_agree",
    "adversarial_review_passed",
)

#: Recorded alongside the checkpoints and inverted: doubt left open blocks
#: approval however many boxes are ticked.
RELEASE_VERIFICATION_DOUBT = "unresolved_doubt"

RELEASE_VERIFICATION_FIELDS: frozenset[str] = frozenset(
    (*RELEASE_VERIFICATION_CHECKPOINTS, RELEASE_VERIFICATION_DOUBT)
)

#: Which direction set an exact collection of reviewed directions *is*. This
#: restates Step 8D's own mapping deliberately -- 8D derives it from an
#: aggregation of a live scan, this derives it from a manifest, and the two
#: inputs have nothing in common. A test pins the two maps together so they
#: cannot drift apart.
#:
#: This is structural equality checking, not decision inference. It answers
#: "does the reviewed policy's declared direction set match the semantics it
#: names", and never "what should follow from that direction set".
_SIGNAL_SETS: dict[frozenset[PersonalDecisionSignal], PersonalSignalSet] = {
    frozenset(): PersonalSignalSet.NONE,
    frozenset({PersonalDecisionSignal.SUPPORTING}): PersonalSignalSet.SUPPORTING_ONLY,
    frozenset({PersonalDecisionSignal.CAUTIONARY}): PersonalSignalSet.CAUTIONARY_ONLY,
    frozenset({
        PersonalDecisionSignal.SUPPORTING,
        PersonalDecisionSignal.CAUTIONARY,
    }): PersonalSignalSet.MIXED,
}

#: Keys that would mean a release had absorbed something about one person.
#: A release is global governed knowledge; personal matching happens at
#: runtime in Step 8B against that person's own trusted facts.
_PERSONAL_DATA_KEYS: frozenset[str] = frozenset({
    "account_id",
    "profile_id",
    "profile_version",
    "device_id",
    "scan_id",
    "scan_event_id",
    "label_snapshot_id",
    "family_profile_id",
    "medication",
    "medications",
    "condition",
    "conditions",
    "body_facts",
    "user_text",
})


class PersonalDecisionReleaseValidationError(ValidationFailedError):
    """A reviewed bundle was refused, with the exact reason.

    A subclass of the repository's own validation error rather than a new
    exception family, so it serialises into the same ``detail`` shape every
    client already understands, and carries a deterministic ``reason`` an
    admin screen can branch on without parsing prose.
    """

    def __init__(
        self,
        code: PersonalDecisionReleaseValidationCode,
        message: str,
        *,
        field: str = "manifest",
    ) -> None:
        super().__init__(message, field=field)
        self.reason = code
        self.extra["reason"] = code.value


class PersonalDecisionReleaseInvariantError(ValueError):
    """The stored release contradicts itself. Nothing is loaded from it.

    Separate from validation failure on purpose. A validation failure means a
    human wrote a bundle that does not hold together and must fix it; this
    means the persisted row is not what the reviewed path wrote, which no
    amount of re-reviewing addresses and which must never be repaired
    silently.
    """


def _fail(code: PersonalDecisionReleaseValidationCode, message: str) -> None:
    raise PersonalDecisionReleaseValidationError(code, message)


# ---------------------------------------------------------------------------
# Review verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReleaseVerification:
    """The named attestations, exactly as a person recorded them."""

    founder_review_completed: bool
    claude_review_completed: bool
    codex_review_completed: bool
    independent_reviews_agree: bool
    adversarial_review_passed: bool
    unresolved_doubt: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "founder_review_completed": self.founder_review_completed,
            "claude_review_completed": self.claude_review_completed,
            "codex_review_completed": self.codex_review_completed,
            "independent_reviews_agree": self.independent_reviews_agree,
            "adversarial_review_passed": self.adversarial_review_passed,
            "unresolved_doubt": self.unresolved_doubt,
        }


def parse_release_verification(value: Any) -> ReleaseVerification | None:
    """Read a stored verification block, or ``None`` if it is not complete.

    Missing verification is incomplete verification. Absence is never consent,
    so a partial or malformed block reads as no attestation at all rather than
    as the parts that happen to be present.
    """
    if not isinstance(value, Mapping) or set(value) != RELEASE_VERIFICATION_FIELDS:
        return None
    if any(not isinstance(value[field], bool) for field in RELEASE_VERIFICATION_FIELDS):
        return None
    return ReleaseVerification(**{field: value[field] for field in RELEASE_VERIFICATION_FIELDS})


def assert_verification_permits_approval(value: Any) -> ReleaseVerification:
    """Every checkpoint recorded true, and no doubt left open."""
    verification = parse_release_verification(value)
    if verification is None or not all(
        getattr(verification, checkpoint) for checkpoint in RELEASE_VERIFICATION_CHECKPOINTS
    ):
        _fail(
            PersonalDecisionReleaseValidationCode.RELEASE_VERIFICATION_INCOMPLETE,
            "Every named review attestation must be recorded before a release is approved.",
        )
    assert verification is not None  # narrowed by the branch above
    if verification.unresolved_doubt:
        _fail(
            PersonalDecisionReleaseValidationCode.RELEASE_UNRESOLVED_DOUBT,
            "A release carrying unresolved doubt must not be approved.",
        )
    return verification


# ---------------------------------------------------------------------------
# Structural cross-validation
# ---------------------------------------------------------------------------


def assert_manifest_carries_no_personal_data(document: Any, *, where: str = "manifest") -> None:
    """No key anywhere in the manifest may name a person or a scan."""
    if isinstance(document, Mapping):
        for key, value in document.items():
            if isinstance(key, str) and key in _PERSONAL_DATA_KEYS:
                _fail(
                    PersonalDecisionReleaseValidationCode.RELEASE_PERSONAL_DATA_PRESENT,
                    f"{where} carries {key!r}; a release holds global knowledge only.",
                )
            assert_manifest_carries_no_personal_data(value, where=where)
    elif isinstance(document, (list, tuple)):
        for entry in document:
            assert_manifest_carries_no_personal_data(entry, where=where)


def _semantic_index(
    manifest: PersonalDecisionReleaseManifest,
) -> dict[tuple[str, str], PersonalDecisionSemanticRule]:
    return {(rule.rule_id, rule.rule_version): rule for rule in manifest.semantic_rules}


def _policy_index(
    manifest: PersonalDecisionReleaseManifest,
) -> dict[tuple[str, str], PersonalDecisionPolicyRule]:
    return {(rule.policy_id, rule.policy_version): rule for rule in manifest.policy_rules}


def _validate_policies(manifest: PersonalDecisionReleaseManifest) -> set[tuple[str, str]]:
    """Policy -> semantics. Returns the semantic identities actually referenced."""
    semantics = _semantic_index(manifest)
    referenced: set[tuple[str, str]] = set()

    for policy in manifest.policy_rules:
        directions: set[PersonalDecisionSignal] = set()
        for identity in sorted(policy.semantic_rule_identities):
            rule = semantics.get(identity)
            if rule is None:
                _fail(
                    PersonalDecisionReleaseValidationCode.POLICY_SEMANTIC_NOT_IN_RELEASE,
                    f"Policy {policy.policy_id}@{policy.policy_version} references semantic rule "
                    f"{identity[0]}@{identity[1]}, which is not in this release.",
                )
                return referenced  # unreachable; keeps the type checker honest
            if rule.category.value != policy.category.value:
                _fail(
                    PersonalDecisionReleaseValidationCode.POLICY_CATEGORY_MISMATCH,
                    f"Policy {policy.policy_id}@{policy.policy_version} is {policy.category.value} "
                    f"but references a {rule.category.value} semantic rule.",
                )
            referenced.add(identity)
            directions.add(rule.signal)

        derived = _SIGNAL_SETS.get(frozenset(directions))
        if derived is not policy.signal_set:
            _fail(
                PersonalDecisionReleaseValidationCode.POLICY_SIGNAL_SET_MISMATCH,
                f"Policy {policy.policy_id}@{policy.policy_version} declares "
                f"{policy.signal_set.value} but the semantic rules it names are "
                f"{derived.value if derived else 'not a recognised direction set'}.",
            )

    return referenced


def _validate_explanations(manifest: PersonalDecisionReleaseManifest) -> None:
    """Explanation -> policy, and explanation -> the semantic rule it anchors to."""
    semantics = _semantic_index(manifest)
    policies = _policy_index(manifest)
    explained: set[tuple[str, str]] = set()

    for explanation in manifest.explanation_rules:
        policy_identity = (explanation.policy_id, explanation.policy_version)
        policy = policies.get(policy_identity)
        if policy is None:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_POLICY_NOT_IN_RELEASE,
                f"Explanation {explanation.explanation_id}@{explanation.explanation_version} "
                f"explains policy {policy_identity[0]}@{policy_identity[1]}, which is not in "
                "this release.",
            )
            continue
        if explanation.action is not policy.action:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_ACTION_MISMATCH,
                f"Explanation {explanation.explanation_id}@{explanation.explanation_version} "
                f"carries {explanation.action.value} but its policy decided "
                f"{policy.action.value}.",
            )

        anchor = (explanation.semantic_rule_id, explanation.semantic_rule_version)
        if anchor not in policy.semantic_rule_identities:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_SEMANTIC_NOT_IN_POLICY,
                f"Explanation {explanation.explanation_id}@{explanation.explanation_version} "
                f"anchors to semantic rule {anchor[0]}@{anchor[1]}, which its policy does not "
                "name.",
            )
        semantic = semantics.get(anchor)
        if semantic is None:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_SEMANTIC_NOT_IN_POLICY,
                f"Explanation {explanation.explanation_id}@{explanation.explanation_version} "
                f"anchors to semantic rule {anchor[0]}@{anchor[1]}, which is not in this release.",
            )
            continue
        if (
            explanation.substance_key != semantic.substance_key
            or explanation.claim_key != semantic.claim_key
            or explanation.claim_version != semantic.claim_version
        ):
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_EVIDENCE_ANCHOR_MISMATCH,
                f"Explanation {explanation.explanation_id}@{explanation.explanation_version} "
                "cites a different evidence identity from the semantic rule it anchors to.",
            )
        explained.add(policy_identity)

    for policy in manifest.policy_rules:
        if (policy.policy_id, policy.policy_version) not in explained:
            _fail(
                PersonalDecisionReleaseValidationCode.POLICY_EXPLANATION_MISSING,
                f"Policy {policy.policy_id}@{policy.policy_version} has no reviewed explanation; "
                "a decision that cannot be shown with a reviewed sourced reason is not "
                "production knowledge.",
            )


def validate_release_structure(
    manifest: PersonalDecisionReleaseManifest,
    *,
    require_complete: bool,
) -> None:
    """Everything checkable without touching the database.

    ``require_complete`` is what separates a draft from a release ready to be
    approved. A draft may legitimately be half-written -- a semantic rule
    typed before its policy exists -- and refusing to save that would make
    incremental authoring impossible. Approval and activation demand the whole
    chain.
    """
    assert_registries_valid(manifest)
    if not require_complete:
        return

    if manifest.is_empty or not (
        manifest.semantic_rules and manifest.policy_rules and manifest.explanation_rules
    ):
        _fail(
            PersonalDecisionReleaseValidationCode.RELEASE_EMPTY,
            "A release must carry at least one semantic rule, one policy and one explanation.",
        )

    referenced = _validate_policies(manifest)

    for rule in manifest.semantic_rules:
        if (rule.rule_id, rule.rule_version) not in referenced:
            _fail(
                PersonalDecisionReleaseValidationCode.UNREFERENCED_SEMANTIC_RULE,
                f"Semantic rule {rule.rule_id}@{rule.rule_version} is not referenced by any "
                "policy in this release; it would change what Step 8C matches with no reviewed "
                "decision acknowledging it.",
            )

    _validate_explanations(manifest)


# ---------------------------------------------------------------------------
# Evidence cross-validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReleaseEvidenceReport:
    """What the evidence pass actually looked at. Counts are admin metadata.

    They exist so a reviewer can see the pass was not vacuous. Nothing here
    ever reaches a decision: no count, ratio or total is an input to what a
    person is told.
    """

    semantic_evidence_checked: int
    policies_checked: int
    explanations_checked: int


async def _load_claims(
    session: AsyncSession,
    manifest: PersonalDecisionReleaseManifest,
) -> dict[tuple[str, int], EvidenceClaim]:
    """One query for every claim the manifest names, however many rules there are."""
    pairs = sorted({(rule.claim_key, rule.claim_version) for rule in manifest.semantic_rules})
    if not pairs:
        return {}
    rows = (
        await session.execute(
            select(EvidenceClaim).where(
                tuple_(EvidenceClaim.claim_key, EvidenceClaim.claim_version).in_(pairs)
            )
        )
    ).scalars().all()
    claims: dict[tuple[str, int], EvidenceClaim] = {}
    for claim in rows:
        identity = (claim.claim_key, claim.claim_version)
        if identity in claims:
            # The database holds a unique constraint on this pair, so reaching
            # here means the constraint is gone. Choosing one row would be
            # choosing which evidence a customer is shown.
            raise PersonalDecisionReleaseInvariantError(
                f"claim {identity[0]} v{identity[1]} resolved to several rows"
            )
        claims[identity] = claim
    return claims


async def _load_source_paths(
    session: AsyncSession,
    claim_ids: set[uuid.UUID],
) -> dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]]:
    """One query for every source path behind those claims."""
    paths: dict[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]] = defaultdict(list)
    if not claim_ids:
        return paths
    rows = await session.execute(
        select(EvidenceClaimSource, EvidenceSource)
        .join(EvidenceSource, EvidenceSource.id == EvidenceClaimSource.source_id)
        .where(EvidenceClaimSource.claim_id.in_(sorted(claim_ids)))
    )
    for link, source in rows.all():
        paths[link.claim_id].append((link, source))
    return paths


def _assert_claim_supports_semantic(
    rule: PersonalDecisionSemanticRule,
    claim: EvidenceClaim | None,
) -> None:
    where = f"Semantic rule {rule.rule_id}@{rule.rule_version}"
    if claim is None:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED,
            f"{where} names claim {rule.claim_key} v{rule.claim_version}, which does not exist.",
        )
        return

    if claim.review_status != ReviewStatus.PUBLISHED.value:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_PUBLISHED,
            f"{where} names claim {rule.claim_key} v{rule.claim_version}, which is "
            f"{claim.review_status} rather than published.",
        )
    if not claim_is_public_knowledge_path(claim):
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
            f"{where} names a claim that does not clear the public-knowledge boundary.",
        )
    if claim.ai_generated is not False:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
            f"{where} names an AI-generated claim.",
        )
    if claim.evidence_tier != EvidenceTier.CLINICALLY_STUDIED.value:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
            f"{where} names a {claim.evidence_tier} claim; Step 8B accepts only "
            f"{EvidenceTier.CLINICALLY_STUDIED.value}.",
        )
    # Membership in Step 8B's own controlled set, reusing that set rather than
    # restating it: two copies would eventually disagree, and the direction of
    # disagreement that matters is Step 8H approving a release Step 8B will
    # never project. This is the only reason strength is read anywhere in this
    # domain. It answers "would Step 8B accept this exact row" and nothing
    # else -- it is never compared, ordered, counted, or turned into a signal,
    # an action, a weight or a confidence.
    if claim.evidence_strength not in PERSONAL_APPLICABILITY_STRENGTHS:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
            f"{where} names a claim graded {claim.evidence_strength!r}, which Step 8B does "
            "not accept.",
        )
    if claim.claim_type != ClaimType.SUBSTANCE_PERSONAL_APPLICABILITY.value:
        _fail(
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH,
            f"{where} names a {claim.claim_type} claim.",
        )
    if claim.subject_type != "substance":
        _fail(
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH,
            f"{where} names a claim about a {claim.subject_type}, not a substance.",
        )
    if claim.subject_key != rule.substance_key:
        _fail(
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH,
            f"{where} names substance {rule.substance_key} but the claim is about "
            f"{claim.subject_key}.",
        )
    if claim.domain != evidence_domain_for_category(rule.category).value:
        _fail(
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH,
            f"{where} is {rule.category.value} but the claim is in the {claim.domain} domain.",
        )

    payload = parse_personal_applicability_payload(claim.structured_value)
    if payload is None:
        _fail(
            PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
            f"{where} names a claim whose personal-applicability payload is not parseable.",
        )
        return
    if payload.category is not rule.category:
        _fail(
            PersonalDecisionReleaseValidationCode.SEMANTIC_EVIDENCE_MISMATCH,
            f"{where} is {rule.category.value} but the claim's payload is "
            f"{payload.category.value}.",
        )


def _assert_semantic_claims_are_projectable(
    manifest: PersonalDecisionReleaseManifest,
    claims: Mapping[tuple[str, int], EvidenceClaim],
    paths: Mapping[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]],
) -> None:
    """Every semantic claim must have at least one source Step 8B would accept.

    A claim with no eligible source path is invisible to Step 8B: it returns
    no claims for it at all, so a semantic rule naming it can never match and
    the release is quietly carrying a rule that does nothing. Worse, a policy
    keyed on that rule's identity can never be reached, so the reviewed action
    behind it is unreachable too -- and none of that is visible from the
    manifest.

    This is a different gate from the explanation source check below, and they
    must not be collapsed. This one asks *whether Step 8B can project this
    claim at all*; that one asks *which exact source the reviewer chose to
    show*. A release whose displayed citation is still perfectly valid can
    still be built on a semantic rule that no longer projects, and approving it
    on the strength of the citation would be approving something that cannot
    work.

    Reuses the already batch-loaded path map, so this costs no query however
    many semantic rules the release holds.
    """
    for rule in manifest.semantic_rules:
        claim = claims.get((rule.claim_key, rule.claim_version))
        if claim is None:
            continue  # already reported by the claim readiness gate
        if not any(
            source_path_is_public_knowledge(
                link,
                source,
                allowed_source_types=PERSONAL_APPLICABILITY_SOURCE_TYPES,
            )
            for link, source in paths.get(claim.id, ())
        ):
            _fail(
                PersonalDecisionReleaseValidationCode.EVIDENCE_CLAIM_NOT_ELIGIBLE,
                f"Semantic rule {rule.rule_id}@{rule.rule_version} names claim "
                f"{rule.claim_key} v{rule.claim_version}, which has no source path Step 8B "
                "would accept; Step 8B cannot project it, so the rule can never match.",
            )


def _assert_explanation_source(
    manifest: PersonalDecisionReleaseManifest,
    claims: Mapping[tuple[str, int], EvidenceClaim],
    paths: Mapping[uuid.UUID, list[tuple[EvidenceClaimSource, EvidenceSource]]],
) -> None:
    for explanation in manifest.explanation_rules:
        where = f"Explanation {explanation.explanation_id}@{explanation.explanation_version}"
        claim = claims.get((explanation.claim_key, explanation.claim_version))
        if claim is None:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_EVIDENCE_ANCHOR_MISMATCH,
                f"{where} cites claim {explanation.claim_key} v{explanation.claim_version}, "
                "which no semantic rule in this release names.",
            )
            continue

        # Exact identity, never a normalised or nearest match. A reviewer chose
        # one source path; substituting another would attach a reviewed
        # sentence to evidence nobody reviewed it against.
        matching = [
            (link, source)
            for link, source in paths.get(claim.id, ())
            if source.source_key == explanation.source_key
            and link.locator == explanation.source_locator
        ]
        if not matching:
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE,
                f"{where} cites source {explanation.source_key} at "
                f"{explanation.source_locator!r}, which is not a path on that claim.",
            )
            continue
        if len(matching) != 1:
            raise PersonalDecisionReleaseInvariantError(
                f"{where} resolved to several distinct reviewed source paths"
            )
        link, source = matching[0]
        if not source_path_is_public_knowledge(
            link,
            source,
            allowed_source_types=PERSONAL_APPLICABILITY_SOURCE_TYPES,
        ):
            _fail(
                PersonalDecisionReleaseValidationCode.EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE,
                f"{where} cites a source path that no longer clears the Step 8B "
                "public-knowledge boundary.",
            )


async def validate_release_evidence(
    session: AsyncSession,
    manifest: PersonalDecisionReleaseManifest,
) -> ReleaseEvidenceReport:
    """Check the bundle against live evidence in a bounded number of queries.

    Two statements, whatever the release holds: one for every claim the
    manifest names, one for every source path behind those claims. A per-rule
    query would make a 500-rule release 1000 round trips, and a validation
    step nobody is willing to run is a validation step that does not exist.
    """
    claims = await _load_claims(session, manifest)
    for rule in manifest.semantic_rules:
        _assert_claim_supports_semantic(rule, claims.get((rule.claim_key, rule.claim_version)))

    paths = await _load_source_paths(session, {claim.id for claim in claims.values()})
    _assert_semantic_claims_are_projectable(manifest, claims, paths)
    _assert_explanation_source(manifest, claims, paths)

    return ReleaseEvidenceReport(
        semantic_evidence_checked=len(manifest.semantic_rules),
        policies_checked=len(manifest.policy_rules),
        explanations_checked=len(manifest.explanation_rules),
    )


async def validate_release_manifest(
    session: AsyncSession,
    manifest: PersonalDecisionReleaseManifest,
    *,
    require_complete: bool = True,
) -> ReleaseEvidenceReport:
    """Structure first, then evidence. Structure is cheaper and more specific."""
    validate_release_structure(manifest, require_complete=require_complete)
    return await validate_release_evidence(session, manifest)
