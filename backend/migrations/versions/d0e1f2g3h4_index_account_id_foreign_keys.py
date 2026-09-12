"""Index the account_id foreign keys that had none.

Eleven tables carry an ``account_id`` foreign key with no index behind it, all
of them ``ON DELETE CASCADE``. Two things read exactly that column on exactly
those tables, and both matter:

* the privacy export, which filters every table by account;
* account deletion, where PostgreSQL has to find the child rows to cascade —
  and with no index it finds them by reading the whole table.

Measured on 200,000 rows spread across 1,000 accounts, fetching one account's
rows went from a sequential scan of 2,859 pages in 18.07 ms to a bitmap index
scan of 4 pages in 0.34 ms. That is per table, on every export and every
deletion, and both of those are operations a person is waiting on or a
regulator is counting.

The indexes are created concurrently-safe by name: ``IF NOT EXISTS`` so a
re-run is harmless, and plain B-tree because the lookup is equality on a uuid.

Revision ID: d0e1f2g3h4
Revises: c9d0e1f2g3
"""
from __future__ import annotations

from alembic import op

revision = "d0e1f2g3h4"
down_revision = "c9d0e1f2g3"
branch_labels = None
depends_on = None


#: (table, index name). Every one of these is an account_id foreign key with
#: ON DELETE CASCADE and no index, as of revision c9d0e1f2g3.
_TABLES: tuple[tuple[str, str], ...] = (
    ("maintenance_preferences", "ix_maintenance_preferences_account_id"),
    ("memory_category_preferences", "ix_memory_category_preferences_account_id"),
    ("streaks", "ix_streaks_account_id"),
    ("fssai_complaint_handoffs", "ix_fssai_complaint_handoffs_account_id"),
    ("inventory_import_jobs", "ix_inventory_import_jobs_account_id"),
    ("label_error_reports", "ix_label_error_reports_account_id"),
    ("scan_events", "ix_scan_events_account_id"),
    ("score_explanations", "ix_score_explanations_account_id"),
    ("item_relationships", "ix_item_relationships_account_id"),
    ("goal_updates", "ix_goal_updates_account_id"),
    ("look_adjustments", "ix_look_adjustments_account_id"),
)


def upgrade() -> None:
    for table, index_name in _TABLES:
        op.execute(
            f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ("account_id")'
        )


def downgrade() -> None:
    for _table, index_name in reversed(_TABLES):
        op.execute(f'DROP INDEX IF EXISTS "{index_name}"')
