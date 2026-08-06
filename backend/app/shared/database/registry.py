"""Imports every model module so ``Base.metadata`` is complete.

Alembic autogenerate and the test schema builder both need every table
registered. A model that is not imported here is invisible to migrations,
which fails silently — the table simply never gets created. Add new model
modules to this list.
"""
from __future__ import annotations

# ruff: noqa: F401  (imported for the side effect of registering the mappers)
from app.domains.ai_gateway.models import AIRun, AIRunOutput
from app.domains.analytics.models import AppEvent
from app.domains.audit.models import AuditEvent
from app.domains.beta_access.models import (
    BetaUsageEvent,
    Invite,
    InviteRedemption,
)
from app.domains.consent.models import Consent
from app.domains.identity.models import Account
from app.domains.inventory import models as inventory_models
from app.domains.media.models import MediaAsset
from app.domains.planning import models as planning_models
from app.domains.privacy.models import AccountDeletionJob
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
from app.domains.progress import models as progress_models
from app.domains.quiz.models import QuizSubmission
from app.domains.recommendation import models as recommendation_models
from app.domains.reference import (
    IngredientContraindicationRule,
    IngredientSensitivityRule,
    InventorySubtypeDefinition,
    RoutineTemplateStep,
    SeedVersionRecord,
    SupplementContextRule,
)
from app.domains.routines import models as routines_models
from app.domains.scan.models import Scan
from app.domains.system.models import WorkerStatus
from app.shared.database.base import Base
from app.shared.flags.models import FeatureFlag

__all__ = [
    "Base",
    "Account",
    "Consent",
    "MediaAsset",
    "AccountDeletionJob",
    "WorkerStatus",
    "AIRun",
    "AIRunOutput",
    "FeatureFlag",
    "AuditEvent",
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
    "Invite",
    "InviteRedemption",
    "BetaUsageEvent",
    "QuizSubmission",
    "Scan",
    "inventory_models",
    "recommendation_models",
    "planning_models",
    "routines_models",
    "progress_models",
    "SeedVersionRecord",
    "RoutineTemplateStep",
    "SupplementContextRule",
    "IngredientContraindicationRule",
    "IngredientSensitivityRule",
    "InventorySubtypeDefinition",
]
