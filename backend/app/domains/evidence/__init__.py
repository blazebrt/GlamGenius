"""Global evidence provenance models and services.

Evidence is release-owned reference data.  It is deliberately independent of
accounts, inventory, media and AI-run rows so a source can be reviewed once
and reused consistently across every user's deterministic rule output.
"""

from app.domains.evidence.applicability import (
    EVIDENCE_APPLICABILITY_VERSION,
    ApplicabilityValidationResult,
    EvidenceApplicability,
    parse_behavior_applicability,
)
from app.domains.evidence.models import (
    EvidenceClaim,
    EvidenceClaimSource,
    EvidenceSource,
    RuleEvidenceLink,
)

__all__ = [
    "EvidenceSource",
    "EvidenceClaim",
    "EvidenceClaimSource",
    "RuleEvidenceLink",
    "EVIDENCE_APPLICABILITY_VERSION",
    "EvidenceApplicability",
    "ApplicabilityValidationResult",
    "parse_behavior_applicability",
]
