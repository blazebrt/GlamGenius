"""Alembic revision — idempotency key for shopping evaluations.

``ShoppingEvaluateRequest`` already accepted a ``client_mutation_id``, but
nothing stored it, so a retry on a flaky mobile connection created a second
candidate and consumed a second run from the account's beta allowance. This
adds the column the replay check needs, with a unique constraint per account so
two concurrent retries cannot both win.

Revision ID: 0005_shopping_idempotency
Revises: 0004_reference_catalogue
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_shopping_idempotency"
down_revision: Union[str, None] = "0004_reference_catalogue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shopping_candidates",
        sa.Column("client_mutation_id", sa.String(length=80), nullable=True),
    )
    op.create_unique_constraint(
        "uq_shopping_candidate_client_mutation",
        "shopping_candidates",
        ["account_id", "client_mutation_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_shopping_candidate_client_mutation",
        "shopping_candidates",
        type_="unique",
    )
    op.drop_column("shopping_candidates", "client_mutation_id")
