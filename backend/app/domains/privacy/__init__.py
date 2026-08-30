"""Privacy: authoritative, versioned export of everything the account owns.

The registry
------------
Every ORM table registered on :data:`app.shared.database.registry.Base.metadata`
is classified in :data:`REGISTRY` as one of:

* ``INCLUDED`` — user-owned data exported to the caller.
* ``NOT_USER_OWNED`` — reference or global data (taxonomy, rules, templates,
  feature flags). Never account-linked.
* ``OPERATIONAL`` — internal state (outbox, deletion jobs, notification
  delivery attempts) whose personal payload is redacted or intentionally
  omitted.
* ``SECRET_EXCLUDED`` — a table whose contents include secrets, credentials
  or tokens and must never appear in an export.
* ``LEGALLY_RETAINED`` — data we keep even after account deletion (a minimal
  deletion audit tombstone).

The critical safety net is :func:`assert_registry_complete`, which walks the
metadata and fails if a new table is introduced without a classification.
Adding a table without deciding whether it goes into the export is exactly
the kind of silent omission a periodic audit is meant to catch.

The export shape
----------------
::

    {
      "schema_version": "1.0",
      "generated_at": "...",
      "account": {...},
      "domains": {
        "identity": {...},
        "profile": {...},
        "consent": {...},
        "inventory": {...},
        ...
      }
    }

Each domain has a stable key and a documented schema; see the module docstrings
of ``registry.py`` and ``service.py``.
"""
from __future__ import annotations

from enum import StrEnum

EXPORT_SCHEMA_VERSION = "1.0"


class Classification(StrEnum):
    INCLUDED = "included"
    NOT_USER_OWNED = "not_user_owned"
    OPERATIONAL = "operational"
    SECRET_EXCLUDED = "secret_excluded"
    LEGALLY_RETAINED = "legally_retained"


# One entry per table. Adding a new table without adding a row here fails
# ``assert_registry_complete``, which is exercised by
# ``tests/test_privacy_export_registry.py``.
REGISTRY: dict[str, Classification] = {
    # --- Identity + lifecycle ---
    "accounts": Classification.INCLUDED,
    # Deletion tombstone; retained after the account is gone.
    "account_deletion_jobs": Classification.LEGALLY_RETAINED,
    # --- Invite gate ---
    # Global invites table is not per-account; the redemption *is*.
    "invites": Classification.NOT_USER_OWNED,
    "invite_redemptions": Classification.INCLUDED,
    "invite_registration_reservations": Classification.OPERATIONAL,
    # --- Consent ---
    "consents": Classification.INCLUDED,
    # --- Profile + onboarding ---
    "appearance_profiles": Classification.INCLUDED,
    "profile_attributes": Classification.INCLUDED,  # via appearance_profiles
    "profile_change_events": Classification.INCLUDED,  # via appearance_profiles
    "attribute_observations": Classification.INCLUDED,  # via appearance_profiles
    "style_preferences": Classification.INCLUDED,  # via appearance_profiles
    "fit_preferences": Classification.INCLUDED,  # via appearance_profiles
    "lifestyle_context": Classification.INCLUDED,  # via appearance_profiles
    "user_constraints": Classification.INCLUDED,  # via appearance_profiles
    "appearance_goals": Classification.INCLUDED,  # via appearance_profiles
    "user_reported_observations": Classification.INCLUDED,
    "onboarding_sessions": Classification.INCLUDED,  # via appearance_profiles
    "hydration_preferences": Classification.INCLUDED,
    "nutrition_preferences": Classification.INCLUDED,
    # --- Inventory ---
    "inventory_items": Classification.INCLUDED,
    "inventory_attributes": Classification.INCLUDED,  # via inventory_items
    "inventory_events": Classification.INCLUDED,
    "inventory_item_images": Classification.INCLUDED,  # via inventory_items
    "inventory_import_jobs": Classification.INCLUDED,
    "inventory_value_events": Classification.INCLUDED,  # via inventory_items
    "item_condition_events": Classification.INCLUDED,  # via inventory_items
    "item_expiry_events": Classification.INCLUDED,  # via inventory_items
    "item_usage_events": Classification.INCLUDED,  # via inventory_items
    "item_relationships": Classification.INCLUDED,
    "duplicate_candidates": Classification.INCLUDED,
    "product_ingredients": Classification.INCLUDED,
    "product_expiry_events": Classification.INCLUDED,
    "laundry_state_events": Classification.INCLUDED,
    # Category-specific detail rows are joined to inventory_items and exported
    # under the inventory domain via that relationship.
    "wardrobe_item_details": Classification.INCLUDED,
    "shoe_item_details": Classification.INCLUDED,
    "accessory_item_details": Classification.INCLUDED,
    "beauty_product_details": Classification.INCLUDED,
    "hair_product_details": Classification.INCLUDED,
    "perfume_details": Classification.INCLUDED,
    "supplement_details": Classification.INCLUDED,
    "supplement_safety_flags": Classification.INCLUDED,
    "supplement_label_components": Classification.INCLUDED,
    # Reference taxonomy
    "inventory_categories": Classification.NOT_USER_OWNED,
    # --- Scans + AI ---
    "scans": Classification.INCLUDED,
    "ai_runs": Classification.INCLUDED,
    "ai_run_outputs": Classification.INCLUDED,  # via ai_runs
    # --- Quiz + styling ---
    "quiz_submissions": Classification.INCLUDED,
    "occasions": Classification.INCLUDED,
    "style_requests": Classification.INCLUDED,
    "recommendation_runs": Classification.INCLUDED,
    "recommendation_inputs": Classification.INCLUDED,  # via recommendation_runs
    "recommendation_entitlements": Classification.INCLUDED,
    "looks": Classification.INCLUDED,
    "look_items": Classification.INCLUDED,  # via looks
    "look_adjustments": Classification.INCLUDED,
    "look_feedback": Classification.INCLUDED,
    "outfit_schedule": Classification.INCLUDED,
    "feedback_events": Classification.INCLUDED,
    # --- Shopping ---
    "shopping_candidates": Classification.INCLUDED,
    "purchase_evaluations": Classification.INCLUDED,
    "purchase_evaluation_factors": Classification.INCLUDED,  # via purchase_evaluations
    "purchase_decisions": Classification.INCLUDED,
    # --- Planning + weather ---
    "daily_plans": Classification.INCLUDED,
    "daily_plan_actions": Classification.INCLUDED,  # via daily_plans
    "daily_plan_inputs": Classification.INCLUDED,  # via daily_plans
    "weekly_plans": Classification.INCLUDED,
    "weekly_plan_days": Classification.INCLUDED,  # via weekly_plans
    "calendar_events": Classification.INCLUDED,
    "weather_snapshots": Classification.INCLUDED,
    "air_quality_snapshots": Classification.INCLUDED,
    "plan_recalculation_events": Classification.INCLUDED,
    "event_ready_plans": Classification.INCLUDED,
    "event_ready_actions": Classification.INCLUDED,  # via event_ready_plans
    # --- Routines + safety ---
    "routines": Classification.INCLUDED,
    "routine_steps": Classification.INCLUDED,  # via routines
    "routine_adherence": Classification.INCLUDED,
    "routine_recommendation_runs": Classification.INCLUDED,
    "care_experience_feedback": Classification.INCLUDED,
    # Maintenance timing is entirely customer-declared, so it is theirs.
    "maintenance_preferences": Classification.INCLUDED,
    "maintenance_events": Classification.INCLUDED,
    "routine_templates": Classification.NOT_USER_OWNED,
    "ingredients": Classification.NOT_USER_OWNED,
    "ingredient_aliases": Classification.NOT_USER_OWNED,
    "ingredient_rules": Classification.NOT_USER_OWNED,
    "compatibility_rules": Classification.NOT_USER_OWNED,
    "compatibility_edges": Classification.INCLUDED,  # per-account compatibility overrides
    "contraindication_rules": Classification.NOT_USER_OWNED,
    "perfume_context_rules": Classification.NOT_USER_OWNED,
    "appearance_nutrition_rules": Classification.NOT_USER_OWNED,
    # --- Progress + goals + memory ---
    "metric_definitions": Classification.NOT_USER_OWNED,
    "metric_events": Classification.INCLUDED,
    "milestone_rules": Classification.NOT_USER_OWNED,
    "milestones": Classification.INCLUDED,
    "progress_goals": Classification.INCLUDED,
    "goal_updates": Classification.INCLUDED,
    "progress_photos": Classification.INCLUDED,
    "progress_snapshots": Classification.INCLUDED,
    "comparison_sessions": Classification.INCLUDED,
    "score_explanations": Classification.INCLUDED,
    "streaks": Classification.INCLUDED,
    "gamification_events": Classification.INCLUDED,
    "memory_facts": Classification.INCLUDED,
    "memory_revisions": Classification.INCLUDED,  # via memory_facts
    "memory_sources": Classification.INCLUDED,  # via memory_facts
    "memory_category_preferences": Classification.INCLUDED,
    # --- Media (metadata only; bytes are opaque to the export) ---
    "media_assets": Classification.INCLUDED,
    # --- Notifications + integrations ---
    "notification_preferences": Classification.INCLUDED,
    "notification_deliveries": Classification.INCLUDED,
    # Expo push tokens are secret-like operational data and never exported.
    "notification_devices": Classification.SECRET_EXCLUDED,
    "external_integrations": Classification.INCLUDED,
    # One-time OAuth CSRF/replay state is operational metadata, never user export.
    "external_oauth_states": Classification.OPERATIONAL,
    # --- Audit + beta usage ---
    "audit_events": Classification.INCLUDED,
    "beta_usage_events": Classification.INCLUDED,
    "app_events": Classification.INCLUDED,
    # --- Feature flags (global) ---
    "feature_flags": Classification.NOT_USER_OWNED,
    # --- Operational only ---
    "system_worker_status": Classification.OPERATIONAL,
    # --- Reference data added by the seed catalogue completion ---
    # Note: ``routine_templates`` and ``perfume_context_rules`` were declared
    # earlier in this dict; the entries below cover only the new tables.
    "seed_version_records": Classification.OPERATIONAL,
    "routine_template_steps": Classification.NOT_USER_OWNED,
    "supplement_context_rules": Classification.NOT_USER_OWNED,
    "ingredient_contraindication_rules": Classification.NOT_USER_OWNED,
    "ingredient_sensitivity_rules": Classification.NOT_USER_OWNED,
    "inventory_subtype_definitions": Classification.NOT_USER_OWNED,
    # Nutrition composition metadata is global reference data; no account
    # ownership or personal food records are stored in these tables.
    "food_composition_datasets": Classification.NOT_USER_OWNED,
    "food_reference_items": Classification.NOT_USER_OWNED,
    "food_nutrient_values": Classification.NOT_USER_OWNED,
    # Evidence provenance is global release-owned reference data. It must not
    # acquire account, inventory, media, or AI-run ownership relationships.
    "evidence_sources": Classification.NOT_USER_OWNED,
    "evidence_claims": Classification.NOT_USER_OWNED,
    "evidence_claim_sources": Classification.NOT_USER_OWNED,
    "rule_evidence_links": Classification.NOT_USER_OWNED,
    # The supplement absorption knowledge base is the same kind of thing: one
    # row per compound form, shipped with the release and identical for every
    # account. A customer's own label transcriptions live in
    # supplement_label_components, which is account-owned and exported.
    "supplement_component_knowledge": Classification.NOT_USER_OWNED,
    # --- Barcode scanning ---
    # A device row carries the hash of the token that authenticates the phone.
    # It authenticates, so it never leaves in an export.
    "scan_devices": Classification.SECRET_EXCLUDED,
    # What we know about a barcode is the same for everybody: a product, its
    # confidence, its FSSAI licence. Nobody's personal data is in it.
    "product_records": Classification.NOT_USER_OWNED,
    # A person's own scan history is theirs. Exported by the ``product_scans``
    # domain in export.py. Scans made anonymously carry no account and belong
    # to nobody, so they are not in anybody's export.
    "scan_events": Classification.INCLUDED,
}


def assert_registry_complete() -> None:
    """Fail if a table lives in the ORM but has no classification.

    Called from a test on every CI run; also safe to call at boot as a
    defensive check.
    """
    # Import inside the function so this module has no import-time metadata
    # side effect. Alembic must be able to import it before models load.
    from app.shared.database.registry import Base

    tables: set[str] = {t.name for t in Base.metadata.sorted_tables}
    unclassified = tables - set(REGISTRY.keys())
    stale = set(REGISTRY.keys()) - tables
    problems = []
    if unclassified:
        problems.append(
            "Tables missing from the privacy export registry (add an entry in "
            "app/domains/privacy/__init__.py): "
            + ", ".join(sorted(unclassified))
        )
    if stale:
        problems.append(
            "Privacy registry references tables that no longer exist: "
            + ", ".join(sorted(stale))
        )
    if problems:
        raise AssertionError("\n".join(problems))


def included_tables() -> set[str]:
    return {name for name, kind in REGISTRY.items() if kind == Classification.INCLUDED}
