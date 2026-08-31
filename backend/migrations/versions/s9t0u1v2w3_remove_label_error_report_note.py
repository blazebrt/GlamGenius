"""Remove unstructured user text from anonymous label-error reports.

Reports are intentionally structured: reason, optional validated pack photo,
and system metadata.  Free text is neither needed for the correction workflow
nor safe to accept from an anonymous endpoint.

Revision ID: s9t0u1v2w3
Revises: r8s9t0u1v2
"""
from typing import Sequence, Union

from alembic import op

revision: str = "s9t0u1v2w3"
down_revision: Union[str, None] = "r8s9t0u1v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("label_error_reports", "note")


def downgrade() -> None:
    import sqlalchemy as sa
    op.add_column("label_error_reports", sa.Column("note", sa.Text(), nullable=True))
