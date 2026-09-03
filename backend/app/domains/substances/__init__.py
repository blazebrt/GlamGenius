"""Step 7A — canonical cross-domain substance identity.

This domain answers exactly one question: *what exact substance or material does
this exact reviewed name refer to?*

It does not answer whether the substance is good, bad, safe, risky or effective;
what it does; whether it suits a particular person; how much of it is present;
whether it is permitted; or how it interacts with anything else. Every one of
those is a claim about a substance **in a context**, requires its own evidence
with its own applicability, and belongs to a later milestone.

Identity is not safety. Identity is not function. Identity is not concentration.
Identity is not regulatory status. Keeping those apart is the entire point of
having this layer at all — see ``docs/architecture/SUBSTANCE_IDENTITY.md``.
"""
from app.domains.substances.enums import EntityKind, NameNamespace, SubstanceStatus
from app.domains.substances.identity_schema import (
    SUBSTANCE_IDENTITY_SCHEMA_VERSION,
    SubstanceIdentity,
    parse_identity,
)
from app.domains.substances.models import Substance, SubstanceName
from app.domains.substances.normalization import normalize_name
from app.domains.substances.service import (
    ResolutionStatus,
    SubstanceResolution,
    resolve_name,
    resolve_names,
)

__all__ = [
    "SUBSTANCE_IDENTITY_SCHEMA_VERSION",
    "EntityKind",
    "NameNamespace",
    "ResolutionStatus",
    "Substance",
    "SubstanceIdentity",
    "SubstanceName",
    "SubstanceResolution",
    "SubstanceStatus",
    "normalize_name",
    "parse_identity",
    "resolve_name",
    "resolve_names",
]
