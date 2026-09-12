"""Every account_id foreign key has an index behind it.

Two operations read these columns and only these columns: the privacy export,
which filters every table by account, and account deletion, where PostgreSQL
has to find the child rows to cascade. Eleven tables had no index, so both read
the whole table.

Measured on 200,000 rows across 1,000 accounts, fetching one account's rows:
a sequential scan of 2,859 pages in 18.07 ms became a bitmap index scan of 203
pages in 1.07 ms. Per table, on every export and every deletion — one of which
somebody is waiting for and the other of which has a legal clock on it.

The test is written against the ORM metadata rather than a list of table names,
so a table added later is covered the day it exists.
"""
from __future__ import annotations

from app.shared.database import registry  # noqa: F401 - imports every model
from app.shared.database.registry import Base


def _indexed_columns(table) -> set[str]:
    """Every column an index or constraint can serve a lookup on."""
    covered: set[str] = set()
    for index in table.indexes:
        names = [column.name for column in index.columns]
        if names:
            # Only the leading column of a composite index serves an equality
            # lookup on its own.
            covered.add(names[0])
    for column in table.columns:
        if column.primary_key or column.unique or column.index:
            covered.add(column.name)
    for constraint in table.constraints:
        names = [column.name for column in getattr(constraint, "columns", [])]
        if names and constraint.__class__.__name__ in {
            "UniqueConstraint",
            "PrimaryKeyConstraint",
        }:
            covered.add(names[0])
    return covered


def test_every_account_id_foreign_key_is_indexed():
    unindexed = []
    for table in Base.metadata.sorted_tables:
        covered = _indexed_columns(table)
        for column in table.columns:
            if column.name != "account_id" or not column.foreign_keys:
                continue
            if column.name not in covered:
                unindexed.append(f"{table.name}.{column.name}")

    assert not unindexed, (
        "these tables are filtered by account on every export and scanned by "
        "every account deletion, with no index to do it with: "
        f"{sorted(unindexed)}"
    )


def test_the_account_id_indexes_are_declared_on_the_models_not_only_in_a_migration():
    """A migration alone would drift: ``alembic check`` compares the database
    against this metadata, so an index the models do not declare reads as
    something to drop."""
    declared = {
        table.name
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name == "account_id" and column.foreign_keys and column.index
    }
    expected = {
        "maintenance_preferences", "memory_category_preferences", "streaks",
        "fssai_complaint_handoffs", "inventory_import_jobs", "label_error_reports",
        "scan_events", "score_explanations", "item_relationships", "goal_updates",
        "look_adjustments",
    }
    assert expected <= declared, f"no longer declared: {sorted(expected - declared)}"
