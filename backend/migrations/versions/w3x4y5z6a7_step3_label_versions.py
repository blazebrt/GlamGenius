"""Add deterministic observed label version identity."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "w3x4y5z6a7"
down_revision: Union[str, None] = "v2w3x4y5z6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("product_label_snapshots", sa.Column("content_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("product_label_snapshots", sa.Column("version_number", sa.Integer(), server_default="1", nullable=False))
    op.add_column("product_label_snapshots", sa.Column("previous_snapshot_id", sa.Uuid(), nullable=True))
    op.add_column(
        "product_label_snapshots",
        sa.Column(
            "changed_fields",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("product_label_snapshots", sa.Column("completeness", sa.String(length=32), server_default="incomplete_for_grading", nullable=False))
    op.execute("UPDATE product_label_snapshots SET content_fingerprint = md5(facts::text) WHERE content_fingerprint IS NULL")
    op.alter_column("product_label_snapshots", "content_fingerprint", nullable=False)
    op.create_foreign_key("fk_product_label_snapshots_previous", "product_label_snapshots", "product_label_snapshots", ["previous_snapshot_id"], ["id"], ondelete="SET NULL")
    op.create_check_constraint("ck_product_label_snapshots_completeness", "product_label_snapshots", "completeness IN ('complete_for_grading', 'incomplete_for_grading', 'identity_only')")
    op.create_unique_constraint("uq_product_label_snapshots_content", "product_label_snapshots", ["barcode", "content_fingerprint"])
    op.create_unique_constraint("uq_product_label_snapshots_version", "product_label_snapshots", ["barcode", "version_number"])

def downgrade() -> None:
    op.drop_constraint("uq_product_label_snapshots_version", "product_label_snapshots", type_="unique")
    op.drop_constraint("uq_product_label_snapshots_content", "product_label_snapshots", type_="unique")
    op.drop_constraint("ck_product_label_snapshots_completeness", "product_label_snapshots", type_="check")
    op.drop_constraint("fk_product_label_snapshots_previous", "product_label_snapshots", type_="foreignkey")
    for name in ("completeness", "changed_fields", "previous_snapshot_id", "version_number", "content_fingerprint"):
        op.drop_column("product_label_snapshots", name)
