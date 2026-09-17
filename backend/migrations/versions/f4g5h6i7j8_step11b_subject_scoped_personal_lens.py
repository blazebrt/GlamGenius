"""subject-scoped personal lens for Step 11B

Revision ID: f4g5h6i7j8
Revises: e3f4g5h6i7

One account stopped meaning one person when households arrived. This lets an
``appearance_profiles`` row belong to a named household subject, and replaces
the single ``UNIQUE(account_id)`` with the two authorities that actually
describe the invariant.

No data is backfilled and no profile is adopted here. Every existing row keeps
``household_subject_id = NULL``, which is precisely what it already meant: the
account holder's profile, recorded before this account had a household.
Adoption happens later, once, on that account's next qualifying write — never
in a migration, because a migration cannot know which rows an operator would
want touched.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4g5h6i7j8"
down_revision = "e3f4g5h6i7"
branch_labels = None
depends_on = None


LEGACY_INDEX = "uq_appearance_profile_account_legacy"
SUBJECT_INDEX = "uq_appearance_profile_household_subject"
FK_NAME = "fk_appearance_profile_household_subject"


def upgrade() -> None:
    op.add_column(
        "appearance_profiles",
        sa.Column("household_subject_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        FK_NAME,
        "appearance_profiles",
        "family_profiles",
        ["household_subject_id"],
        ["id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )

    # The old authority said "one profile per account". That is the sentence a
    # household makes false, so it goes.
    op.drop_constraint("appearance_profiles_account_id_key", "appearance_profiles", type_="unique")

    # Two partial authorities replace it. They are partial on purpose: a plain
    # UNIQUE over (account_id, household_subject_id) does not enforce this,
    # because PostgreSQL treats NULLs as distinct and would accept two
    # NULL-subject rows for one account.
    op.create_index(
        LEGACY_INDEX,
        "appearance_profiles",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("household_subject_id IS NULL"),
    )
    op.create_index(
        SUBJECT_INDEX,
        "appearance_profiles",
        ["household_subject_id"],
        unique=True,
        postgresql_where=sa.text("household_subject_id IS NOT NULL"),
    )
    # No ordinary index on household_subject_id: the partial unique index above
    # already provides the B-tree access path for the only lookup that matters.


def downgrade() -> None:
    """Reverse the schema, or refuse honestly.

    This is reversible right up until the moment a second person exists. After
    that it is not, and saying otherwise would be the dangerous kind of wrong:
    restoring ``UNIQUE(account_id)`` with two profiles under one account means
    deleting or merging a real human's body facts, and a migration must never
    make that choice on somebody's behalf.

    So it checks first and stops with an explanation, rather than letting
    PostgreSQL raise a bare unique violation halfway through and leaving an
    operator to guess what happened.
    """
    connection = op.get_bind()
    offending = connection.execute(
        sa.text(
            """
            SELECT account_id, count(*) AS profiles
            FROM appearance_profiles
            GROUP BY account_id
            HAVING count(*) > 1
            ORDER BY profiles DESC
            LIMIT 5
            """
        )
    ).all()
    if offending:
        accounts = ", ".join(f"{row.account_id} ({row.profiles} profiles)" for row in offending)
        raise RuntimeError(
            "Cannot downgrade f4g5h6i7j8: this database already holds more than "
            "one appearance profile for at least one account, so restoring "
            "UNIQUE(account_id) would require deleting or merging one person's "
            "profile into another's. This migration will not choose which human "
            "to discard. Affected accounts include: "
            f"{accounts}. "
            "Recover with a forward corrective migration that deliberately "
            "resolves each account, not with this downgrade."
        )

    op.drop_index(SUBJECT_INDEX, table_name="appearance_profiles")
    op.drop_index(LEGACY_INDEX, table_name="appearance_profiles")
    op.create_unique_constraint(
        "appearance_profiles_account_id_key", "appearance_profiles", ["account_id"]
    )
    op.drop_constraint(FK_NAME, "appearance_profiles", type_="foreignkey")
    op.drop_column("appearance_profiles", "household_subject_id")
