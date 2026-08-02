"""Explainable progress, controlled memory and milestones.

Forward-only and purely additive. It creates the seventeen tables Phase 7 needs
and touches nothing migrations 0001-0006 created: no existing table is renamed,
altered or dropped, so an existing deployment upgrades without any data
movement.

Note on ``progress_goals``. Phase 2 already has an ``appearance_goals`` table,
and this migration deliberately does **not** extend it. That table is a
projection of a profile attribute — ``profile.service.sync_projections`` deletes
and rebuilds every row in it whenever the profile is patched — so attaching
months of goal history to it would lose that history silently on an unrelated
profile edit. ``progress_goals.appearance_goal_id`` links the two instead, so a
declared goal can be promoted into a tracked one without either table changing
shape.

The reviewed reference data — ``metric_definitions`` and ``milestone_rules`` —
is seeded here from ``app.domains.progress.registry`` and ``.milestones`` rather
than from a copied literal, so a stored metric event always has a formula row to
be read against. Rows are keyed on ``(key, formula_version)``, which means
changing a formula later adds a row instead of rewriting history.

Revision ID: 0007_progress_and_memory
Revises: 0006_routine_intelligence
"""
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0007_progress_and_memory'
down_revision: Union[str, None] = '0006_routine_intelligence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('metric_definitions',
    sa.Column('key', sa.String(length=48), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('unit', sa.String(length=16), nullable=False),
    sa.Column('direction', sa.String(length=24), nullable=False),
    sa.Column('formula', sa.Text(), nullable=False),
    sa.Column('formula_version', sa.String(length=16), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('missing_data_behaviour', sa.String(length=48), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('update_frequency', sa.String(length=16), nullable=False),
    sa.Column('not_a_measure_of', sa.Text(), server_default='', nullable=False),
    sa.Column('registry_version', sa.String(length=24), server_default='phase7-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key', 'formula_version', name='uq_metric_definition_version')
    )
    op.create_table('milestone_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('behaviour', sa.String(length=48), nullable=False),
    sa.Column('threshold', sa.Integer(), server_default='1', nullable=False),
    sa.Column('repeatable', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('registry_version', sa.String(length=24), server_default='phase7-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_table('gamification_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('behaviour', sa.String(length=48), nullable=False),
    sa.Column('occurred_on', sa.Date(), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('dedup_hash', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'dedup_hash', name='uq_gamification_event_dedup')
    )
    op.create_index('ix_gamification_events_account', 'gamification_events', ['account_id', 'behaviour', 'occurred_on'], unique=False)
    op.create_table('memory_category_preferences',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'category', name='uq_memory_category_preference')
    )
    op.create_table('memory_facts',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('subject_key', sa.String(length=200), nullable=False),
    sa.Column('fact', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('confidence', sa.Float(), server_default='0.5', nullable=False),
    sa.Column('verification_state', sa.String(length=16), server_default='unverified', nullable=False),
    sa.Column('deletion_state', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_reinforced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reinforcement_count', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_facts_account_state', 'memory_facts', ['account_id', 'deletion_state', 'category'], unique=False)
    op.create_index('ix_memory_facts_subject', 'memory_facts', ['account_id', 'category', 'subject_key'], unique=False)
    op.create_table('metric_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('metric_key', sa.String(length=48), nullable=False),
    sa.Column('formula_version', sa.String(length=16), nullable=False),
    sa.Column('period', sa.String(length=16), server_default='point', nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('unit', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='ok', nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('missing_inputs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('dedup_hash', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'dedup_hash', name='uq_metric_event_dedup')
    )
    op.create_index('ix_metric_events_account_metric_date', 'metric_events', ['account_id', 'metric_key', 'period_start'], unique=False)
    op.create_table('milestones',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('earned_on', sa.Date(), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['milestone_rules.rule_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'rule_id', 'earned_on', name='uq_milestone_account_rule_day')
    )
    op.create_index('ix_milestones_account', 'milestones', ['account_id', 'earned_on'], unique=False)
    op.create_table('progress_snapshots',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('period', sa.String(length=16), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('registry_version', sa.String(length=24), server_default='phase7-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'period', 'period_start', name='uq_progress_snapshot_period')
    )
    op.create_index('ix_progress_snapshots_account', 'progress_snapshots', ['account_id', 'period_start'], unique=False)
    op.create_table('streaks',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('behaviour', sa.String(length=48), nullable=False),
    sa.Column('current_length', sa.Integer(), server_default='0', nullable=False),
    sa.Column('longest_length', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_on', sa.Date(), nullable=True),
    sa.Column('last_counted_on', sa.Date(), nullable=True),
    sa.Column('last_reset_on', sa.Date(), nullable=True),
    sa.Column('reset_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'behaviour', name='uq_streak_account_behaviour')
    )
    op.create_table('comparison_sessions',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('baseline_media_id', sa.Uuid(), nullable=False),
    sa.Column('current_media_id', sa.Uuid(), nullable=False),
    sa.Column('body_area', sa.String(length=32), nullable=False),
    sa.Column('comparable', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('blocking_reasons', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('days_apart', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['baseline_media_id'], ['media_assets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['current_media_id'], ['media_assets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_comparison_sessions_account', 'comparison_sessions', ['account_id', 'created_at'], unique=False)
    op.create_table('feedback_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=True),
    sa.Column('signal', sa.String(length=24), nullable=False),
    sa.Column('reason', sa.String(length=240), nullable=True),
    sa.Column('learned_fact_id', sa.Uuid(), nullable=True),
    sa.Column('applied', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['learned_fact_id'], ['memory_facts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feedback_events_account', 'feedback_events', ['account_id', 'created_at'], unique=False)
    op.create_table('memory_revisions',
    sa.Column('fact_id', sa.Uuid(), nullable=False),
    sa.Column('previous_fact', sa.Text(), nullable=False),
    sa.Column('previous_confidence', sa.Float(), server_default='0', nullable=False),
    sa.Column('previous_verification_state', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=48), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['fact_id'], ['memory_facts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_revisions_fact', 'memory_revisions', ['fact_id', 'created_at'], unique=False)
    op.create_table('memory_sources',
    sa.Column('fact_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['fact_id'], ['memory_facts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_sources_fact', 'memory_sources', ['fact_id', 'observed_at'], unique=False)
    op.create_table('progress_photos',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('media_id', sa.Uuid(), nullable=False),
    sa.Column('body_area', sa.String(length=32), nullable=False),
    sa.Column('taken_on', sa.Date(), nullable=False),
    sa.Column('lighting', sa.String(length=24), nullable=False),
    sa.Column('angle', sa.String(length=24), nullable=False),
    sa.Column('framing', sa.String(length=24), nullable=False),
    sa.Column('time_of_day', sa.String(length=16), nullable=True),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['media_id'], ['media_assets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('media_id', name='uq_progress_photo_media')
    )
    op.create_index('ix_progress_photos_account_area', 'progress_photos', ['account_id', 'body_area', 'taken_on'], unique=False)
    op.create_table('score_explanations',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('metric_event_id', sa.Uuid(), nullable=False),
    sa.Column('metric_key', sa.String(length=48), nullable=False),
    sa.Column('formula_version', sa.String(length=16), nullable=False),
    sa.Column('formula', sa.Text(), nullable=False),
    sa.Column('plain_english', sa.Text(), nullable=False),
    sa.Column('contributing_factors', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['metric_event_id'], ['metric_events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_score_explanations_event', 'score_explanations', ['metric_event_id'], unique=False)
    op.create_table('progress_goals',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('appearance_goal_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('metric_key', sa.String(length=48), nullable=True),
    sa.Column('target_value', sa.Float(), nullable=True),
    sa.Column('starting_value', sa.Float(), nullable=True),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('target_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['appearance_goal_id'], ['appearance_goals.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_progress_goals_account_status', 'progress_goals', ['account_id', 'status'], unique=False)
    op.create_table('goal_updates',
    sa.Column('goal_id', sa.Uuid(), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('value', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=24), server_default='metric', nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('recorded_on', sa.Date(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['goal_id'], ['progress_goals.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_goal_updates_goal', 'goal_updates', ['goal_id', 'recorded_on'], unique=False)

    _seed_reference_data()


def downgrade() -> None:
    op.drop_index('ix_goal_updates_goal', table_name='goal_updates')
    op.drop_table('goal_updates')
    op.drop_index('ix_progress_goals_account_status', table_name='progress_goals')
    op.drop_table('progress_goals')
    op.drop_index('ix_score_explanations_event', table_name='score_explanations')
    op.drop_table('score_explanations')
    op.drop_index('ix_progress_photos_account_area', table_name='progress_photos')
    op.drop_table('progress_photos')
    op.drop_index('ix_memory_sources_fact', table_name='memory_sources')
    op.drop_table('memory_sources')
    op.drop_index('ix_memory_revisions_fact', table_name='memory_revisions')
    op.drop_table('memory_revisions')
    op.drop_index('ix_feedback_events_account', table_name='feedback_events')
    op.drop_table('feedback_events')
    op.drop_index('ix_comparison_sessions_account', table_name='comparison_sessions')
    op.drop_table('comparison_sessions')
    op.drop_table('streaks')
    op.drop_index('ix_progress_snapshots_account', table_name='progress_snapshots')
    op.drop_table('progress_snapshots')
    op.drop_index('ix_milestones_account', table_name='milestones')
    op.drop_table('milestones')
    op.drop_index('ix_metric_events_account_metric_date', table_name='metric_events')
    op.drop_table('metric_events')
    op.drop_index('ix_memory_facts_subject', table_name='memory_facts')
    op.drop_index('ix_memory_facts_account_state', table_name='memory_facts')
    op.drop_table('memory_facts')
    op.drop_table('memory_category_preferences')
    op.drop_index('ix_gamification_events_account', table_name='gamification_events')
    op.drop_table('gamification_events')
    op.drop_table('milestone_rules')
    op.drop_table('metric_definitions')


# --- Seeding the reviewed reference data ------------------------------------


def _seed_reference_data() -> None:
    """Write the metric contracts and milestone rules into the database.

    Imported from the domain modules rather than copied, so the formula a user
    is shown and the formula the engine ran are the same string by construction.
    ``ON CONFLICT DO NOTHING`` on the natural key makes this safe to re-run.
    """
    from app.domains.progress import milestones, registry

    connection = op.get_bind()

    def insert(table: str, rows, conflict: str) -> None:
        if not rows:
            return
        columns = list(rows[0])
        statement = sa.text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + name for name in columns)}) "
            f"ON CONFLICT ({conflict}) DO NOTHING"
        )
        connection.execute(statement, rows)

    insert("metric_definitions", [{
        "id": str(uuid.uuid4()), "key": row.key, "label": row.label, "unit": row.unit,
        "direction": row.direction, "formula": row.formula,
        "formula_version": row.formula_version, "inputs": json.dumps(list(row.inputs)),
        "missing_data_behaviour": row.missing_data_behaviour,
        "explanation": row.explanation, "update_frequency": row.update_frequency,
        "not_a_measure_of": row.not_a_measure_of,
        "registry_version": registry.REGISTRY_VERSION,
    } for row in registry.METRICS], "key, formula_version")

    insert("milestone_rules", [{
        "id": str(uuid.uuid4()), "rule_id": row.rule_id, "label": row.label,
        "description": row.description, "behaviour": row.behaviour,
        "threshold": row.threshold, "repeatable": row.repeatable,
        "registry_version": milestones.MILESTONES_VERSION,
    } for row in milestones.RULES], "rule_id")
