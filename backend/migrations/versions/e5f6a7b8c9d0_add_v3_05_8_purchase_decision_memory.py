"""Unify Style and Care purchase decision memory.

Revision ID: e5f6a7b8c9d0
Revises: d5e6f7a8b9c0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("purchase_decisions", sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("purchase_decisions", sa.Column("strategy_key", sa.String(length=32), nullable=True))
    op.add_column("purchase_decisions", sa.Column("recommendation_verdict", sa.String(length=8), nullable=True))
    op.add_column("purchase_decisions", sa.Column("recommendation_version", sa.String(length=32), nullable=True))
    op.add_column("purchase_decisions", sa.Column("recommendation_fingerprint", sa.String(length=128), nullable=True))
    op.add_column(
        "purchase_decisions",
        sa.Column(
            "recommendation_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=True,
        ),
    )
    op.alter_column("purchase_decisions", "evaluation_id", nullable=True)

    op.execute(
        sa.text(
            """
            UPDATE purchase_decisions AS pd
            SET candidate_id = pe.candidate_id,
                strategy_key = 'style_purchase',
                recommendation_verdict = pe.verdict,
                recommendation_version = pe.roi_version,
                recommendation_snapshot = jsonb_build_object(
                    'strategy', 'style_purchase',
                    'evaluation_id', pe.id::text,
                    'verdict', pe.verdict,
                    'roi_version', pe.roi_version,
                    'roi_score', pe.roi_score
                )
            FROM purchase_evaluations AS pe
            WHERE pd.evaluation_id = pe.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM purchase_decisions
                    WHERE candidate_id IS NULL
                       OR strategy_key IS NULL
                       OR recommendation_verdict IS NULL
                       OR recommendation_version IS NULL
                       OR recommendation_snapshot IS NULL
                ) THEN
                    RAISE EXCEPTION 'purchase_decisions backfill left incomplete provenance';
                END IF;
            END $$;
            """
        )
    )
    op.alter_column("purchase_decisions", "candidate_id", nullable=False)
    op.alter_column("purchase_decisions", "strategy_key", nullable=False)
    op.alter_column("purchase_decisions", "recommendation_verdict", nullable=False)
    op.alter_column("purchase_decisions", "recommendation_version", nullable=False)
    op.alter_column("purchase_decisions", "recommendation_snapshot", nullable=False)
    op.create_foreign_key(
        "fk_purchase_decisions_candidate_id",
        "purchase_decisions",
        "shopping_candidates",
        ["candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_purchase_decisions_account_candidate_updated",
        "purchase_decisions",
        ["account_id", "candidate_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "uq_purchase_decision_candidate_strategy",
        "purchase_decisions",
        ["account_id", "candidate_id", "strategy_key"],
        unique=True,
        postgresql_where=sa.text("evaluation_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_purchase_decision_candidate_strategy", table_name="purchase_decisions")
    op.drop_index("ix_purchase_decisions_account_candidate_updated", table_name="purchase_decisions")
    op.drop_constraint("fk_purchase_decisions_candidate_id", "purchase_decisions", type_="foreignkey")
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM purchase_decisions WHERE evaluation_id IS NULL) THEN
                    RAISE EXCEPTION 'cannot downgrade purchase decision memory while Care decisions exist';
                END IF;
            END $$;
            """
        )
    )
    op.alter_column("purchase_decisions", "evaluation_id", nullable=False)
    op.drop_column("purchase_decisions", "recommendation_snapshot")
    op.drop_column("purchase_decisions", "recommendation_fingerprint")
    op.drop_column("purchase_decisions", "recommendation_version")
    op.drop_column("purchase_decisions", "recommendation_verdict")
    op.drop_column("purchase_decisions", "strategy_key")
    op.drop_column("purchase_decisions", "candidate_id")
