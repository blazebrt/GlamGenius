"""The reviewed policy registry, and the validation that keeps it honest.

A policy rule says one thing: *if the governed state is exactly this, a
reviewer decided the product action is exactly that.* "Exactly this" includes
the precise set of Step 8C semantic rule identities and versions -- not the
direction they happen to point in.

That distinction is the reason this module exists. Two entirely different
published claims can both carry SUPPORTING while deserving opposite product
actions, so a policy keyed on `SUPPORTING_ONLY` would be a sweeping unreviewed
judgement wearing the costume of a lookup table. Keying on the exact identity
set means a policy reviewed for one body of evidence cannot silently apply to
another, and a claim revised to a new version drops out of its old policy
until someone reviews it again.

The production registry is empty in V1, and that is the design. The
architecture for carrying BUY / WAIT / SKIP exists; no reviewed instance of
one does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domains.personal_decision_aggregation import PersonalSignalSet
from app.domains.personal_decision_policy.enums import (
    PersonalDecisionAction,
    PersonalDecisionPolicyCategory,
)

#: (step_8c_rule_id, step_8c_rule_version)
SemanticRuleIdentity = tuple[str, str]

#: The complete governed state a policy rule is allowed to match:
#: category, exact semantic rule identity set, direction set, and the three
#: upstream epistemic gap flags. Nothing else -- no order, no counts, no
#: strength, no prose.
PolicyTarget = tuple[
    PersonalDecisionPolicyCategory,
    frozenset[SemanticRuleIdentity],
    PersonalSignalSet,
    bool,
    bool,
    bool,
]


class PersonalDecisionPolicyRegistryError(ValueError):
    """The policy registry is not fit to be used. Nothing is decided from it."""


@dataclass(frozen=True, slots=True)
class PersonalDecisionPolicyRule:
    """One reviewed mapping from an exact governed state to an exact action.

    ``semantic_rule_identities`` is the heart of it. A rule reviewed for
    ``{a@1}`` matches only ``{a@1}`` -- not ``{a@1, b@1}``, not ``{a@2}``, not
    ``{}``, however similar the direction looks.

    The three gap flags are part of the target rather than automatic blockers.
    Whether an unresolved ingredient or a missing personal-evidence path
    should stop an action is itself a policy question, and infrastructure
    inventing a universal answer would be the same overreach as inventing a
    direction. So the flags are stated exactly, and a reviewer decides.
    """

    policy_id: str
    policy_version: str
    category: PersonalDecisionPolicyCategory
    semantic_rule_identities: frozenset[SemanticRuleIdentity]
    signal_set: PersonalSignalSet
    has_identity_unresolved: bool
    has_identity_ambiguous: bool
    has_personal_evidence_gap: bool
    action: PersonalDecisionAction

    @property
    def target(self) -> PolicyTarget:
        """The complete governed state this rule is allowed to match."""
        return (
            self.category,
            self.semantic_rule_identities,
            self.signal_set,
            self.has_identity_unresolved,
            self.has_identity_ambiguous,
            self.has_personal_evidence_gap,
        )


#: Production policy. Deliberately empty in V1; see the module docstring.
PERSONAL_DECISION_POLICY_RULES: tuple[PersonalDecisionPolicyRule, ...] = ()


def _validate_semantic_identities(rule: PersonalDecisionPolicyRule) -> None:
    identities = rule.semantic_rule_identities
    if not isinstance(identities, frozenset):
        raise PersonalDecisionPolicyRegistryError(
            f"{rule.policy_id} must declare semantic_rule_identities as a frozenset"
        )
    if not identities:
        raise PersonalDecisionPolicyRegistryError(
            f"{rule.policy_id} declares an empty semantic identity set; a policy must "
            "name the exact reviewed evidence it was decided against"
        )
    for identity in identities:
        if not isinstance(identity, tuple) or len(identity) != 2:
            raise PersonalDecisionPolicyRegistryError(
                f"{rule.policy_id} has a malformed semantic identity entry"
            )
        semantic_rule_id, semantic_rule_version = identity
        if not isinstance(semantic_rule_id, str) or not semantic_rule_id.strip():
            raise PersonalDecisionPolicyRegistryError(
                f"{rule.policy_id} has a blank semantic rule id"
            )
        if not isinstance(semantic_rule_version, str) or not semantic_rule_version.strip():
            raise PersonalDecisionPolicyRegistryError(
                f"{rule.policy_id} has a blank semantic rule version"
            )


def _validate_rule(rule: object, position: int) -> PersonalDecisionPolicyRule:
    where = f"policy rule at position {position}"
    if not isinstance(rule, PersonalDecisionPolicyRule):
        raise PersonalDecisionPolicyRegistryError(f"{where} is not a PersonalDecisionPolicyRule")
    if not isinstance(rule.policy_id, str) or not rule.policy_id.strip():
        raise PersonalDecisionPolicyRegistryError(f"{where} has a blank policy_id")
    if not isinstance(rule.policy_version, str) or not rule.policy_version.strip():
        raise PersonalDecisionPolicyRegistryError(f"{rule.policy_id} has a blank policy_version")
    if not isinstance(rule.category, PersonalDecisionPolicyCategory):
        raise PersonalDecisionPolicyRegistryError(f"{rule.policy_id} has an invalid category")
    if not isinstance(rule.signal_set, PersonalSignalSet):
        raise PersonalDecisionPolicyRegistryError(f"{rule.policy_id} has an invalid signal_set")
    if rule.signal_set is PersonalSignalSet.NONE:
        raise PersonalDecisionPolicyRegistryError(
            f"{rule.policy_id} targets an empty direction set; no reviewed evidence direction "
            "is present in that state, so no product action may be attached to it"
        )
    if not isinstance(rule.action, PersonalDecisionAction):
        raise PersonalDecisionPolicyRegistryError(f"{rule.policy_id} has an invalid action")
    for flag_name in (
        "has_identity_unresolved",
        "has_identity_ambiguous",
        "has_personal_evidence_gap",
    ):
        if not isinstance(getattr(rule, flag_name), bool):
            raise PersonalDecisionPolicyRegistryError(
                f"{rule.policy_id} has a non-boolean {flag_name}"
            )
    _validate_semantic_identities(rule)
    return rule


def build_policy_index(
    rules: Iterable[PersonalDecisionPolicyRule],
) -> Mapping[PolicyTarget, PersonalDecisionPolicyRule]:
    """Validate the reviewed policy registry and index it by exact target.

    Fails closed on every ambiguity. Two policies aimed at the same governed
    state are rejected outright -- not resolved by version recency, by
    declaration order, or by preferring the "safer" action. Each of those
    would be an unreviewed policy decision made by a tie-break, and preferring
    SKIP is no more legitimate than preferring BUY.

    Retiring a policy version is future governance, done deliberately. It is
    not something a lookup gets to infer from a version number.
    """
    index: dict[PolicyTarget, PersonalDecisionPolicyRule] = {}
    identities: set[tuple[str, str]] = set()

    for position, candidate in enumerate(rules):
        rule = _validate_rule(candidate, position)

        identity = (rule.policy_id, rule.policy_version)
        if identity in identities:
            raise PersonalDecisionPolicyRegistryError(
                f"duplicate policy identity {rule.policy_id}@{rule.policy_version}"
            )
        identities.add(identity)

        target = rule.target
        existing = index.get(target)
        if existing is not None:
            raise PersonalDecisionPolicyRegistryError(
                f"{rule.policy_id}@{rule.policy_version} and "
                f"{existing.policy_id}@{existing.policy_version} target the same governed "
                "state; a reviewed conflict must be resolved by review, not by lookup order"
            )
        index[target] = rule

    return index
