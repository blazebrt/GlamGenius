"""Controlled vocabularies used by the evidence provenance layer."""
from enum import StrEnum


class SourceType(StrEnum):
    OFFICIAL_REGULATION = "official_regulation"
    OFFICIAL_GUIDELINE = "official_guideline"
    GOVERNMENT_REFERENCE = "government_reference"
    SYSTEMATIC_REVIEW = "systematic_review"
    PEER_REVIEWED_RESEARCH = "peer_reviewed_research"
    PROFESSIONAL_CONSENSUS = "professional_consensus"
    INGREDIENT_REFERENCE_DATABASE = "ingredient_reference_database"
    MANUFACTURER_LABEL = "manufacturer_label"
    MANUFACTURER_TECHNICAL_DOCUMENT = "manufacturer_technical_document"
    MANUFACTURER_CLAIM = "manufacturer_claim"
    INDEPENDENT_LAB_REPORT = "independent_lab_report"
    TRADITIONAL_REFERENCE = "traditional_reference"
    OTHER = "other"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"
    UNAVAILABLE = "unavailable"


class EvidenceStrength(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    LIMITED = "limited"
    TRADITIONAL = "traditional_uncertain"
    INSUFFICIENT = "insufficient"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    QUALIFIED = "qualified"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class ReviewStatus(StrEnum):
    """Where an entry sits in the authoring queue.

    The path a knowledge entry takes is draft -> approved -> published.
    REJECTED is a terminal answer to a draft and always carries a reason.
    REVIEWED, SUPERSEDED and RETIRED predate the authoring tool and keep their
    original meanings; SUPERSEDED is what an older version becomes when an
    entry is edited.
    """

    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class EvidenceTier(StrEnum):
    """How good the backing for an entry is, in the product's own words.

    Deliberately separate from EvidenceStrength: that grades a body of
    research, while this is the tier an author picks in the authoring tool and
    the product surfaces. NOT_ENOUGH_INFORMATION is a real answer, not a gap.
    """

    CLINICALLY_STUDIED = "clinically_studied"
    CLASSICAL_TEXT = "classical_text"
    TRADITIONAL_USE = "traditional_use"
    NOT_ENOUGH_INFORMATION = "not_enough_information"
    AVOID = "avoid"


class ClaimSourceRelationship(StrEnum):
    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    LIMITS = "limits"
    CONTRADICTS = "contradicts"
    BACKGROUND = "background"


class RuleEvidenceRelationship(StrEnum):
    SUPPORTS = "supports"
    QUALIFIES = "qualifies"
    LIMITS = "limits"
    BACKGROUND = "background"


class RegulatoryContext(StrEnum):
    COSMETIC = "cosmetic"
    OTC_OR_REGULATED = "otc_or_regulated"
    PROFESSIONAL_GUIDANCE_REQUIRED = "professional_guidance_required"
    JURISDICTION_SENSITIVE = "jurisdiction_sensitive"
    UNKNOWN = "unknown"


class EvidenceDomain(StrEnum):
    SKIN_CARE = "skin_care"
    HAIR_CARE = "hair_care"
    HOME_CARE = "home_care"
    NUTRITION = "nutrition"
    SUPPLEMENTS = "supplements"
    PRODUCT_QUALITY = "product_quality"


class RuleKind(StrEnum):
    INGREDIENT_COMPATIBILITY = "ingredient_compatibility"
    INGREDIENT_CONTRAINDICATION = "ingredient_contraindication"
    INGREDIENT_SENSITIVITY = "ingredient_sensitivity"
    ROUTINE_GUIDANCE = "routine_guidance"
    #: The ten environment rules: what today's air, humidity, UV and
    #: temperature change about a routine.
    ENVIRONMENT_RESPONSE = "environment_response"
    NUTRITION_CONTEXT = "nutrition_context"
    SUPPLEMENT_CONTEXT = "supplement_context"


class ClaimType(StrEnum):
    COMPATIBILITY_CONTEXT = "compatibility_context"
    CONTRAINDICATION_CONTEXT = "contraindication_context"
    SENSITIVITY_CONTEXT = "sensitivity_context"
    USAGE_CONTEXT = "usage_context"
    REGULATORY_CONTEXT = "regulatory_context"
    NUTRITION_REFERENCE = "nutrition_reference"
    PRODUCT_PROVENANCE = "product_provenance"
    TRADITIONAL_USE = "traditional_use"


# Descriptive aliases keep the public vocabulary obvious to callers while the
# short names remain pleasant for model/service implementation code.
EvidenceSourceType = SourceType
EvidenceSourceStatus = SourceStatus
EvidenceClaimStrength = EvidenceStrength
EvidenceClaimStatus = ClaimStatus
EvidenceReviewStatus = ReviewStatus
EvidenceClaimTier = EvidenceTier
EvidenceClaimSourceRelationship = ClaimSourceRelationship
RuleEvidenceLinkRelationship = RuleEvidenceRelationship
EvidenceRuleKind = RuleKind
EvidenceClaimType = ClaimType


SOURCE_TYPES = tuple(x.value for x in SourceType)
SOURCE_STATUSES = tuple(x.value for x in SourceStatus)
EVIDENCE_STRENGTHS = tuple(x.value for x in EvidenceStrength)
CLAIM_STATUSES = tuple(x.value for x in ClaimStatus)
REVIEW_STATUSES = tuple(x.value for x in ReviewStatus)

#: Statuses that carry a completed human approval.
#:
#: Publishing is a step *past* approval, not an alternative to it: the
#: transition table only allows approved -> published, so a published claim was
#: reviewed by a person and still is. Checking for the literal "approved" alone
#: would make publishing a claim silently switch off the rules it supports.
APPROVED_REVIEW_STATUSES: frozenset[str] = frozenset({
    ReviewStatus.APPROVED.value,
    ReviewStatus.PUBLISHED.value,
})

EVIDENCE_TIERS = tuple(x.value for x in EvidenceTier)
CLAIM_SOURCE_RELATIONSHIPS = tuple(x.value for x in ClaimSourceRelationship)
RULE_EVIDENCE_RELATIONSHIPS = tuple(x.value for x in RuleEvidenceRelationship)
REGULATORY_CONTEXTS = tuple(x.value for x in RegulatoryContext)
EVIDENCE_DOMAINS = tuple(x.value for x in EvidenceDomain)
RULE_KINDS = tuple(x.value for x in RuleKind)
CLAIM_TYPES = tuple(x.value for x in ClaimType)
