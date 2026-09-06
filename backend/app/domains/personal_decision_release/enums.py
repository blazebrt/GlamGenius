"""Controlled Step 8H release vocabulary.

Four statuses and a fixed set of failure codes. Neither says anything about a
product, an ingredient or a person: a release is a bundle of reviewed rules,
and these words describe only where that bundle sits in its review lifecycle
and why a bundle was refused.
"""

from enum import StrEnum


class PersonalDecisionReleaseStatus(StrEnum):
    """Where one reviewed decision bundle sits in its lifecycle.

    The path is DRAFT -> APPROVED -> ACTIVE -> RETIRED, plus ACTIVE -> RETIRED
    for emergency deactivation. There is deliberately no way back. A reviewed
    release is immutable once approved, so "returning it to draft" would mean
    editing something a review already blessed; and reactivating a retired
    release would put knowledge back into production without anyone looking at
    it against today's evidence. Both are done by cloning into a new draft and
    reviewing that.
    """

    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    RETIRED = "retired"


#: Every transition the lifecycle permits. Anything absent is refused, and the
#: map is the single authority -- no service re-states these rules inline.
ALLOWED_RELEASE_TRANSITIONS: dict[
    PersonalDecisionReleaseStatus, frozenset[PersonalDecisionReleaseStatus]
] = {
    PersonalDecisionReleaseStatus.DRAFT: frozenset({PersonalDecisionReleaseStatus.APPROVED}),
    PersonalDecisionReleaseStatus.APPROVED: frozenset({PersonalDecisionReleaseStatus.ACTIVE}),
    PersonalDecisionReleaseStatus.ACTIVE: frozenset({PersonalDecisionReleaseStatus.RETIRED}),
    PersonalDecisionReleaseStatus.RETIRED: frozenset(),
}

#: The one production release series in V1. A constant, never a client input:
#: accepting a key from a request would let an unreviewed series be created and
#: activated beside the reviewed one, and the runtime loader would then have to
#: choose between them.
PERSONAL_DECISION_RELEASE_KEY = "for_you.personal_decision"


class PersonalDecisionReleaseValidationCode(StrEnum):
    """Exactly why a release was refused.

    Deterministic and machine-readable so an admin sees which link of the
    reviewed chain broke, rather than prose that has to be re-parsed. None of
    these is recoverable by the system: every one means a human must look at
    the bundle again.
    """

    # --- the bundle itself ------------------------------------------------
    RELEASE_EMPTY = "RELEASE_EMPTY"
    RELEASE_NOT_EDITABLE = "RELEASE_NOT_EDITABLE"
    RELEASE_VERIFICATION_INCOMPLETE = "RELEASE_VERIFICATION_INCOMPLETE"
    RELEASE_UNRESOLVED_DOUBT = "RELEASE_UNRESOLVED_DOUBT"
    RELEASE_CONTENT_HASH_MISMATCH = "RELEASE_CONTENT_HASH_MISMATCH"
    #: The persisted `manifest_schema_version` column is not the supported one.
    #: Separate from the manifest's own internal `schema_version`: the column is
    #: what the runtime loader reads first, so a row whose column disagrees can
    #: never be loaded and must never be installed as active.
    RELEASE_SCHEMA_VERSION_UNSUPPORTED = "RELEASE_SCHEMA_VERSION_UNSUPPORTED"
    #: A release holds global governed knowledge. Anything naming one person,
    #: one profile or one scan has escaped from runtime into the bundle.
    RELEASE_PERSONAL_DATA_PRESENT = "RELEASE_PERSONAL_DATA_PRESENT"

    # --- semantic rule -> published evidence ------------------------------
    EVIDENCE_CLAIM_NOT_PUBLISHED = "EVIDENCE_CLAIM_NOT_PUBLISHED"
    EVIDENCE_CLAIM_NOT_ELIGIBLE = "EVIDENCE_CLAIM_NOT_ELIGIBLE"
    SEMANTIC_EVIDENCE_MISMATCH = "SEMANTIC_EVIDENCE_MISMATCH"

    # --- policy -> semantics ----------------------------------------------
    POLICY_SEMANTIC_NOT_IN_RELEASE = "POLICY_SEMANTIC_NOT_IN_RELEASE"
    POLICY_CATEGORY_MISMATCH = "POLICY_CATEGORY_MISMATCH"
    POLICY_SIGNAL_SET_MISMATCH = "POLICY_SIGNAL_SET_MISMATCH"
    UNREFERENCED_SEMANTIC_RULE = "UNREFERENCED_SEMANTIC_RULE"

    # --- explanation -> policy, semantics and source ----------------------
    POLICY_EXPLANATION_MISSING = "POLICY_EXPLANATION_MISSING"
    EXPLANATION_POLICY_NOT_IN_RELEASE = "EXPLANATION_POLICY_NOT_IN_RELEASE"
    EXPLANATION_ACTION_MISMATCH = "EXPLANATION_ACTION_MISMATCH"
    EXPLANATION_SEMANTIC_NOT_IN_POLICY = "EXPLANATION_SEMANTIC_NOT_IN_POLICY"
    EXPLANATION_EVIDENCE_ANCHOR_MISMATCH = "EXPLANATION_EVIDENCE_ANCHOR_MISMATCH"
    EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE = "EXPLANATION_SOURCE_PATH_NOT_ELIGIBLE"

    # --- runtime corruption -----------------------------------------------
    MULTIPLE_ACTIVE_DECISION_RELEASES = "MULTIPLE_ACTIVE_DECISION_RELEASES"
