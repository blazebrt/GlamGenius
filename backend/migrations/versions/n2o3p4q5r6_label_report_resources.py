"""Exact label report photo size, preserving unknown legacy accounting.

Revision ID: n2o3p4q5r6
Revises: m1n2o3p4q5
"""
import sqlalchemy as sa
from alembic import op

revision = "n2o3p4q5r6"
down_revision = "m1n2o3p4q5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL is deliberately not backfilled to zero: old objects were not sized.
    op.add_column("label_error_reports", sa.Column("photo_byte_size", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_label_error_reports_photo_size", "label_error_reports", "photo_byte_size IS NULL OR photo_byte_size BETWEEN 0 AND 6291456")


def downgrade() -> None:
    op.drop_constraint("ck_label_error_reports_photo_size", "label_error_reports", type_="check")
    op.drop_column("label_error_reports", "photo_byte_size")
