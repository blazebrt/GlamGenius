"""Add account-owned structured supplement label facts.

Revision ID: k1l2m3n4o5p6
Revises: j0e1f2a3b4c5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "supplement_label_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=160), nullable=False),
        sa.Column("canonical_component_key", sa.String(length=160), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("serving_text", sa.String(length=160), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="user_declared", nullable=False),
        sa.Column("verification_state", sa.String(length=24), server_default="confirmed", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_ai_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("schema_version", sa.String(length=32), server_default="vc-07-v1", nullable=False),
        sa.Column("client_mutation_id", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "client_mutation_id", name="uq_supplement_label_client_mutation"),
    )
    op.create_index("ix_supplement_label_account_item", "supplement_label_components", ["account_id", "item_id"])
    op.create_index("ix_supplement_label_overlap", "supplement_label_components", ["account_id", "canonical_component_key", "verification_state"])


def downgrade() -> None:
    op.drop_index("ix_supplement_label_overlap", table_name="supplement_label_components")
    op.drop_index("ix_supplement_label_account_item", table_name="supplement_label_components")
    op.drop_table("supplement_label_components")
