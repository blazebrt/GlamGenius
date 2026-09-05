"""The reviewed rule registry, and the validation that keeps it honest.

A rule says one thing: *if Step 8B has already accepted this exact claim
version for this exact canonical substance under this exact category, a
reviewer has decided the claim may contribute in this direction.* It decides
nothing about identity, applicability, evidence eligibility, safety, or the
product.

The production registry is empty on purpose, and that is the whole design.
A mapping from published evidence to product policy is a separate reviewed
decision, and until one exists for a given claim version the honest answer is
NOT_ENOUGH_DECISION_SEMANTICS. Seeding plausible-looking rules to make the
feature "work" would invent exactly the judgement this milestone exists to
withhold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domains.personal_applicability import PersonalApplicabilityCategory
from app.domains.personal_decision_semantics.enums import PersonalDecisionSignal

#: (category, substance_key, claim_key, claim_version)
RuleTarget = tuple[str, str, str, int]


class PersonalDecisionSemanticRegistryError(ValueError):
    """The registry is not fit to be used. Nothing is projected from it."""


@dataclass(frozen=True, slots=True)
class PersonalDecisionSemanticRule:
    """One reviewed mapping from an exact claim version to a direction."""

    rule_id: str
    rule_version: str
    category: PersonalApplicabilityCategory
    substance_key: str
    claim_key: str
    claim_version: int
    signal: PersonalDecisionSignal

    @property
    def target(self) -> RuleTarget:
        """The complete identity this rule is allowed to match.

        All four parts are required. Matching on a subset -- claim_key alone,
        say, or substance plus claim without the version -- would let a claim
        revised after review inherit the old review's direction.
        """
        return (str(self.category), self.substance_key, self.claim_key, self.claim_version)


#: Production rules. Deliberately empty in V1; see the module docstring.
PERSONAL_DECISION_SEMANTIC_RULES: tuple[PersonalDecisionSemanticRule, ...] = ()


def build_rule_index(
    rules: Iterable[PersonalDecisionSemanticRule],
) -> Mapping[RuleTarget, PersonalDecisionSemanticRule]:
    """Validate the registry and index it by exact target.

    Fails closed on every ambiguity. Two rules aimed at the same evidence
    identity are rejected outright rather than resolved by recency, order, or
    a "safer" preference for CAUTIONARY -- picking a winner here would be an
    unreviewed policy decision made by a tie-break.
    """
    index: dict[RuleTarget, PersonalDecisionSemanticRule] = {}
    identities: set[tuple[str, str]] = set()

    for position, rule in enumerate(rules):
        where = f"rule at position {position}"

        if not isinstance(rule, PersonalDecisionSemanticRule):
            raise PersonalDecisionSemanticRegistryError(f"{where} is not a PersonalDecisionSemanticRule")
        if not rule.rule_id or not rule.rule_id.strip():
            raise PersonalDecisionSemanticRegistryError(f"{where} has a blank rule_id")
        if not rule.rule_version or not rule.rule_version.strip():
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has a blank rule_version")
        if not rule.substance_key or not rule.substance_key.strip():
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has a blank substance_key")
        if not rule.claim_key or not rule.claim_key.strip():
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has a blank claim_key")
        if not isinstance(rule.claim_version, int) or isinstance(rule.claim_version, bool):
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has a non-integer claim_version")
        if rule.claim_version <= 0:
            raise PersonalDecisionSemanticRegistryError(
                f"{rule.rule_id} has claim_version {rule.claim_version}; versions start at 1"
            )
        if not isinstance(rule.category, PersonalApplicabilityCategory):
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has an invalid category")
        if not isinstance(rule.signal, PersonalDecisionSignal):
            raise PersonalDecisionSemanticRegistryError(f"{rule.rule_id} has an invalid signal")

        identity = (rule.rule_id, rule.rule_version)
        if identity in identities:
            raise PersonalDecisionSemanticRegistryError(
                f"duplicate rule identity {rule.rule_id}@{rule.rule_version}"
            )
        identities.add(identity)

        target = rule.target
        existing = index.get(target)
        if existing is not None:
            raise PersonalDecisionSemanticRegistryError(
                f"{rule.rule_id}@{rule.rule_version} and "
                f"{existing.rule_id}@{existing.rule_version} both target {target}; "
                "an evidence identity may carry at most one reviewed mapping"
            )
        index[target] = rule

    return index
