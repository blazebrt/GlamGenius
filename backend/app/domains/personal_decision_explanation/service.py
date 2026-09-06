"""Step 8F: may this decision actually be shown, and on whose authority?

Step 8E answers whether a reviewed policy selected an action. That is not the
same question as whether the customer may be told. GlamGenius says why, and
it says why with a source: a decision that cannot name its reviewed reason and
a named openable source is an unsourced claim, and an unsourced claim is not
shown at all.

So Step 8F is deliberately stricter than the layer above it. A reviewed BUY
sitting inside a Step 8E result with no reviewed explanation comes back as
NOT_ENOUGH_EXPLANATION with ``action=None``. The action is not leaked at the
top-level contract on the reasoning that "we know it really is a BUY" --
that reasoning is exactly how an unsourced verdict reaches a screen.

Three things this module must never grow into:

- **writing prose.** It emits copy keys. The sentences live behind those keys
  where a reviewer reads them all at once against LEGAL_RULES, not scattered
  through Python. Nothing here paraphrases a claim, and a static test proves
  the claim's ``summary`` and ``scope`` are never even read.
- **choosing a source.** A reviewer chose one. Title, publisher and date are
  copied into the citation *after* selection and never participate in it,
  because a citation that could change when a publisher renames a document is
  not a citation.
- **deriving an action.** The only non-``None`` action it may emit is the one
  Step 8E already decided. Nothing infers BUY from a direction, from a reason
  key, from a source, or from how a policy id happens to read.

The evidence chain is reached, never re-queried:

```
policy → source_aggregation → source_semantics → source_personal_applicability
```

Every object on that path is the exact upstream instance. Step 8F imports only
Step 8E and treats everything below it structurally.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.domains.personal_decision_explanation.enums import (
    PersonalDecisionPresentationReason,
    PersonalDecisionPresentationStatus,
)
from app.domains.personal_decision_explanation.rules import (
    PERSONAL_DECISION_EXPLANATION_RULES,
    PersonalDecisionExplanationRule,
    build_explanation_index,
)
from app.domains.personal_decision_policy import (
    PersonalDecisionAction,
    PersonalDecisionPolicyReason,
    PersonalDecisionPolicyResult,
    PersonalDecisionPolicyStatus,
)

#: Copy keys for the structural non-decision states. The sentences behind them
#: belong to the frontend string catalogue, where a reviewer reads every
#: user-facing string in one place. No English is written in this module.
REASON_KEY_HANDOFF = "for_you.handoff.required"
REASON_KEY_PERSONAL_CONTEXT = "for_you.not_enough.personal_context"
REASON_KEY_FORMULA = "for_you.not_enough.formula"
REASON_KEY_SEMANTIC_MAPPING = "for_you.not_enough.semantic_mapping"
REASON_KEY_DECISION_POLICY = "for_you.not_enough.decision_policy"
REASON_KEY_EXPLANATION = "for_you.not_enough.explanation"

#: Which keyed label represents an action Step 8E already chose. This is a
#: rendering choice, not a decision: the action is an input here, and a test
#: proves Step 8F never creates or changes one.
#:
#: Keyed by the action's own value rather than by the enum member, so that no
#: production line anywhere names BUY, WAIT or SKIP. That lets the static
#: guard against inventing an action stay absolute, with no carve-out for
#: "but this one is only a label".
_VERDICT_KEYS: dict[str, str] = {
    "buy": "for_you.verdict.buy",
    "wait": "for_you.verdict.wait",
    "skip": "for_you.verdict.skip",
}

#: Step 8E's structural reasons, mapped to this layer's vocabulary and copy.
_BLOCKED_BY_POLICY: dict[
    PersonalDecisionPolicyReason, tuple[PersonalDecisionPresentationReason, str]
] = {
    PersonalDecisionPolicyReason.PERSONAL_CONTEXT_NOT_COMPLETE: (
        PersonalDecisionPresentationReason.PERSONAL_CONTEXT_NOT_COMPLETE,
        REASON_KEY_PERSONAL_CONTEXT,
    ),
    PersonalDecisionPolicyReason.FORMULA_NOT_PROJECTABLE: (
        PersonalDecisionPresentationReason.FORMULA_NOT_PROJECTABLE,
        REASON_KEY_FORMULA,
    ),
    PersonalDecisionPolicyReason.SEMANTIC_MAPPING_NOT_COMPLETE: (
        PersonalDecisionPresentationReason.SEMANTIC_MAPPING_NOT_COMPLETE,
        REASON_KEY_SEMANTIC_MAPPING,
    ),
}

_OPENABLE_PREFIXES = ("http://", "https://")


class PersonalDecisionPresentationInvariantError(ValueError):
    """The upstream object is not a shape a valid governed chain could produce.

    Reaching this means something was assembled or mutated outside that chain.
    Presenting from it would put an unreviewed claim in front of a customer,
    so it fails closed.
    """


@dataclass(frozen=True, slots=True)
class PersonalDecisionSourceCitation:
    """The exact reviewed source behind a presentable decision.

    Every value is copied from the Step 8B source object that the explanation
    rule selected -- never reconstructed from the rule itself, which holds
    only the identity used to find it.
    """

    source_key: str
    title: str
    publisher: str
    canonical_url: str
    locator: str | None
    publication_date: date | None
    version_or_revision: str | None
    jurisdiction: str | None


@dataclass(frozen=True, slots=True)
class PersonalDecisionPresentation:
    """Whether a decision may be shown, with the reason and source if so.

    The invariants below make the dangerous states unconstructible rather than
    merely unreached: no action without its explanation and citation, and no
    blocked state carrying an action.
    """

    source_policy: PersonalDecisionPolicyResult
    status: PersonalDecisionPresentationStatus
    reason: PersonalDecisionPresentationReason
    action: PersonalDecisionAction | None
    verdict_key: str | None
    reason_key: str
    explanation_id: str | None
    explanation_version: str | None
    citation: PersonalDecisionSourceCitation | None
    handoff_reason: str | None
    handoff_message: str | None

    def __post_init__(self) -> None:
        presentable = self.status is PersonalDecisionPresentationStatus.DECISION_PRESENTABLE
        decided = (
            self.action,
            self.verdict_key,
            self.explanation_id,
            self.explanation_version,
            self.citation,
        )

        if presentable:
            if any(part is None for part in decided):
                raise ValueError(
                    "DECISION_PRESENTABLE requires an action, a verdict key, explanation "
                    "provenance and a citation"
                )
            if self.action is not self.source_policy.action:
                raise ValueError("the presented action must be the action Step 8E decided")
        elif any(part is not None for part in decided):
            raise ValueError(f"{self.status} must carry no action, explanation or citation")

        if not self.reason_key or not self.reason_key.strip():
            raise ValueError("every presentation carries a reason key")

        handoff = self.status is PersonalDecisionPresentationStatus.HANDOFF_REQUIRED
        handoff_fields = (self.handoff_reason, self.handoff_message)
        if handoff:
            if any(part is None for part in handoff_fields):
                raise ValueError("HANDOFF_REQUIRED requires the upstream reason and message")
        elif any(part is not None for part in handoff_fields):
            raise ValueError(f"{self.status} must carry no handoff fields")


def _blocked(
    policy: PersonalDecisionPolicyResult,
    status: PersonalDecisionPresentationStatus,
    reason: PersonalDecisionPresentationReason,
    reason_key: str,
) -> PersonalDecisionPresentation:
    """A presentation that shows no decision. The only non-decision path."""
    return PersonalDecisionPresentation(
        source_policy=policy,
        status=status,
        reason=reason,
        action=None,
        verdict_key=None,
        reason_key=reason_key,
        explanation_id=None,
        explanation_version=None,
        citation=None,
        handoff_reason=None,
        handoff_message=None,
    )


def _no_explanation(
    policy: PersonalDecisionPolicyResult,
    reason: PersonalDecisionPresentationReason,
) -> PersonalDecisionPresentation:
    """Step 8E has an action; it cannot be shown. It is not leaked here."""
    return _blocked(
        policy,
        PersonalDecisionPresentationStatus.NOT_ENOUGH_EXPLANATION,
        reason,
        REASON_KEY_EXPLANATION,
    )


def _handoff(policy: PersonalDecisionPolicyResult) -> PersonalDecisionPresentation:
    """Pass the canonical handoff text through untouched.

    The message came from the hard-handoff authority and was written for this
    boundary. Rewriting or embellishing it here would put a second, unreviewed
    voice on the most sensitive sentence the product says.
    """
    handoff = getattr(policy.source_aggregation.source_semantics, "handoff", None)
    return PersonalDecisionPresentation(
        source_policy=policy,
        status=PersonalDecisionPresentationStatus.HANDOFF_REQUIRED,
        reason=PersonalDecisionPresentationReason.PROFESSIONAL_HANDOFF_REQUIRED,
        action=None,
        verdict_key=None,
        reason_key=REASON_KEY_HANDOFF,
        explanation_id=None,
        explanation_version=None,
        citation=None,
        handoff_reason=str(getattr(handoff, "reason", "")),
        handoff_message=str(getattr(handoff, "message", "")),
    )


def _matching_aggregated_rule(
    policy: PersonalDecisionPolicyResult, explanation: PersonalDecisionExplanationRule
) -> object | None:
    """The one distinct governed rule this explanation is anchored to.

    All five parts must match. Anchoring on the semantic rule id alone, or on
    the claim key without its version, would let a reason written against one
    reviewed finding travel to a different or revised one.
    """
    matches = [
        rule
        for rule in policy.source_aggregation.rules
        if rule.rule_id == explanation.semantic_rule_id
        and rule.rule_version == explanation.semantic_rule_version
        and rule.substance_key == explanation.substance_key
        and rule.claim_key == explanation.claim_key
        and rule.claim_version == explanation.claim_version
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise PersonalDecisionPresentationInvariantError(
            "one exact evidence anchor resolved to several distinct governed rules"
        )
    return matches[0]


def _matching_claim(
    applicability: object, explanation: PersonalDecisionExplanationRule
) -> object | None:
    """The exact reviewed Step 8B claim the anchor names.

    One ingredient repeated across printed positions yields the same claim
    identity more than once. That is one evidence chain, not several, so
    repeats of the identical object collapse. Genuinely different claim
    objects wearing one identity are a corrupted chain and fail closed.
    """
    found: list[object] = []
    for ingredient in getattr(applicability, "ingredients", ()):
        if ingredient.substance_key != explanation.substance_key:
            continue
        for claim in ingredient.claims:
            if (
                claim.claim_key == explanation.claim_key
                and claim.claim_version == explanation.claim_version
                and not any(existing is claim for existing in found)
            ):
                found.append(claim)

    if not found:
        return None
    if len(found) != 1:
        raise PersonalDecisionPresentationInvariantError(
            "one exact claim identity resolved to several distinct reviewed claims"
        )
    return found[0]


def _selected_source(claim: object, explanation: PersonalDecisionExplanationRule) -> object | None:
    """The exact source a reviewer chose, matched by identity alone.

    No normalisation, no case folding, no punctuation stripping, and no
    falling back to another source when the chosen one is absent. Substituting
    a different source would attach a reviewed sentence to evidence nobody
    reviewed it against.
    """
    found = [
        source
        for source in getattr(claim, "sources", ())
        if source.source_key == explanation.source_key
        and source.locator == explanation.source_locator
    ]
    if not found:
        return None
    if len(found) != 1:
        raise PersonalDecisionPresentationInvariantError(
            "one exact source anchor resolved to several distinct reviewed sources"
        )
    return found[0]


def _citation(source: object) -> PersonalDecisionSourceCitation:
    """Copy the selected source's own metadata into the citation.

    This runs only after selection. The values below are what a customer would
    be shown beside a negative statement, so an unopenable URL is a failure,
    not a cosmetic problem.
    """
    canonical_url = source.canonical_url
    if not isinstance(canonical_url, str) or not canonical_url.strip():
        raise PersonalDecisionPresentationInvariantError(
            "the selected reviewed source has no canonical URL"
        )
    if not canonical_url.startswith(_OPENABLE_PREFIXES):
        raise PersonalDecisionPresentationInvariantError(
            "the selected reviewed source has no openable canonical URL"
        )
    return PersonalDecisionSourceCitation(
        source_key=source.source_key,
        title=source.title,
        publisher=source.publisher,
        canonical_url=canonical_url,
        locator=source.locator,
        publication_date=source.publication_date,
        version_or_revision=source.version_or_revision,
        jurisdiction=source.jurisdiction,
    )


def present_personal_decision(
    policy: PersonalDecisionPolicyResult,
    *,
    rules: Iterable[PersonalDecisionExplanationRule] = PERSONAL_DECISION_EXPLANATION_RULES,
) -> PersonalDecisionPresentation:
    """Decide whether one exact Step 8E result may be shown, and with what.

    Pure and synchronous: no session, no account, no snapshot, no category
    argument, no safety input, no query of any kind. The Step 8E result is the
    complete input and is returned untouched on ``source_policy``.

    Order matters. Handoff and every Step 8E non-decision are answered before
    the explanation registry is built, so a broken registry can neither
    suppress a handoff nor turn a structural non-decision into an exception.
    """
    status = policy.status

    if status is PersonalDecisionPolicyStatus.HANDOFF_REQUIRED:
        return _handoff(policy)

    if status is PersonalDecisionPolicyStatus.NOT_ENOUGH_INFORMATION:
        blocked = _BLOCKED_BY_POLICY.get(policy.reason)
        if blocked is None:
            raise PersonalDecisionPresentationInvariantError(
                f"upstream reason {policy.reason!r} is not a recognised structural block"
            )
        reason, reason_key = blocked
        return _blocked(
            policy,
            PersonalDecisionPresentationStatus.NOT_ENOUGH_INFORMATION,
            reason,
            reason_key,
        )

    if status is PersonalDecisionPolicyStatus.NOT_ENOUGH_DECISION_POLICY:
        return _blocked(
            policy,
            PersonalDecisionPresentationStatus.NOT_ENOUGH_DECISION_POLICY,
            PersonalDecisionPresentationReason.NO_EXACT_DECISION_POLICY,
            REASON_KEY_DECISION_POLICY,
        )

    if status is not PersonalDecisionPolicyStatus.DECISION_AVAILABLE:
        raise PersonalDecisionPresentationInvariantError(
            f"upstream policy status {status!r} is unrecognised"
        )

    action = policy.action
    if action is None or policy.policy_id is None or policy.policy_version is None:
        raise PersonalDecisionPresentationInvariantError(
            "a decided policy result must carry an action and its policy provenance"
        )

    index = build_explanation_index(rules)
    explanation = index.get((policy.policy_id, policy.policy_version, action))
    if explanation is None:
        return _no_explanation(
            policy, PersonalDecisionPresentationReason.NO_EXACT_EXPLANATION_RULE
        )

    if _matching_aggregated_rule(policy, explanation) is None:
        return _no_explanation(
            policy, PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )

    applicability = getattr(
        policy.source_aggregation.source_semantics, "source_personal_applicability", None
    )
    if applicability is None:
        return _no_explanation(
            policy, PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )

    claim = _matching_claim(applicability, explanation)
    if claim is None:
        return _no_explanation(
            policy, PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )

    source = _selected_source(claim, explanation)
    if source is None:
        return _no_explanation(
            policy, PersonalDecisionPresentationReason.EXPLANATION_SOURCE_NOT_AVAILABLE
        )

    return PersonalDecisionPresentation(
        source_policy=policy,
        status=PersonalDecisionPresentationStatus.DECISION_PRESENTABLE,
        reason=PersonalDecisionPresentationReason.REVIEWED_EXPLANATION_AVAILABLE,
        action=action,
        verdict_key=_VERDICT_KEYS[str(action)],
        reason_key=explanation.reason_key,
        explanation_id=explanation.explanation_id,
        explanation_version=explanation.explanation_version,
        citation=_citation(source),
        handoff_reason=None,
        handoff_message=None,
    )
