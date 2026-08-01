"""Imports every model module so ``Base.metadata`` is complete.

Alembic autogenerate and the test schema builder both need every table
registered. A model that is not imported here is invisible to migrations, which
fails silently — the table simply never gets created. Add new model modules to
this list.
"""
from __future__ import annotations

# ruff: noqa: F401  (imported for the side effect of registering the mappers)
from app.domains.ai_gateway.models import AIRun, AIRunOutput
from app.domains.analytics.models import AppEvent
from app.domains.audit.models import AuditEvent
from app.domains.consent.models import Consent
from app.domains.entitlements.models import UsageLedgerEntry
from app.domains.identity.models import AccountLink
from app.domains.media.models import MediaAsset
from app.domains.profile.models import (
    AppearanceGoal,
    AppearanceProfile,
    AttributeObservation,
    FitPreference,
    LifestyleContext,
    OnboardingSession,
    ProfileAttribute,
    ProfileChangeEvent,
    StylePreference,
    UserConstraint,
)
from app.shared.database.base import Base
from app.shared.events.models import OutboxEvent
from app.shared.flags.models import FeatureFlag

__all__ = [
    "Base",
    "AccountLink",
    "Consent",
    "MediaAsset",
    "AIRun",
    "AIRunOutput",
    "FeatureFlag",
    "AuditEvent",
    "UsageLedgerEntry",
    "OutboxEvent",
    "AppEvent",
    "AppearanceProfile",
    "ProfileAttribute",
    "StylePreference",
    "FitPreference",
    "LifestyleContext",
    "AppearanceGoal",
    "UserConstraint",
    "AttributeObservation",
    "OnboardingSession",
    "ProfileChangeEvent",
]
