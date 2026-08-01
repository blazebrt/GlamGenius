"""Appearance digital twin and progressive onboarding.

Revision ID: 0002_appearance_digital_twin
Revises: 0001_v2_foundation
Create Date: 2026-08-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_appearance_digital_twin"
down_revision: Union[str, None] = "0001_v2_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def jsonb(name: str, default: str | None = None, nullable: bool = False):
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), server_default=default, nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "appearance_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("baseline_status", sa.String(24), server_default="not_started", nullable=False),
        sa.Column("baseline_ai_run_id", sa.Uuid(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["account_id"], ["account_links.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["baseline_ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("account_id"),
    )
    op.create_table(
        "profile_attributes",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False), jsonb("value"),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("confidence", sa.Float(), server_default="1", nullable=False),
        sa.Column("verification_state", sa.String(24), server_default="confirmed", nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)), sa.Column("review_due_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)), sa.Column("source_ai_run_id", sa.Uuid()), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("profile_id", "key", name="uq_profile_attribute_key"),
    )
    op.create_index("ix_profile_attributes_profile_section", "profile_attributes", ["profile_id", "key"])
    op.create_table(
        "style_preferences",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("preferred_style", sa.String(120)), jsonb("favourite_colours", "[]"), jsonb("disliked_colours", "[]"),
        jsonb("brand_preferences", "[]"), sa.Column("formality_preference", sa.String(120)), sa.Column("style_experimentation", sa.String(24)),
        jsonb("indian_categories", "[]"), jsonb("western_categories", "[]"), jsonb("fusion_categories", "[]"), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("profile_id"),
    )
    op.create_table(
        "fit_preferences",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("height_cm", sa.Numeric(5, 1)), sa.Column("usual_top_size", sa.String(32)), sa.Column("usual_bottom_size", sa.String(32)),
        sa.Column("shoulder_fit", sa.String(120)), sa.Column("waist_fit", sa.String(120)), sa.Column("hip_fit", sa.String(120)),
        sa.Column("preferred_coverage", sa.String(120)), jsonb("sleeve_preferences", "[]"), jsonb("neckline_preferences", "[]"),
        sa.Column("footwear_comfort", sa.String(120)), jsonb("silhouettes_liked", "[]"), jsonb("silhouettes_avoided", "[]"), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("profile_id"),
    )
    op.create_table(
        "lifestyle_context",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("city", sa.String(120)), sa.Column("climate", sa.String(120)), sa.Column("work_context", sa.String(160)),
        sa.Column("routine", sa.String(200)), sa.Column("activity_level", sa.String(80)), sa.Column("travel_frequency", sa.String(80)),
        sa.Column("budget", sa.String(80)), jsonb("calendar_patterns", "[]"), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("profile_id"),
    )
    op.create_table(
        "appearance_goals",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("goal", sa.String(200), nullable=False), sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(24), server_default="active", nullable=False), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appearance_goals_profile_status", "appearance_goals", ["profile_id", "status"])
    op.create_table(
        "user_constraints",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False), sa.Column("value", sa.String(300), nullable=False), sa.Column("notes", sa.Text()),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_constraints_profile_active", "user_constraints", ["profile_id", "active"])
    op.create_table(
        "attribute_observations",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False), sa.Column("key", sa.String(64), nullable=False),
        jsonb("proposed_value"), sa.Column("source", sa.String(32), nullable=False), sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False), sa.Column("verification_state", sa.String(24), server_default="unverified", nullable=False),
        sa.Column("source_ai_run_id", sa.Uuid()), sa.Column("reviewed_at", sa.DateTime(timezone=True)), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_ai_run_id"], ["ai_runs.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attribute_observations_profile_state", "attribute_observations", ["profile_id", "verification_state", "created_at"])
    op.create_index("ix_attribute_observations_run_key", "attribute_observations", ["source_ai_run_id", "key"])
    op.create_table(
        "onboarding_sessions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), server_default="in_progress", nullable=False), sa.Column("current_step", sa.String(48), server_default="goal", nullable=False),
        jsonb("completed_steps", "[]"), jsonb("skipped_steps", "[]"), jsonb("answers", "{}"), jsonb("recommendation_preview", nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True)), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_onboarding_sessions_profile_status", "onboarding_sessions", ["profile_id", "status", "created_at"])
    op.create_table(
        "profile_change_events",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False), sa.Column("attribute_key", sa.String(64), nullable=False),
        jsonb("old_value", nullable=True), jsonb("new_value", nullable=True), sa.Column("source", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False), *timestamps(),
        sa.ForeignKeyConstraint(["profile_id"], ["appearance_profiles.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_profile_change_events_profile_version", "profile_change_events", ["profile_id", "profile_version", "created_at"])


def downgrade() -> None:
    for table in ["profile_change_events", "onboarding_sessions", "attribute_observations", "user_constraints", "appearance_goals", "lifestyle_context", "fit_preferences", "style_preferences", "profile_attributes", "appearance_profiles"]:
        op.drop_table(table)
