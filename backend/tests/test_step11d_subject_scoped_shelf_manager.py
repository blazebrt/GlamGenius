"""Step 11D contract tests that do not require a live database.

PostgreSQL-backed household, migration and route tests run in canonical CI;
these checks keep the schema and governed policy visible even on a workstation
without PostgreSQL.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.domains.care.models import CareProductPreference
from app.domains.care.subject_preferences import PreferenceCoverage
from app.domains.inventory.models import InventoryEvent
from app.domains.routines.models import ShelfManagerDecisionEvent


def test_preference_schema_is_subject_owned_and_closed_list() -> None:
    columns = CareProductPreference.__table__.c
    assert columns.household_subject_id.nullable is False
    assert columns.account_id.nullable is False
    assert columns.inventory_item_id.nullable is False
    checks = {
        constraint.name: str(getattr(constraint, "sqltext", ""))
        for constraint in CareProductPreference.__table__.constraints if constraint.name
    }
    assert "ck_care_product_preference_kind" in checks
    assert "ck_care_product_preference_source" in checks
    assert "uq_care_product_preference_subject_item_kind" in checks


def test_audit_and_manager_events_have_nullable_subject_without_changing_physical_ownership() -> None:
    assert InventoryEvent.__table__.c.account_id.nullable is False
    assert InventoryEvent.__table__.c.household_subject_id.nullable is True
    assert ShelfManagerDecisionEvent.__table__.c.account_id.nullable is False
    assert ShelfManagerDecisionEvent.__table__.c.household_subject_id.nullable is True


def test_ambiguous_legacy_preference_coverage_is_fail_closed() -> None:
    coverage = PreferenceCoverage(unattributed_legacy_preferences_present=True)
    assert coverage.complete_for_subject is False
    assert coverage.as_dict() == {
        "unattributed_legacy_preferences_present": True,
        "complete_for_subject": False,
    }


def test_step11d_migration_is_linear_and_refuses_populated_downgrade() -> None:
    path = Path(__file__).parents[1] / "migrations" / "versions" / "h6i7j8k9l0_step11d_subject_scoped_care_preferences.py"
    spec = importlib.util.spec_from_file_location("step11d_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "h6i7j8k9l0"
    assert module.down_revision == "g5h6i7j8k9"
    source = path.read_text(encoding="utf-8")
    assert "Cannot downgrade h6i7j8k9l0" in source
    assert "household_subject_id IS NOT NULL" in source
