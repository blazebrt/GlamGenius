"""Export coverage: where every ``INCLUDED`` table is exported, and how.

``REGISTRY`` classifies a table ``INCLUDED``, which is a promise that the
account holder can read it back. The registry alone never kept that promise:
CI proved every table was *classified*, not that anything *exported* it, and
33 tables classified ``INCLUDED`` were read by no handler at all.

:data:`EXPORT_COVERAGE` is the other half of the promise. It has exactly one
entry per ``INCLUDED`` table — never more, never fewer — and each entry says:

* which export domain the rows appear in, and at which path in that domain;
* how the rows are scoped to the account **in SQL**: by their own
  ``account_id``, or through an account-owned parent row;
* how they are laid out: a flat chronological list, or grouped by the human in
  the household they describe;
* which handler writes them. ``CONTRACT`` rows are exported straight from the
  entry by the generic, SQL-scoped exporter in :mod:`.export`. ``DOMAIN`` rows
  are written by a hand-written domain handler, because their structure is
  subject-aware or their serialisation is deliberately reduced;
* which internal fields are withheld, and which references to other
  account-owned rows are kept only when the referenced row is this account's.

It is load-bearing, not decorative. :func:`.export.build_export` refuses to
return an export when the entries and the registry disagree, when a covered
table was not read by its declared domain, or when a declared path is missing
from the output — the request fails as incomplete instead of answering 200
with part of somebody's data. ``tests/test_privacy_export_completeness.py``
holds the same equality and seeds a real row for every contract-exported table.

This module names tables and columns as strings and imports no model, so it is
as safe to import early as the registry itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    """How a table's rows are scoped to one account, at the SQL level."""

    #: The account row itself: ``accounts.id = :account``.
    ACCOUNT_ROW = "account_row"
    #: ``<table>.account_id = :account``.
    ACCOUNT = "account_id"
    #: ``<table>.<fk> IN (the account's own rows of the parent table)``. For a
    #: child row that carries no ``account_id`` of its own.
    PARENT = "parent"


class Layout(StrEnum):
    #: One list, in a deterministic order.
    FLAT = "flat"
    #: Grouped by the household member each row describes. Rows nobody can be
    #: shown to own are kept, unattributed, and a foreign subject id is stripped.
    SUBJECT_GROUPED = "subject_grouped"
    #: Summarised into the fields a person can read (the identity section).
    SUMMARY = "summary"


class Handler(StrEnum):
    #: Exported straight from this entry by the generic SQL-scoped exporter.
    CONTRACT = "contract"
    #: Exported by a hand-written domain handler in :mod:`.export`.
    DOMAIN = "domain"


@dataclass(frozen=True, slots=True)
class ExportCoverage:
    """The export contract for one ``INCLUDED`` table."""

    domain: str
    #: Where the rows land, relative to ``payload["domains"][domain]``. A
    #: ``[*]`` segment is a list whose every element carries the rest of the
    #: path. The first path of a ``CONTRACT`` entry is its collection key.
    paths: tuple[str, ...]
    scope: Scope
    handler: Handler
    layout: Layout = Layout.FLAT
    #: ``(fk column, parent table)`` for :attr:`Scope.PARENT`.
    parent: tuple[str, str] | None = None
    #: Internal columns deliberately left out of the export: storage paths,
    #: provider bookkeeping, credentials, retry keys.
    withheld: tuple[str, ...] = ()
    #: ``(column, table)`` — a reference to another account-owned row, kept
    #: only when that row belongs to this account. ``CONTRACT`` entries only.
    references: tuple[tuple[str, str], ...] = ()
    #: ``(json column, table)`` — a JSON list of ids of another account-owned
    #: table, filtered the same way. ``CONTRACT`` entries only.
    reference_lists: tuple[tuple[str, str], ...] = ()
    #: Deterministic order for ``CONTRACT`` collections. Chronological, with
    #: the primary key as the tie-break. Never a limit.
    order_by: tuple[str, ...] = ("created_at", "id")
    #: For a deliberately reduced serialisation, what is and is not exported.
    note: str = ""

    @property
    def key(self) -> str:
        return self.paths[0]


def _domain(domain: str, paths: str | tuple[str, ...], scope: Scope, **kwargs) -> ExportCoverage:
    return ExportCoverage(
        domain=domain,
        paths=(paths,) if isinstance(paths, str) else paths,
        scope=scope,
        handler=Handler.DOMAIN,
        **kwargs,
    )


def _contract(domain: str, table: str, scope: Scope, **kwargs) -> ExportCoverage:
    """A table exported directly from this entry, under its own table name."""
    return ExportCoverage(domain=domain, paths=(table,), scope=scope, handler=Handler.CONTRACT, **kwargs)


def _child(domain: str, table: str, fk: str, parent: str, **kwargs) -> ExportCoverage:
    """A ``CONTRACT`` child row owned through its parent."""
    return _contract(domain, table, Scope.PARENT, parent=(fk, parent), **kwargs)


_ACCOUNT = Scope.ACCOUNT
_PARENT = Scope.PARENT
_GROUPED = Layout.SUBJECT_GROUPED

_PROFILE_CHILDREN = {
    "profile_attributes": "attributes",
    "profile_change_events": "change_events",
    "attribute_observations": "observations",
    "style_preferences": "style_preferences",
    "fit_preferences": "fit_preferences",
    "lifestyle_context": "lifestyle_context",
    "user_constraints": "user_constraints",
    "appearance_goals": "goals",
    "onboarding_sessions": "onboarding_sessions",
}


EXPORT_COVERAGE: dict[str, ExportCoverage] = {
    # --- identity ---------------------------------------------------------
    "accounts": _domain("identity", "id", Scope.ACCOUNT_ROW, layout=Layout.SUMMARY),
    "invite_redemptions": _domain(
        "identity", "invite_redemptions", _ACCOUNT, layout=Layout.SUMMARY,
        note="The code redeemed and when; the internal row ids are not exported.",
    ),
    # --- consent ------------------------------------------------------------
    "consents": _domain("consent", "entries", _ACCOUNT),
    # --- household ----------------------------------------------------------
    "family_circles": _domain("household", "circles", _ACCOUNT),
    "family_profiles": _domain(
        "household", "profiles", _PARENT, parent=("circle_id", "family_circles"),
    ),
    # --- profile: grouped by the human each profile describes ---------------
    "appearance_profiles": _domain(
        "profile", ("subjects[*].profile", "unattributed_profiles[*].profile"), _ACCOUNT,
        layout=_GROUPED,
    ),
    **{
        table: _domain(
            "profile", (f"subjects[*].{label}", f"unattributed_profiles[*].{label}"), _PARENT,
            parent=("profile_id", "appearance_profiles"), layout=_GROUPED,
        )
        for table, label in _PROFILE_CHILDREN.items()
    },
    # --- inventory ----------------------------------------------------------
    "inventory_items": _domain("inventory", "items", _ACCOUNT),
    "inventory_attributes": _domain(
        "inventory", ("attributes", "care_preference_history"), _PARENT,
        parent=("item_id", "inventory_items"), layout=_GROUPED,
    ),
    "inventory_events": _domain(
        "inventory", ("events", "care_preference_events"), _ACCOUNT, layout=_GROUPED,
    ),
    "supplement_details": _domain(
        "inventory", "supplement_details", _PARENT, parent=("item_id", "inventory_items"),
    ),
    "inventory_import_jobs": _domain("inventory", "import_jobs", _ACCOUNT),
    "inventory_import_candidates": _domain("inventory", "import_candidates", _ACCOUNT),
    "inventory_product_links": _domain("inventory", "product_links", _ACCOUNT),
    # Category detail rows. Some describe categories the product no longer
    # offers; the rows are still this person's history.
    "wardrobe_item_details": _child("inventory", "wardrobe_item_details", "item_id", "inventory_items"),
    "shoe_item_details": _child("inventory", "shoe_item_details", "item_id", "inventory_items"),
    "accessory_item_details": _child("inventory", "accessory_item_details", "item_id", "inventory_items"),
    "beauty_product_details": _child("inventory", "beauty_product_details", "item_id", "inventory_items"),
    "hair_product_details": _child("inventory", "hair_product_details", "item_id", "inventory_items"),
    "perfume_details": _child("inventory", "perfume_details", "item_id", "inventory_items"),
    # Which of this person's photos belong to which item. The photo is
    # referenced by media id; its storage path never leaves.
    "inventory_item_images": _child(
        "inventory", "inventory_item_images", "item_id", "inventory_items",
        references=(("media_asset_id", "media_assets"),),
    ),
    "inventory_value_events": _child("inventory", "inventory_value_events", "item_id", "inventory_items"),
    "item_condition_events": _child("inventory", "item_condition_events", "item_id", "inventory_items"),
    "item_expiry_events": _child("inventory", "item_expiry_events", "item_id", "inventory_items"),
    "item_usage_events": _child("inventory", "item_usage_events", "item_id", "inventory_items"),
    "item_relationships": _contract(
        "inventory", "item_relationships", _ACCOUNT,
        references=(("from_item_id", "inventory_items"), ("to_item_id", "inventory_items")),
    ),
    "duplicate_candidates": _contract(
        "inventory", "duplicate_candidates", _ACCOUNT,
        references=(("item_a_id", "inventory_items"), ("item_b_id", "inventory_items")),
    ),
    "laundry_state_events": _contract(
        "inventory", "laundry_state_events", _ACCOUNT,
        references=(("item_id", "inventory_items"),),
    ),
    # --- media --------------------------------------------------------------
    "media_assets": _domain(
        "media", "assets", _ACCOUNT, withheld=("storage_key", "storage_backend"),
        note="The public media fields (media.service.to_public_dict); never a storage path.",
    ),
    # --- scans + product scans ----------------------------------------------
    "scans": _domain("scans", "scans", _ACCOUNT),
    "scan_events": _domain("product_scans", "scans", _ACCOUNT),
    # The photo lives in object storage under an internal key. The export says
    # whether one was attached; the key itself never leaves.
    "label_error_reports": _domain(
        "product_scans", "label_error_reports", _ACCOUNT, withheld=("photo_key",),
        note="photo_attached says whether a photo was sent; its storage key is not exported.",
    ),
    "product_watches": _domain(
        "product_scans", "product_watches", _ACCOUNT,
        withheld=("anchor_scan_event_id", "anchor_label_snapshot_id", "notice_cursor"),
        note="Which product, since when, and when it last notified; not the internal cursor.",
    ),
    "scan_decision_events": _domain(
        "product_scans", ("subjects[*].scan_decision_events", "unattributed_scan_decision_events"),
        _ACCOUNT, layout=_GROUPED,
    ),
    # A person's own preparation for the FSSAI portal. Official FSSAI records
    # are global reference data and are not part of it.
    "fssai_complaint_handoffs": _contract(
        "product_scans", "fssai_complaint_handoffs", _ACCOUNT,
        references=(("photo_asset_id", "media_assets"),),
    ),
    # --- community ----------------------------------------------------------
    "community_observation_reports": _domain("community", "observation_reports", _ACCOUNT),
    # --- quiz + styling -----------------------------------------------------
    "quiz_submissions": _domain("quiz_and_styling", "quiz_submissions", _ACCOUNT),
    "occasions": _domain("quiz_and_styling", "occasions", _ACCOUNT),
    "style_requests": _domain("quiz_and_styling", "style_requests", _ACCOUNT),
    "recommendation_runs": _domain("quiz_and_styling", "recommendation_runs", _ACCOUNT),
    "looks": _domain("quiz_and_styling", "looks", _ACCOUNT),
    "look_adjustments": _domain("quiz_and_styling", "look_adjustments", _ACCOUNT),
    "look_feedback": _domain("quiz_and_styling", "look_feedback", _ACCOUNT),
    "recommendation_inputs": _child(
        "quiz_and_styling", "recommendation_inputs", "run_id", "recommendation_runs",
    ),
    "recommendation_entitlements": _contract("quiz_and_styling", "recommendation_entitlements", _ACCOUNT),
    "look_items": _child(
        "quiz_and_styling", "look_items", "look_id", "looks",
        references=(("inventory_item_id", "inventory_items"),),
    ),
    "outfit_schedule": _contract(
        "quiz_and_styling", "outfit_schedule", _ACCOUNT,
        references=(("look_id", "looks"),),
        reference_lists=(("item_ids", "inventory_items"),),
    ),
    "compatibility_edges": _contract(
        "quiz_and_styling", "compatibility_edges", _ACCOUNT,
        references=(("item_a_id", "inventory_items"), ("item_b_id", "inventory_items")),
    ),
    # --- shopping: candidates account-wide, decisions grouped by human -------
    "shopping_candidates": _domain("shopping", "candidates", _ACCOUNT),
    "purchase_evaluations": _domain("shopping", "evaluations", _ACCOUNT),
    "purchase_decisions": _domain(
        "shopping", ("subjects[*].decisions", "unattributed_decisions"), _ACCOUNT, layout=_GROUPED,
    ),
    "purchase_decision_events": _domain(
        "shopping", ("subjects[*].decision_events", "unattributed_decision_events"), _ACCOUNT,
        layout=_GROUPED,
    ),
    "purchase_evaluation_factors": _child(
        "shopping", "purchase_evaluation_factors", "evaluation_id", "purchase_evaluations",
    ),
    # --- planning -----------------------------------------------------------
    "daily_plans": _domain("planning", "daily_plans", _ACCOUNT),
    "weekly_plans": _domain("planning", "weekly_plans", _ACCOUNT),
    "calendar_events": _domain("planning", "calendar_events", _ACCOUNT),
    "external_integrations": _domain(
        "planning", "calendar_integrations", _ACCOUNT,
        withheld=("credential_ref", "sync_cursor"),
        note="Connection health only; credentials and the provider sync cursor are not exported.",
    ),
    "weather_snapshots": _domain("planning", "weather_snapshots", _ACCOUNT),
    "event_ready_plans": _domain("planning", "event_ready_plans", _ACCOUNT),
    "event_ready_actions": _domain(
        "planning", "event_ready_actions", _PARENT, parent=("event_ready_plan_id", "event_ready_plans"),
    ),
    "notification_preferences": _domain("planning", "notification_preferences", _ACCOUNT),
    "notification_deliveries": _domain(
        "planning", "notification_deliveries", _ACCOUNT,
        withheld=("dedup_hash", "provider_ticket_id", "provider_error_code", "claimed_at", "claim_token"),
        note="Delivery status metadata only, as before this contract; provider internals are not exported.",
    ),
    "daily_plan_actions": _child(
        "planning", "daily_plan_actions", "plan_id", "daily_plans",
        references=(("inventory_item_id", "inventory_items"),),
    ),
    "daily_plan_inputs": _child("planning", "daily_plan_inputs", "plan_id", "daily_plans"),
    "weekly_plan_days": _child(
        "planning", "weekly_plan_days", "weekly_plan_id", "weekly_plans",
        references=(("daily_plan_id", "daily_plans"),),
    ),
    "air_quality_snapshots": _contract("planning", "air_quality_snapshots", _ACCOUNT),
    "plan_recalculation_events": _contract("planning", "plan_recalculation_events", _ACCOUNT),
    # --- routines + Care ----------------------------------------------------
    "care_product_preferences": _domain(
        "routines", ("care_product_preferences_by_subject", "unattributed_care_product_preferences"),
        _ACCOUNT, layout=_GROUPED,
    ),
    "routines": _domain("routines", "routine_history", _ACCOUNT, layout=_GROUPED),
    "routine_steps": _domain(
        "routines", "routine_history", _PARENT, parent=("routine_id", "routines"), layout=_GROUPED,
    ),
    "routine_adherence": _domain("routines", "routine_history", _ACCOUNT, layout=_GROUPED),
    "routine_recommendation_runs": _domain("routines", "routine_history", _ACCOUNT, layout=_GROUPED),
    "shelf_manager_decision_events": _domain("routines", "manager_history", _ACCOUNT, layout=_GROUPED),
    "product_ingredients": _domain("routines", "product_ingredients", _ACCOUNT),
    "user_reported_observations": _domain("routines", "observations", _ACCOUNT),
    "product_expiry_events": _domain("routines", "product_expiry_events", _ACCOUNT),
    "supplement_safety_flags": _domain("routines", "supplement_safety_flags", _ACCOUNT),
    "supplement_label_components": _domain(
        "routines", "supplement_label_components", _ACCOUNT,
        withheld=("account_id", "source_ai_run_id", "model_version", "prompt_version", "client_mutation_id"),
        note="What the label said and its provenance; pipeline bookkeeping is not exported.",
    ),
    "nutrition_preferences": _domain("routines", "nutrition_preferences", _ACCOUNT),
    "hydration_preferences": _domain("routines", "hydration_preferences", _ACCOUNT),
    "care_experience_feedback": _domain("routines", "experience_feedback", _ACCOUNT),
    "maintenance_preferences": _domain("routines", "maintenance_preferences", _ACCOUNT),
    "maintenance_events": _domain("routines", "maintenance_events", _ACCOUNT),
    # --- progress + memory --------------------------------------------------
    "metric_events": _domain("progress_and_memory", "metric_events", _ACCOUNT),
    "progress_goals": _domain("progress_and_memory", "goals", _ACCOUNT),
    "milestones": _domain("progress_and_memory", "milestones", _ACCOUNT),
    "progress_photos": _domain("progress_and_memory", "photos", _ACCOUNT),
    "memory_facts": _domain("progress_and_memory", "memory_facts", _ACCOUNT),
    "memory_revisions": _domain(
        "progress_and_memory", "memory_revisions", _PARENT, parent=("fact_id", "memory_facts"),
    ),
    "memory_sources": _domain(
        "progress_and_memory", "memory_sources", _PARENT, parent=("fact_id", "memory_facts"),
    ),
    "feedback_events": _domain("progress_and_memory", "feedback_events", _ACCOUNT),
    "gamification_events": _domain("progress_and_memory", "behaviour_events", _ACCOUNT),
    "goal_updates": _contract(
        "progress_and_memory", "goal_updates", _ACCOUNT,
        references=(("goal_id", "progress_goals"),),
    ),
    "progress_snapshots": _contract("progress_and_memory", "progress_snapshots", _ACCOUNT),
    "comparison_sessions": _contract(
        "progress_and_memory", "comparison_sessions", _ACCOUNT,
        references=(("baseline_media_id", "media_assets"), ("current_media_id", "media_assets")),
    ),
    "score_explanations": _contract(
        "progress_and_memory", "score_explanations", _ACCOUNT,
        references=(("metric_event_id", "metric_events"),),
    ),
    "streaks": _contract("progress_and_memory", "streaks", _ACCOUNT),
    "memory_category_preferences": _contract("progress_and_memory", "memory_category_preferences", _ACCOUNT),
    # --- AI + operations ----------------------------------------------------
    "ai_runs": _domain("ai_and_ops", "ai_runs", _ACCOUNT),
    "ai_run_outputs": _domain(
        "ai_and_ops", "ai_run_outputs", _PARENT, parent=("ai_run_id", "ai_runs"),
    ),
    "audit_events": _domain("ai_and_ops", "audit_events", _ACCOUNT),
    "beta_usage_events": _domain("ai_and_ops", "beta_usage_events", _ACCOUNT),
    # Product analytics this account generated. A row whose account was
    # detached (``SET NULL``) belongs to nobody and is in nobody's export.
    "app_events": _contract("ai_and_ops", "app_events", _ACCOUNT),
}


def coverage_drift() -> tuple[set[str], set[str]]:
    """``(INCLUDED but not covered, covered but not INCLUDED)``. Both empty when sound."""
    from app.domains.privacy import included_tables

    included = included_tables()
    covered = set(EXPORT_COVERAGE)
    return included - covered, covered - included


def contract_tables(domain: str) -> list[str]:
    """The tables a domain exports straight from the contract, in a fixed order."""
    return sorted(
        table for table, entry in EXPORT_COVERAGE.items()
        if entry.domain == domain and entry.handler == Handler.CONTRACT
    )


def export_locations() -> dict[str, list[str]]:
    """Where each exported table's rows are, as ``domain.path`` strings."""
    return {
        table: [f"{entry.domain}.{path}" for path in entry.paths]
        for table, entry in sorted(EXPORT_COVERAGE.items())
    }
