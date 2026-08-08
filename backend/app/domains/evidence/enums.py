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
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


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
EvidenceClaimSourceRelationship = ClaimSourceRelationship
RuleEvidenceLinkRelationship = RuleEvidenceRelationship
EvidenceRuleKind = RuleKind
EvidenceClaimType = ClaimType


SOURCE_TYPES = tuple(x.value for x in SourceType)
SOURCE_STATUSES = tuple(x.value for x in SourceStatus)
EVIDENCE_STRENGTHS = tuple(x.value for x in EvidenceStrength)
CLAIM_STATUSES = tuple(x.value for x in ClaimStatus)
REVIEW_STATUSES = tuple(x.value for x in ReviewStatus)
CLAIM_SOURCE_RELATIONSHIPS = tuple(x.value for x in ClaimSourceRelationship)
RULE_EVIDENCE_RELATIONSHIPS = tuple(x.value for x in RuleEvidenceRelationship)
REGULATORY_CONTEXTS = tuple(x.value for x in RegulatoryContext)
EVIDENCE_DOMAINS = tuple(x.value for x in EvidenceDomain)
RULE_KINDS = tuple(x.value for x in RuleKind)
CLAIM_TYPES = tuple(x.value for x in ClaimType)
