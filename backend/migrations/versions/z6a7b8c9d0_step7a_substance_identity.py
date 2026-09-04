"""Step 7A: canonical substance identity, and the evidence vocabulary it needs.

Two new Store-B tables plus three widened CHECK constraints on the existing
evidence tables. No data is backfilled: in particular nothing is copied from the
legacy Care ingredient aliases, because those are broad routine-matching
concepts rather than exact identities, and promoting them here would assert
equivalences no reviewer ever made.

Nothing in Open Food Facts' Store A is touched.

Revision ID: z6a7b8c9d0
Revises: y5z6a7b8c9
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "z6a7b8c9d0"
down_revision = "y5z6a7b8c9"
branch_labels = None
depends_on = None


def _in_list(column: str, values: tuple[str, ...]) -> str:
    literal = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({literal})"


_OLD_DOMAINS = ("skin_care", "hair_care", "home_care", "nutrition", "supplements", "product_quality")
_NEW_DOMAINS = (*_OLD_DOMAINS, "substance")

_OLD_CLAIM_TYPES = (
    "compatibility_context", "contraindication_context", "sensitivity_context",
    "usage_context", "regulatory_context", "nutrition_reference",
    "product_provenance", "traditional_use",
)
_NEW_CLAIM_TYPES = (*_OLD_CLAIM_TYPES, "substance_identity")

_OLD_TIERS = ("clinically_studied", "classical_text", "traditional_use", "not_enough_information", "avoid")
_NEW_TIERS = (
    "clinically_studied", "classical_text", "traditional_use", "reference_data",
    "not_enough_information", "avoid",
)

_ENTITY_KINDS = ("defined_substance", "botanical_material", "mixture", "group")
_SUBSTANCE_STATUSES = ("active", "retired")
_NAME_NAMESPACES = ("inci", "scientific", "common", "official_reference", "other")


def upgrade() -> None:
    # --- widen the existing evidence vocabularies -------------------------
    # Widening only. No existing value changes meaning, and nothing that was
    # allowed before is disallowed now, so no row can be invalidated.
    for table, constraint, column, values in (
        ("evidence_claims", "ck_evidence_claims_domain", "domain", _NEW_DOMAINS),
        ("rule_evidence_links", "ck_rule_evidence_links_domain", "domain", _NEW_DOMAINS),
        ("evidence_claims", "ck_evidence_claims_claim_type", "claim_type", _NEW_CLAIM_TYPES),
        ("evidence_claims", "ck_evidence_claims_tier", "evidence_tier", _NEW_TIERS),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _in_list(column, values))

    # --- substances -------------------------------------------------------
    op.create_table(
        "substances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("substance_key", sa.String(length=120), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("substance_key"),
        sa.CheckConstraint(_in_list("entity_kind", _ENTITY_KINDS), name="ck_substances_entity_kind"),
        sa.CheckConstraint(_in_list("status", _SUBSTANCE_STATUSES), name="ck_substances_status"),
        sa.CheckConstraint("substance_key ~ '^[a-z0-9][a-z0-9_.-]*$'", name="ck_substances_key_shape"),
    )

    # --- substance_names --------------------------------------------------
    op.create_table(
        "substance_names",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("substance_id", sa.Uuid(), nullable=False),
        sa.Column("identity_claim_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("namespace", sa.String(length=32), nullable=False),
        sa.Column("language_tag", sa.String(length=32), nullable=True),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["substance_id"], ["substances.id"], ondelete="CASCADE"),
        # RESTRICT: an identity row must never outlive the evidence for it.
        sa.ForeignKeyConstraint(["identity_claim_id"], ["evidence_claims.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(_in_list("namespace", _NAME_NAMESPACES), name="ck_substance_names_namespace"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_substance_names_name_present"),
        sa.CheckConstraint("btrim(normalized_name) <> ''", name="ck_substance_names_normalized_present"),
        # Bookkeeping only. Deliberately NOT unique on normalized_name alone:
        # the same text may legitimately name two entities, and the resolver
        # answers AMBIGUOUS rather than letting the database pick a winner by
        # insertion order.
        sa.UniqueConstraint(
            "identity_claim_id", "substance_id", "normalized_name", "namespace",
            name="uq_substance_name_claim_identity",
        ),
    )
    op.create_index("ix_substance_names_normalized", "substance_names", ["normalized_name"])
    op.create_index("ix_substance_names_substance", "substance_names", ["substance_id"])
    op.create_index("ix_substance_names_claim", "substance_names", ["identity_claim_id"])


def downgrade() -> None:
    op.drop_index("ix_substance_names_claim", table_name="substance_names")
    op.drop_index("ix_substance_names_substance", table_name="substance_names")
    op.drop_index("ix_substance_names_normalized", table_name="substance_names")
    op.drop_table("substance_names")
    op.drop_table("substances")

    # Narrow the vocabularies back. Any row using a Step 7A value would make the
    # old constraint invalid, so those rows go first.
    #
    # Everything referencing an identity claim is cleared *by claim id*, not by
    # domain. Both incoming foreign keys are ``RESTRICT``, and a row on the far
    # side of one may legitimately carry a different domain — a rule link from
    # skin_care, or a later claim that supersedes an identity claim — so a
    # domain-scoped delete would leave the reference in place and the downgrade
    # would fail on a database that has any of them. A downgrade that only works
    # on an empty database is not a downgrade.
    substance_claims = "SELECT id FROM evidence_claims WHERE domain = 'substance'"
    op.execute(f"DELETE FROM evidence_claim_sources WHERE claim_id IN ({substance_claims})")
    op.execute(f"DELETE FROM rule_evidence_links WHERE claim_id IN ({substance_claims})")
    op.execute(
        "UPDATE evidence_claims SET supersedes_claim_id = NULL "
        f"WHERE supersedes_claim_id IN ({substance_claims})"
    )
    op.execute("DELETE FROM evidence_claims WHERE domain = 'substance'")
    # The tier is nulled rather than remapped: no old tier means the same thing,
    # and guessing one would misstate what a reviewer graded.
    op.execute("UPDATE evidence_claims SET evidence_tier = NULL WHERE evidence_tier = 'reference_data'")

    for table, constraint, column, values in (
        ("evidence_claims", "ck_evidence_claims_tier", "evidence_tier", _OLD_TIERS),
        ("evidence_claims", "ck_evidence_claims_claim_type", "claim_type", _OLD_CLAIM_TYPES),
        ("rule_evidence_links", "ck_rule_evidence_links_domain", "domain", _OLD_DOMAINS),
        ("evidence_claims", "ck_evidence_claims_domain", "domain", _OLD_DOMAINS),
    ):
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _in_list(column, values))
