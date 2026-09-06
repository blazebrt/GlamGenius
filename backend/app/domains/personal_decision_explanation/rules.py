"""The reviewed explanation registry, and the validation that keeps it honest.

An explanation rule says one thing: *for this exact reviewed policy version
and the exact action it selected, the reviewed one-line reason is this copy
key, and it is anchored to this exact claim version and this exact source.*

Two things about that are deliberate and easy to erode.

**The rule stores a reason key, not a sentence.** Product copy lives behind
keys so a reviewer can read every user-facing string in one place, checked
against LEGAL_RULES, without reading Python. A service that composed prose
from evidence would put the most legally sensitive text in the least
reviewable place.

**The rule names one source, and names it by identity.** Not the strongest
source, not the newest, not the first in the list -- one that a reviewer
chose. Source metadata (title, publisher, date) is copied into the citation
only *after* selection, and never participates in it. Letting metadata pick
the source would mean the citation shown to a customer could change because a
publisher renamed a document.

The production registry is empty in V1, so no real decision is presentable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domains.personal_decision_policy import PersonalDecisionAction

#: (policy_id, policy_version, action) -- the reviewed decision an explanation
#: attaches to. An explanation never attaches to an action alone: "this is a
#: SKIP" is not a reason, and a reason written for one policy cannot be reused
#: on another that happens to reach the same action.
ExplanationTarget = tuple[str, str, PersonalDecisionAction]


class PersonalDecisionExplanationRegistryError(ValueError):
    """The explanation registry is not fit to be used. Nothing is presented."""


@dataclass(frozen=True, slots=True)
class PersonalDecisionExplanationRule:
    """One reviewed reason, anchored to one exact evidence chain.

    The anchor is spelled out in full -- semantic rule, substance, claim
    version, source -- so the reason shown beside a decision can be traced
    back to the exact reviewed evidence a person approved it against. If any
    link of that chain is missing from the live governed state, the
    explanation does not apply and the decision is not presentable.
    """

    explanation_id: str
    explanation_version: str

    policy_id: str
    policy_version: str
    action: PersonalDecisionAction

    semantic_rule_id: str
    semantic_rule_version: str

    substance_key: str
    claim_key: str
    claim_version: int

    source_key: str
    source_locator: str | None

    reason_key: str

    @property
    def target(self) -> ExplanationTarget:
        """The exact reviewed decision this explanation is allowed to explain."""
        return (self.policy_id, self.policy_version, self.action)


#: Production explanations. Deliberately empty in V1; see the module docstring.
PERSONAL_DECISION_EXPLANATION_RULES: tuple[PersonalDecisionExplanationRule, ...] = ()


_TEXT_FIELDS = (
    "explanation_id",
    "explanation_version",
    "policy_id",
    "policy_version",
    "semantic_rule_id",
    "semantic_rule_version",
    "substance_key",
    "claim_key",
    "source_key",
    "reason_key",
)


def _validate_rule(rule: object, position: int) -> PersonalDecisionExplanationRule:
    where = f"explanation rule at position {position}"
    if not isinstance(rule, PersonalDecisionExplanationRule):
        raise PersonalDecisionExplanationRegistryError(
            f"{where} is not a PersonalDecisionExplanationRule"
        )

    for field_name in _TEXT_FIELDS:
        value = getattr(rule, field_name)
        if not isinstance(value, str) or not value.strip():
            raise PersonalDecisionExplanationRegistryError(f"{where} has a blank {field_name}")

    if not isinstance(rule.action, PersonalDecisionAction):
        raise PersonalDecisionExplanationRegistryError(
            f"{rule.explanation_id} has an invalid action"
        )
    if not isinstance(rule.claim_version, int) or isinstance(rule.claim_version, bool):
        raise PersonalDecisionExplanationRegistryError(
            f"{rule.explanation_id} has a non-integer claim_version"
        )
    if rule.claim_version <= 0:
        raise PersonalDecisionExplanationRegistryError(
            f"{rule.explanation_id} has claim_version {rule.claim_version}; versions start at 1"
        )
    if rule.source_locator is not None and (
        not isinstance(rule.source_locator, str) or not rule.source_locator.strip()
    ):
        raise PersonalDecisionExplanationRegistryError(
            f"{rule.explanation_id} has a malformed source_locator"
        )
    return rule


def build_explanation_index(
    rules: Iterable[PersonalDecisionExplanationRule],
) -> Mapping[ExplanationTarget, PersonalDecisionExplanationRule]:
    """Validate the reviewed explanation registry and index it by exact target.

    Two explanations for one reviewed decision are rejected outright, even
    when they name the same source and the same reason. Choosing between them
    -- by version recency, declaration order, shortest reason, or apparent
    source quality -- would be an unreviewed editorial decision about what a
    customer is told, made by a lookup.
    """
    index: dict[ExplanationTarget, PersonalDecisionExplanationRule] = {}
    identities: set[tuple[str, str]] = set()

    for position, candidate in enumerate(rules):
        rule = _validate_rule(candidate, position)

        identity = (rule.explanation_id, rule.explanation_version)
        if identity in identities:
            raise PersonalDecisionExplanationRegistryError(
                f"duplicate explanation identity {rule.explanation_id}@{rule.explanation_version}"
            )
        identities.add(identity)

        target = rule.target
        existing = index.get(target)
        if existing is not None:
            raise PersonalDecisionExplanationRegistryError(
                f"{rule.explanation_id}@{rule.explanation_version} and "
                f"{existing.explanation_id}@{existing.explanation_version} both explain "
                f"{rule.policy_id}@{rule.policy_version}; one reviewed decision carries at "
                "most one reviewed reason"
            )
        index[target] = rule

    return index
