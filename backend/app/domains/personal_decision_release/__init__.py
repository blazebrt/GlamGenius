"""Step 8H governed personal decision knowledge release.

The controlled production-release mechanism for the decision chain built in
Steps 8C to 8F. Reviewed semantic rules, policy rules and explanation rules
become one immutable, hashed, cross-validated bundle that is approved as a
whole and activated atomically -- or not at all.

The three rule sets travel together because they are one chain. A semantic
rule with no policy changes what Step 8C matches while nobody has decided what
should follow; a policy with no explanation can select an action that can
never be shown with a source. Releasing them separately would put production
into a state no reviewer ever looked at.

This layer authors no evidence -- Step 8G owns that -- and infers nothing. It
checks that a reviewed bundle still describes reality: that every claim it
names is still published and eligible, that every policy's declared direction
set matches the exact semantics it references, that every policy has exactly
one explanation, and that every citation still points at a source somebody can
open. The direction, the action and the reason are copied through exactly as a
human wrote them.

No real release is seeded. After this milestone production has zero active
releases and therefore still emits no BUY / WAIT / SKIP.
"""

from app.domains.personal_decision_release.enums import (
    ALLOWED_RELEASE_TRANSITIONS,
    PERSONAL_DECISION_RELEASE_KEY,
    PersonalDecisionReleaseStatus,
    PersonalDecisionReleaseValidationCode,
)
from app.domains.personal_decision_release.manifest import (
    MAX_CANONICAL_MANIFEST_BYTES,
    MAX_EXPLANATION_RULES,
    MAX_POLICY_RULES,
    MAX_SEMANTIC_RULES,
    PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION,
    PersonalDecisionReleaseManifest,
    PersonalDecisionReleaseManifestError,
    assert_registries_valid,
    canonical_json,
    canonical_manifest,
    manifest_content_hash,
    parse_release_manifest,
)
from app.domains.personal_decision_release.models import PersonalDecisionRelease
from app.domains.personal_decision_release.runtime import (
    ActivePersonalDecisionRelease,
    ReleasedPersonalDecisionResult,
    evaluate_personal_decision_with_release,
    load_active_personal_decision_release,
    materialise_active_release,
    select_active_release,
)
from app.domains.personal_decision_release.validation import (
    RELEASE_VERIFICATION_CHECKPOINTS,
    PersonalDecisionReleaseInvariantError,
    PersonalDecisionReleaseValidationError,
    ReleaseEvidenceReport,
    ReleaseVerification,
    parse_release_verification,
    validate_release_evidence,
    validate_release_manifest,
    validate_release_structure,
)

__all__ = [
    "ALLOWED_RELEASE_TRANSITIONS",
    "MAX_CANONICAL_MANIFEST_BYTES",
    "MAX_EXPLANATION_RULES",
    "MAX_POLICY_RULES",
    "MAX_SEMANTIC_RULES",
    "PERSONAL_DECISION_RELEASE_KEY",
    "PERSONAL_DECISION_RELEASE_MANIFEST_SCHEMA_VERSION",
    "RELEASE_VERIFICATION_CHECKPOINTS",
    "ActivePersonalDecisionRelease",
    "PersonalDecisionRelease",
    "PersonalDecisionReleaseInvariantError",
    "PersonalDecisionReleaseManifest",
    "PersonalDecisionReleaseManifestError",
    "PersonalDecisionReleaseStatus",
    "PersonalDecisionReleaseValidationCode",
    "PersonalDecisionReleaseValidationError",
    "ReleaseEvidenceReport",
    "ReleaseVerification",
    "ReleasedPersonalDecisionResult",
    "assert_registries_valid",
    "canonical_json",
    "canonical_manifest",
    "evaluate_personal_decision_with_release",
    "load_active_personal_decision_release",
    "manifest_content_hash",
    "materialise_active_release",
    "parse_release_manifest",
    "parse_release_verification",
    "select_active_release",
    "validate_release_evidence",
    "validate_release_manifest",
    "validate_release_structure",
]
