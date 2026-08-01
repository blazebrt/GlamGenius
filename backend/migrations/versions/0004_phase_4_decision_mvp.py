"""Phase 4 decision MVP: occasion styling and shopping decisions.

Forward-only and purely additive. It creates the fourteen tables the decision
engine needs and touches nothing that migrations 0001-0003 created: no existing
table is renamed, altered or dropped, so an existing deployment upgrades without
any data movement.

Revision ID: 0004_phase_4_decision_mvp
Revises: 0003_complete_inventory
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_phase_4_decision_mvp"
down_revision = "0003_complete_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('occasions',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('occasion_key', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=True),
    sa.Column('event_date', sa.Date(), nullable=True),
    sa.Column('time_of_day', sa.String(length=24), nullable=True),
    sa.Column('location', sa.String(length=160), nullable=True),
    sa.Column('setting', sa.String(length=16), nullable=True),
    sa.Column('dress_code', sa.String(length=32), nullable=True),
    sa.Column('weather', sa.String(length=24), nullable=True),
    sa.Column('comfort_preference', sa.String(length=24), nullable=True),
    sa.Column('optional_budget', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('preparation_time', sa.String(length=48), nullable=True),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_occasions_account_created', 'occasions', ['account_id', 'created_at'], unique=False)
    op.create_index('ix_occasions_account_key', 'occasions', ['account_id', 'occasion_key'], unique=False)
    op.create_table('recommendation_entitlements',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('feature', sa.String(length=48), nullable=False),
    sa.Column('period_key', sa.String(length=7), nullable=False),
    sa.Column('included', sa.Integer(), server_default='0', nullable=False),
    sa.Column('used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('source', sa.String(length=32), server_default='beta_grant', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'feature', 'period_key', name='uq_recommendation_entitlement_period')
    )
    op.create_index('ix_recommendation_entitlements_account', 'recommendation_entitlements', ['account_id', 'period_key'], unique=False)
    op.create_table('shopping_candidates',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=24), nullable=False),
    sa.Column('media_asset_id', sa.Uuid(), nullable=True),
    sa.Column('ai_run_id', sa.Uuid(), nullable=True),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('subcategory', sa.String(length=80), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('brand', sa.String(length=120), nullable=True),
    sa.Column('colour', sa.String(length=80), nullable=True),
    sa.Column('size', sa.String(length=40), nullable=True),
    sa.Column('fabric', sa.String(length=100), nullable=True),
    sa.Column('fit', sa.String(length=80), nullable=True),
    sa.Column('formality', sa.String(length=80), nullable=True),
    sa.Column('occasion_tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('season_tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('product_url', sa.String(length=2048), nullable=True),
    sa.Column('extraction_confidence', sa.Float(), nullable=True),
    sa.Column('uncertain_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('verification_state', sa.String(length=24), server_default='draft', nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=True),
    sa.Column('prompt_version', sa.String(length=32), nullable=True),
    sa.Column('schema_version', sa.String(length=32), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ai_run_id'], ['ai_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['media_asset_id'], ['media_assets.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_shopping_candidates_account_created', 'shopping_candidates', ['account_id', 'created_at'], unique=False)
    op.create_table('style_requests',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('occasion_id', sa.Uuid(), nullable=False),
    sa.Column('preferred_item_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('answers', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('client_mutation_id', sa.String(length=80), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['occasion_id'], ['occasions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'client_mutation_id', name='uq_style_request_client_mutation')
    )
    op.create_index('ix_style_requests_account_created', 'style_requests', ['account_id', 'created_at'], unique=False)
    op.create_table('compatibility_edges',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('item_a_id', sa.Uuid(), nullable=False),
    sa.Column('item_b_id', sa.Uuid(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('basis', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('metric_version', sa.String(length=32), server_default='phase4-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_a_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_b_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'item_a_id', 'item_b_id', 'metric_version', name='uq_compatibility_edge_pair')
    )
    op.create_index('ix_compatibility_edges_account', 'compatibility_edges', ['account_id', 'score'], unique=False)
    op.create_table('recommendation_runs',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('style_request_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('engine_version', sa.String(length=32), server_default='phase4-v1', nullable=False),
    sa.Column('explanation_source', sa.String(length=24), server_default='deterministic', nullable=False),
    sa.Column('ai_run_id', sa.Uuid(), nullable=True),
    sa.Column('candidates_considered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ai_run_id'], ['ai_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['style_request_id'], ['style_requests.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_recommendation_runs_account_kind', 'recommendation_runs', ['account_id', 'kind', 'created_at'], unique=False)
    op.create_table('looks',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('variant', sa.String(length=24), nullable=False),
    sa.Column('rank', sa.Integer(), server_default='1', nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('score', sa.Float(), server_default='0', nullable=False),
    sa.Column('confidence', sa.Float(), server_default='0', nullable=False),
    sa.Column('why_it_works', sa.Text(), nullable=False),
    sa.Column('weather_note', sa.Text(), server_default='', nullable=False),
    sa.Column('dress_code_note', sa.Text(), server_default='', nullable=False),
    sa.Column('preparation_steps', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('missing_information', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('factor_scores', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('explanation_source', sa.String(length=24), server_default='deterministic', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('saved', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['recommendation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'variant', name='uq_look_run_variant')
    )
    op.create_index('ix_looks_account_created', 'looks', ['account_id', 'created_at'], unique=False)
    op.create_table('purchase_evaluations',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('candidate_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('verdict', sa.String(length=8), nullable=False),
    sa.Column('roi_score', sa.Float(), nullable=False),
    sa.Column('roi_version', sa.String(length=32), server_default='appearance-roi-v1', nullable=False),
    sa.Column('confidence', sa.Float(), server_default='0', nullable=False),
    sa.Column('new_combinations', sa.Integer(), server_default='0', nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('headline', sa.String(length=200), server_default='', nullable=False),
    sa.Column('duplicate_item_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('alternative_item_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('fit_risks', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('colour_risks', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('climate_notes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('missing_information', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('explanation_source', sa.String(length=24), server_default='deterministic', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['candidate_id'], ['shopping_candidates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['recommendation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_evaluations_account_created', 'purchase_evaluations', ['account_id', 'created_at'], unique=False)
    op.create_table('recommendation_inputs',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('input_type', sa.String(length=32), nullable=False),
    sa.Column('input_key', sa.String(length=64), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['recommendation_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_recommendation_inputs_run', 'recommendation_inputs', ['run_id', 'input_type'], unique=False)
    op.create_table('look_adjustments',
    sa.Column('look_id', sa.Uuid(), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('adjustment_type', sa.String(length=24), nullable=False),
    sa.Column('slot', sa.String(length=24), nullable=True),
    sa.Column('from_item_id', sa.Uuid(), nullable=True),
    sa.Column('to_item_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.String(length=400), nullable=True),
    sa.Column('actor', sa.String(length=16), server_default='user', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_item_id'], ['inventory_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['look_id'], ['looks.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_item_id'], ['inventory_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_look_adjustments_look', 'look_adjustments', ['look_id', 'created_at'], unique=False)
    op.create_table('look_feedback',
    sa.Column('look_id', sa.Uuid(), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('rating', sa.String(length=24), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=True),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('worn_on', sa.Date(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['look_id'], ['looks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('look_id', 'account_id', name='uq_look_feedback_once')
    )
    op.create_index('ix_look_feedback_account', 'look_feedback', ['account_id', 'created_at'], unique=False)
    op.create_table('look_items',
    sa.Column('look_id', sa.Uuid(), nullable=False),
    sa.Column('slot', sa.String(length=24), nullable=False),
    sa.Column('ownership', sa.String(length=24), nullable=False),
    sa.Column('inventory_item_id', sa.Uuid(), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=False),
    sa.Column('role_note', sa.String(length=400), server_default='', nullable=False),
    sa.Column('score', sa.Float(), server_default='0', nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('estimated_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['look_id'], ['looks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_look_items_look_slot', 'look_items', ['look_id', 'slot', 'position'], unique=False)
    op.create_table('purchase_decisions',
    sa.Column('evaluation_id', sa.Uuid(), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('followed_recommendation', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evaluation_id'], ['purchase_evaluations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('evaluation_id', 'account_id', name='uq_purchase_decision_once')
    )
    op.create_index('ix_purchase_decisions_account', 'purchase_decisions', ['account_id', 'created_at'], unique=False)
    op.create_table('purchase_evaluation_factors',
    sa.Column('evaluation_id', sa.Uuid(), nullable=False),
    sa.Column('factor_key', sa.String(length=48), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('raw_value', sa.Float(), nullable=False),
    sa.Column('weight', sa.Float(), nullable=False),
    sa.Column('contribution', sa.Float(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['evaluation_id'], ['purchase_evaluations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('evaluation_id', 'factor_key', name='uq_purchase_factor_key')
    )
    op.create_index('ix_purchase_factors_evaluation', 'purchase_evaluation_factors', ['evaluation_id', 'position'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_purchase_factors_evaluation', table_name='purchase_evaluation_factors')
    op.drop_table('purchase_evaluation_factors')
    op.drop_index('ix_purchase_decisions_account', table_name='purchase_decisions')
    op.drop_table('purchase_decisions')
    op.drop_index('ix_look_items_look_slot', table_name='look_items')
    op.drop_table('look_items')
    op.drop_index('ix_look_feedback_account', table_name='look_feedback')
    op.drop_table('look_feedback')
    op.drop_index('ix_look_adjustments_look', table_name='look_adjustments')
    op.drop_table('look_adjustments')
    op.drop_index('ix_recommendation_inputs_run', table_name='recommendation_inputs')
    op.drop_table('recommendation_inputs')
    op.drop_index('ix_purchase_evaluations_account_created', table_name='purchase_evaluations')
    op.drop_table('purchase_evaluations')
    op.drop_index('ix_looks_account_created', table_name='looks')
    op.drop_table('looks')
    op.drop_index('ix_recommendation_runs_account_kind', table_name='recommendation_runs')
    op.drop_table('recommendation_runs')
    op.drop_index('ix_compatibility_edges_account', table_name='compatibility_edges')
    op.drop_table('compatibility_edges')
    op.drop_index('ix_style_requests_account_created', table_name='style_requests')
    op.drop_table('style_requests')
    op.drop_index('ix_shopping_candidates_account_created', table_name='shopping_candidates')
    op.drop_table('shopping_candidates')
    op.drop_index('ix_recommendation_entitlements_account', table_name='recommendation_entitlements')
    op.drop_table('recommendation_entitlements')
    op.drop_index('ix_occasions_account_key', table_name='occasions')
    op.drop_index('ix_occasions_account_created', table_name='occasions')
    op.drop_table('occasions')
