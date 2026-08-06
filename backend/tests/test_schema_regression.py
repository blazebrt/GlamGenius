"""Migration & schema regression assertions.

The Supabase cutover requires the schema to be free of payment tables and
of the ``account_links`` bridge, and every user-scoped table must reference
the canonical ``accounts.id`` (Supabase UUID) column.
"""
from __future__ import annotations

import pytest
from app.shared.database.registry import Base
from app.shared.database.sql import get_engine
from sqlalchemy import text

# Payment/subscription/refund/plan-adjacent table names that must never appear.
FORBIDDEN_TABLE_SUBSTRINGS = (
    "subscription",
    "razorpay",
    "refund",
    "checkout",
    "invoice",
    "paywall",
    "plan_prices",
    "event_pass",
    "billing_event",
    "billing_webhook",
    "orders",
    "payment",
)


def test_metadata_contains_no_payment_tables():
    names = list(Base.metadata.tables)
    offenders = [
        n
        for n in names
        if any(sub in n.lower() for sub in FORBIDDEN_TABLE_SUBSTRINGS)
    ]
    assert offenders == [], f"forbidden payment tables present: {offenders}"


def test_metadata_contains_no_account_links_bridge():
    assert "account_links" not in Base.metadata.tables


def test_accounts_table_has_uuid_primary_key():
    accounts = Base.metadata.tables["accounts"]
    pk_columns = list(accounts.primary_key.columns)
    assert len(pk_columns) == 1
    assert pk_columns[0].name == "id"
    # SQLAlchemy renders PgUUID as sa.UUID here; both count.
    assert "UUID" in str(pk_columns[0].type).upper()


def test_every_user_scoped_fk_targets_accounts_id():
    """Every ``account_id`` column must FK to ``accounts.id`` — never
    ``account_links.id`` or a bare UUID with no ownership constraint.

    ``account_deletion_jobs`` is deliberately exempt: the deletion state
    machine must survive the account row being deleted (the tombstone), so
    that table stores ``account_id`` without an FK. See
    ``app/domains/privacy/models.py``.
    """
    # Tables that intentionally hold ``account_id`` without an FK.
    NO_FK_EXEMPT = {"account_deletion_jobs"}
    problems: list[str] = []
    for tname, table in Base.metadata.tables.items():
        col = table.columns.get("account_id")
        if col is None:
            continue
        fks = list(col.foreign_keys)
        if not fks:
            if tname in NO_FK_EXEMPT:
                continue
            problems.append(f"{tname}.account_id has no foreign key")
            continue
        targets = {fk.target_fullname for fk in fks}
        if targets != {"accounts.id"}:
            problems.append(f"{tname}.account_id points to {targets}, not accounts.id")
    assert problems == [], problems


@pytest.mark.asyncio
async def test_empty_database_upgrades_and_survives_a_roundtrip(db_clean):
    """The single initial migration must build the whole schema, then
    downgrade to base and re-upgrade without drift."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "ORDER BY tablename"
            )
        )
        live_tables = {r[0] for r in rows}

    expected = {t for t in Base.metadata.tables}
    # Alembic's own version table lives in the same schema.
    live_tables.discard("alembic_version")
    missing = expected - live_tables
    extra = live_tables - expected
    assert not missing, f"tables missing from live db: {sorted(missing)}"
    assert not extra, f"unexpected tables in live db: {sorted(extra)}"
