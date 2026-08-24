"""Widen integration status for reconnect-required lifecycle state."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("external_integrations", "status", type_=sa.String(length=32), existing_type=sa.String(length=16), existing_nullable=False)


def downgrade() -> None:
    # Older schemas cannot represent lifecycle states longer than 16 chars.
    # Normalize the explicitly introduced lifecycle states before narrowing.
    # The previous schema has no representation for these states; connected is
    # the only valid legacy value that preserves a recoverable integration.
    op.execute("""
        UPDATE external_integrations
        SET status = CASE status
            WHEN 'revocation_pending' THEN 'connected'
            WHEN 'reconnect_required' THEN 'connected'
            WHEN 'temporary_failure' THEN 'connected'
            ELSE status
        END
        WHERE length(status) > 16
    """)
    op.alter_column("external_integrations", "status", type_=sa.String(length=16), existing_type=sa.String(length=32), existing_nullable=False)
