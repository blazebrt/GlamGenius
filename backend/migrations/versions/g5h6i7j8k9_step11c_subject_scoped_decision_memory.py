"""subject-scoped decision memory for Step 11C

Revision ID: g5h6i7j8k9
Revises: f4g5h6i7j8

Decision Memory learns whose decision it was. Three tables gain a nullable
``household_subject_id``; nothing is backfilled, because nothing could be
backfilled truthfully.

Every existing row keeps NULL, and NULL keeps meaning exactly what it always
meant: this was written before the question was asked. Whether such a row can
honestly be read as the account holder's depends on that account's own
``FamilyCircle`` timing, which the application decides at read time — see
``app/domains/family/decision_subject.py``. A migration cannot make that
judgement, because it would have to make the same one for every account.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g5h6i7j8k9"
down_revision = "f4g5h6i7j8"
branch_labels = None
depends_on = None

SUBJECT_TABLES = ("scan_decision_events", "purchase_decisions", "purchase_decision_events")

LEGACY_UNIQUE = "uq_purchase_decision_candidate_strategy"
SUBJECT_UNIQUE = "uq_purchase_decision_subject_candidate_strategy"


def _subject_column(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "household_subject_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        f"fk_{table}_household_subject",
        table,
        "family_profiles",
        ["household_subject_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )


def upgrade() -> None:
    for table in SUBJECT_TABLES:
        _subject_column(table)

    # The old authority said "one current decision per account per candidate
    # per strategy". With a household that sentence is false, and replacing it
    # takes two partial indexes rather than one: PostgreSQL treats NULLs as
    # distinct, so a single index including the subject column would let an
    # account accumulate any number of subject-less rows for one candidate —
    # exactly the row adoption depends on there being only one of.
    op.drop_index(LEGACY_UNIQUE, table_name="purchase_decisions")
    op.create_index(
        LEGACY_UNIQUE,
        "purchase_decisions",
        ["account_id", "candidate_id", "strategy_key"],
        unique=True,
        postgresql_where=sa.text(
            "evaluation_id IS NULL AND household_subject_id IS NULL"
        ),
    )
    op.create_index(
        SUBJECT_UNIQUE,
        "purchase_decisions",
        ["account_id", "household_subject_id", "candidate_id", "strategy_key"],
        unique=True,
        postgresql_where=sa.text(
            "evaluation_id IS NULL AND household_subject_id IS NOT NULL"
        ),
    )

    # ``uq_purchase_decision_once`` is untouched: the historical Style rows it
    # governs are keyed by evaluation, are not subject-scoped by this slice,
    # and nothing here gives them a new surface.

    op.create_index(
        "ix_purchase_decisions_subject_candidate_updated",
        "purchase_decisions",
        ["account_id", "household_subject_id", "candidate_id", "updated_at"],
    )
    op.create_index(
        "ix_purchase_decision_events_subject_created",
        "purchase_decision_events",
        ["account_id", "household_subject_id", "created_at"],
    )
    op.create_index(
        "ix_purchase_decision_events_subject_identity_created",
        "purchase_decision_events",
        ["account_id", "household_subject_id", "identity_fingerprint", "created_at"],
    )
    op.create_index(
        "ix_scan_decision_events_subject_barcode_created",
        "scan_decision_events",
        ["account_id", "household_subject_id", "barcode", "created_at"],
    )


def downgrade() -> None:
    """Reverse the schema, or refuse honestly.

    This one is reversible for exactly as long as nobody has used it. The
    moment a decision is stored against a named human, dropping the column
    erases *which person made it* — and unlike a constraint, that is not
    something a later migration can reconstruct. The rows would survive looking
    complete and be quietly wrong, which is worse than an error.

    So it checks twice. First for any attributed row at all, which is the real
    loss. Then for the narrower mechanical problem: restoring the old
    account-wide uniqueness needs at most one subject-less current row per
    account, candidate and strategy, and an account that adopted nothing while
    writing subject-bound rows could have more.

    Neither is resolved here. Nulling the subject would erase the answer,
    merging would invent one, and deleting would throw away a customer's
    history — so it stops and says which accounts to look at.
    """
    connection = op.get_bind()

    attributed = connection.execute(
        sa.text(
            """
            SELECT 'scan_decision_events' AS source, count(*) AS rows
            FROM scan_decision_events WHERE household_subject_id IS NOT NULL
            UNION ALL
            SELECT 'purchase_decisions', count(*)
            FROM purchase_decisions WHERE household_subject_id IS NOT NULL
            UNION ALL
            SELECT 'purchase_decision_events', count(*)
            FROM purchase_decision_events WHERE household_subject_id IS NOT NULL
            """
        )
    ).all()
    populated = [(row.source, row.rows) for row in attributed if row.rows]
    if populated:
        detail = ", ".join(f"{source} ({rows} rows)" for source, rows in populated)
        raise RuntimeError(
            "Cannot downgrade g5h6i7j8k9: decision memory is already attributed "
            "to named household members, and dropping the column would erase "
            "which person made each decision — not a constraint that can be "
            "rebuilt, but the answer itself. Affected: "
            f"{detail}. "
            "Recover with a forward corrective migration that deliberately "
            "decides what each subject's history becomes, not with this "
            "downgrade."
        )

    duplicates = connection.execute(
        sa.text(
            """
            SELECT account_id, candidate_id, strategy_key, count(*) AS rows
            FROM purchase_decisions
            WHERE evaluation_id IS NULL
            GROUP BY account_id, candidate_id, strategy_key
            HAVING count(*) > 1
            ORDER BY rows DESC
            LIMIT 5
            """
        )
    ).all()
    if duplicates:
        accounts = ", ".join(
            f"{row.account_id} (candidate {row.candidate_id}, {row.rows} current rows)"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot downgrade g5h6i7j8k9: restoring the account-wide unique "
            "index would require deleting or merging current purchase "
            "decisions that belong to different people. This migration will "
            "not choose whose decision to discard. Affected: "
            f"{accounts}. "
            "Recover with a forward corrective migration."
        )

    op.drop_index("ix_scan_decision_events_subject_barcode_created", table_name="scan_decision_events")
    op.drop_index("ix_purchase_decision_events_subject_identity_created", table_name="purchase_decision_events")
    op.drop_index("ix_purchase_decision_events_subject_created", table_name="purchase_decision_events")
    op.drop_index("ix_purchase_decisions_subject_candidate_updated", table_name="purchase_decisions")

    op.drop_index(SUBJECT_UNIQUE, table_name="purchase_decisions")
    op.drop_index(LEGACY_UNIQUE, table_name="purchase_decisions")
    op.create_index(
        LEGACY_UNIQUE,
        "purchase_decisions",
        ["account_id", "candidate_id", "strategy_key"],
        unique=True,
        postgresql_where=sa.text("evaluation_id IS NULL"),
    )

    for table in reversed(SUBJECT_TABLES):
        op.drop_constraint(f"fk_{table}_household_subject", table, type_="foreignkey")
        op.drop_column(table, "household_subject_id")
