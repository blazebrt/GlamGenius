"""Today engine and weekly planner.

Forward-only and purely additive. It creates the thirteen tables the planner
needs and touches nothing that migrations 0001-0004 created: no existing table
is renamed, altered or dropped, so an existing deployment upgrades without any
data movement.

Revision ID: 0005_today_engine_and_planner
Revises: 0004_phase_4_decision_mvp
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_today_engine_and_planner"
down_revision = "0004_phase_4_decision_mvp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('external_integrations',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='connected', nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('credential_ref', sa.String(length=200), nullable=True),
    sa.Column('external_account_label', sa.String(length=160), nullable=True),
    sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=240), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'kind', 'provider', name='uq_integration_account_kind_provider')
    )
    op.create_index('ix_external_integrations_account_status', 'external_integrations', ['account_id', 'status'], unique=False)
    op.create_table('notification_deliveries',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('notification_key', sa.String(length=64), nullable=False),
    sa.Column('dedup_hash', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=16), server_default='push', nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), server_default='', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False),
    sa.Column('suppressed_reason', sa.String(length=64), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'dedup_hash', name='uq_notification_dedup')
    )
    op.create_index('ix_notification_deliveries_account_date', 'notification_deliveries', ['account_id', 'plan_date'], unique=False)
    op.create_table('notification_preferences',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('daily_cap', sa.Integer(), server_default='1', nullable=False),
    sa.Column('quiet_hours_start', sa.Integer(), server_default='21', nullable=False),
    sa.Column('quiet_hours_end', sa.Integer(), server_default='7', nullable=False),
    sa.Column('preferred_hour', sa.Integer(), server_default='7', nullable=False),
    sa.Column('modules', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('timezone_name', sa.String(length=48), server_default='Asia/Kolkata', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id')
    )
    op.create_table('plan_recalculation_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('trigger', sa.String(length=48), nullable=False),
    sa.Column('detail', sa.String(length=400), server_default='', nullable=False),
    sa.Column('changed_keys', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('old_cache_key', sa.String(length=64), nullable=True),
    sa.Column('new_cache_key', sa.String(length=64), nullable=True),
    sa.Column('recomputed', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_plan_recalculation_account_date', 'plan_recalculation_events', ['account_id', 'plan_date', 'created_at'], unique=False)
    op.create_table('weather_snapshots',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('for_date', sa.Date(), nullable=False),
    sa.Column('location', sa.String(length=160), nullable=True),
    sa.Column('provider', sa.String(length=32), server_default='manual', nullable=False),
    sa.Column('source', sa.String(length=32), server_default='user_declared', nullable=False),
    sa.Column('condition', sa.String(length=24), nullable=False),
    sa.Column('temp_min_c', sa.Float(), nullable=True),
    sa.Column('temp_max_c', sa.Float(), nullable=True),
    sa.Column('precipitation_chance', sa.Integer(), nullable=True),
    sa.Column('humidity', sa.Integer(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_weather_snapshots_account_date', 'weather_snapshots', ['account_id', 'for_date', 'created_at'], unique=False)
    op.create_table('weekly_plans',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('week_start', sa.Date(), nullable=False),
    sa.Column('timezone_name', sa.String(length=48), server_default='Asia/Kolkata', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='ready', nullable=False),
    sa.Column('engine_version', sa.String(length=32), server_default='phase5-v1', nullable=False),
    sa.Column('repetition_window_days', sa.Integer(), server_default='7', nullable=False),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'week_start', name='uq_weekly_plan_account_week')
    )
    op.create_index('ix_weekly_plans_account_week', 'weekly_plans', ['account_id', 'week_start'], unique=False)
    op.create_table('calendar_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('integration_id', sa.Uuid(), nullable=True),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('dedup_key', sa.String(length=400), nullable=False),
    sa.Column('title', sa.String(length=240), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('all_day', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('location', sa.String(length=200), nullable=True),
    sa.Column('occasion_key', sa.String(length=32), nullable=True),
    sa.Column('dress_code_hint', sa.String(length=32), nullable=True),
    sa.Column('inference_confidence', sa.Float(), server_default='0', nullable=False),
    sa.Column('user_confirmed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('provider', sa.String(length=32), server_default='manual', nullable=False),
    sa.Column('source', sa.String(length=32), server_default='user_declared', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['integration_id'], ['external_integrations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'dedup_key', name='uq_calendar_event_dedup')
    )
    op.create_index('ix_calendar_events_account_start', 'calendar_events', ['account_id', 'starts_at'], unique=False)
    op.create_table('laundry_state_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('available_from', sa.Date(), nullable=True),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('actor', sa.String(length=16), server_default='user', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_laundry_state_events_item', 'laundry_state_events', ['account_id', 'item_id', 'created_at'], unique=False)
    op.create_table('daily_plans',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('timezone_name', sa.String(length=48), server_default='Asia/Kolkata', nullable=False),
    sa.Column('status', sa.String(length=24), server_default='ready', nullable=False),
    sa.Column('headline', sa.String(length=240), server_default='', nullable=False),
    sa.Column('look_id', sa.Uuid(), nullable=True),
    sa.Column('weather_snapshot_id', sa.Uuid(), nullable=True),
    sa.Column('weather_note', sa.Text(), server_default='', nullable=False),
    sa.Column('event_note', sa.Text(), server_default='', nullable=False),
    sa.Column('confidence', sa.Float(), server_default='0', nullable=False),
    sa.Column('generated_from', sa.String(length=16), server_default='fresh', nullable=False),
    sa.Column('engine_version', sa.String(length=32), server_default='phase5-v1', nullable=False),
    sa.Column('cache_key', sa.String(length=64), nullable=False),
    sa.Column('used_llm', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('needs_clarification', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('clarification', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('missing_information', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('locked', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['look_id'], ['looks.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['weather_snapshot_id'], ['weather_snapshots.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'plan_date', name='uq_daily_plan_account_date')
    )
    op.create_index('ix_daily_plans_account_date', 'daily_plans', ['account_id', 'plan_date'], unique=False)
    op.create_table('outfit_schedule',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('look_id', sa.Uuid(), nullable=True),
    sa.Column('item_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='planned', nullable=False),
    sa.Column('worn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['look_id'], ['looks.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'plan_date', name='uq_outfit_schedule_account_date')
    )
    op.create_index('ix_outfit_schedule_account_date', 'outfit_schedule', ['account_id', 'plan_date'], unique=False)
    op.create_table('daily_plan_actions',
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('module', sa.String(length=24), nullable=False),
    sa.Column('action_type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), server_default='', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='50', nullable=False),
    sa.Column('relevance', sa.String(length=240), server_default='', nullable=False),
    sa.Column('inventory_item_id', sa.Uuid(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plan_id'], ['daily_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_daily_plan_actions_plan_priority', 'daily_plan_actions', ['plan_id', 'priority'], unique=False)
    op.create_table('daily_plan_inputs',
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('input_type', sa.String(length=32), nullable=False),
    sa.Column('input_key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['daily_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_daily_plan_inputs_plan', 'daily_plan_inputs', ['plan_id', 'input_type'], unique=False)
    op.create_table('weekly_plan_days',
    sa.Column('weekly_plan_id', sa.Uuid(), nullable=False),
    sa.Column('plan_date', sa.Date(), nullable=False),
    sa.Column('daily_plan_id', sa.Uuid(), nullable=True),
    sa.Column('locked', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['daily_plan_id'], ['daily_plans.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['weekly_plan_id'], ['weekly_plans.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('weekly_plan_id', 'plan_date', name='uq_weekly_plan_day')
    )
    op.create_index('ix_weekly_plan_days_plan', 'weekly_plan_days', ['weekly_plan_id', 'position'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_weekly_plan_days_plan', table_name='weekly_plan_days')
    op.drop_table('weekly_plan_days')
    op.drop_index('ix_daily_plan_inputs_plan', table_name='daily_plan_inputs')
    op.drop_table('daily_plan_inputs')
    op.drop_index('ix_daily_plan_actions_plan_priority', table_name='daily_plan_actions')
    op.drop_table('daily_plan_actions')
    op.drop_index('ix_outfit_schedule_account_date', table_name='outfit_schedule')
    op.drop_table('outfit_schedule')
    op.drop_index('ix_daily_plans_account_date', table_name='daily_plans')
    op.drop_table('daily_plans')
    op.drop_index('ix_laundry_state_events_item', table_name='laundry_state_events')
    op.drop_table('laundry_state_events')
    op.drop_index('ix_calendar_events_account_start', table_name='calendar_events')
    op.drop_table('calendar_events')
    op.drop_index('ix_weekly_plans_account_week', table_name='weekly_plans')
    op.drop_table('weekly_plans')
    op.drop_index('ix_weather_snapshots_account_date', table_name='weather_snapshots')
    op.drop_table('weather_snapshots')
    op.drop_index('ix_plan_recalculation_account_date', table_name='plan_recalculation_events')
    op.drop_table('plan_recalculation_events')
    op.drop_table('notification_preferences')
    op.drop_index('ix_notification_deliveries_account_date', table_name='notification_deliveries')
    op.drop_table('notification_deliveries')
    op.drop_index('ix_external_integrations_account_status', table_name='external_integrations')
    op.drop_table('external_integrations')
