"""Billing, entitlements, experiments and release tooling.

Forward-only and purely additive. It creates the sixteen tables Phase 8 needs
and touches nothing migrations 0001-0007 created.

The money tables are append-only by design, and three unique constraints carry
most of the correctness rather than application code:

* ``uq_payment_event_provider_id`` — a webhook delivered five times is
  processed once, because the second insert collides.
* ``uq_entitlement_grant`` — a duplicate grant collides instead of stacking, so
  a replayed payment cannot double somebody's allowance.
* ``uq_entitlement_usage_idempotency`` — a retried request cannot spend the
  same allowance twice.

Doing this with constraints rather than checks matters: two workers processing
the same retried webhook can both pass an application check, but they cannot
both win an insert.

The plan and offer catalogue is seeded from ``app.domains.billing.catalogue``.
Note that **no price is seeded from a literal** — the amounts come from
configuration at seed time, because a price must never need a deploy.

Revision ID: 0008_billing_and_release
Revises: 0007_progress_and_memory
"""
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0008_billing_and_release'
down_revision: Union[str, None] = '0007_progress_and_memory'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('experiments',
    sa.Column('key', sa.String(length=48), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('surface', sa.String(length=24), nullable=False),
    sa.Column('variants', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('notes', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('feature_cost_daily',
    sa.Column('on_date', sa.Date(), nullable=False),
    sa.Column('feature', sa.String(length=64), nullable=False),
    sa.Column('run_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failure_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('estimated_cost_usd', sa.Float(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('on_date', 'feature', name='uq_feature_cost_day')
    )
    op.create_index('ix_feature_cost_date', 'feature_cost_daily', ['on_date'], unique=False)
    op.create_table('model_evaluations',
    sa.Column('feature', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), server_default='', nullable=False),
    sa.Column('schema_version', sa.String(length=32), server_default='', nullable=False),
    sa.Column('model', sa.String(length=64), server_default='', nullable=False),
    sa.Column('sample_key', sa.String(length=80), nullable=False),
    sa.Column('passed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('failure_reason', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_model_evaluations_feature', 'model_evaluations', ['feature', 'created_at'], unique=False)
    op.create_table('plans',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('tagline', sa.Text(), server_default='', nullable=False),
    sa.Column('recurring', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('valid_days', sa.Integer(), nullable=True),
    sa.Column('limits', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('benefits', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('catalogue_version', sa.String(length=24), server_default='phase8-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('billing_audit_events',
    sa.Column('account_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=48), nullable=False),
    sa.Column('actor', sa.String(length=24), server_default='system', nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=True),
    sa.Column('subject_id', sa.String(length=80), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_billing_audit_account', 'billing_audit_events', ['account_id', 'created_at'], unique=False)
    op.create_table('entitlements',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('feature', sa.String(length=48), nullable=False),
    sa.Column('plan_key', sa.String(length=32), nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('limit_value', sa.Integer(), nullable=True),
    sa.Column('used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('source', sa.String(length=24), server_default='plan', nullable=False),
    sa.Column('source_id', sa.Uuid(), nullable=True),
    sa.Column('grant_key', sa.String(length=48), server_default='free', nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=False),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=120), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'feature', 'period_key', 'grant_key', name='uq_entitlement_grant')
    )
    op.create_index('ix_entitlements_account_feature', 'entitlements', ['account_id', 'feature', 'revoked_at'], unique=False)
    op.create_table('experiment_assignments',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('experiment_key', sa.String(length=48), nullable=False),
    sa.Column('variant', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['experiment_key'], ['experiments.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'experiment_key', name='uq_experiment_assignment')
    )
    op.create_table('offers',
    sa.Column('key', sa.String(length=48), nullable=False),
    sa.Column('plan_key', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('amount_inr', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('period', sa.String(length=16), nullable=False),
    sa.Column('compare_at_inr', sa.Integer(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_key'], ['plans.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('payment_events',
    sa.Column('account_id', sa.Uuid(), nullable=True),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('provider_event_id', sa.String(length=120), nullable=False),
    sa.Column('raw_event_name', sa.String(length=64), server_default='', nullable=False),
    sa.Column('kind', sa.String(length=48), nullable=False),
    sa.Column('provider_order_id', sa.String(length=80), nullable=True),
    sa.Column('provider_payment_id', sa.String(length=80), nullable=True),
    sa.Column('provider_subscription_id', sa.String(length=80), nullable=True),
    sa.Column('provider_refund_id', sa.String(length=80), nullable=True),
    sa.Column('amount_inr', sa.Integer(), nullable=True),
    sa.Column('occurred_at_epoch', sa.Integer(), nullable=True),
    sa.Column('signature_verified', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('outcome', sa.String(length=32), server_default='processed', nullable=False),
    sa.Column('detail', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_event_id', name='uq_payment_event_provider_id')
    )
    op.create_index('ix_payment_events_account', 'payment_events', ['account_id', 'created_at'], unique=False)
    op.create_table('stylist_reviews',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('subject_type', sa.String(length=32), nullable=False),
    sa.Column('subject_id', sa.Uuid(), nullable=True),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='queued', nullable=False),
    sa.Column('reviewer_ref', sa.String(length=80), nullable=True),
    sa.Column('response', sa.Text(), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_stylist_reviews_account', 'stylist_reviews', ['account_id', 'status'], unique=False)
    op.create_table('subscriptions',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('plan_key', sa.String(length=32), nullable=False),
    sa.Column('offer_key', sa.String(length=48), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('provider_subscription_id', sa.String(length=80), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('current_period_start', sa.Date(), nullable=False),
    sa.Column('current_period_end', sa.Date(), nullable=False),
    sa.Column('grace_until', sa.Date(), nullable=True),
    sa.Column('cancel_at_period_end', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('renewal_failures', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_event_epoch', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_subscription_id', name='uq_subscription_provider_ref')
    )
    op.create_index('ix_subscriptions_account_status', 'subscriptions', ['account_id', 'status'], unique=False)
    op.create_table('support_cases',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=160), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
    sa.Column('severity', sa.String(length=16), server_default='normal', nullable=False),
    sa.Column('billing_snapshot', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_support_cases_account', 'support_cases', ['account_id', 'status'], unique=False)
    op.create_table('entitlement_usage',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('entitlement_id', sa.Uuid(), nullable=True),
    sa.Column('feature', sa.String(length=48), nullable=False),
    sa.Column('quantity', sa.Integer(), server_default='1', nullable=False),
    sa.Column('period_key', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=80), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['entitlement_id'], ['entitlements.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'feature', 'idempotency_key', name='uq_entitlement_usage_idempotency')
    )
    op.create_index('ix_entitlement_usage_account', 'entitlement_usage', ['account_id', 'feature', 'period_key'], unique=False)
    op.create_table('orders',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('offer_key', sa.String(length=48), nullable=False),
    sa.Column('plan_key', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('provider_order_id', sa.String(length=80), nullable=True),
    sa.Column('amount_inr', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='created', nullable=False),
    sa.Column('simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('occasion_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=80), nullable=True),
    sa.Column('notes', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['occasion_id'], ['occasions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'idempotency_key', name='uq_order_idempotency'),
    sa.UniqueConstraint('provider', 'provider_order_id', name='uq_order_provider_ref')
    )
    op.create_index('ix_orders_account_status', 'orders', ['account_id', 'status'], unique=False)
    op.create_table('virtual_tryon_jobs',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('provider', sa.String(length=32), server_default='unconfigured', nullable=False),
    sa.Column('provider_job_id', sa.String(length=80), nullable=True),
    sa.Column('status', sa.String(length=24), server_default='queued', nullable=False),
    sa.Column('source_media_id', sa.Uuid(), nullable=True),
    sa.Column('result_media_id', sa.Uuid(), nullable=True),
    sa.Column('item_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('estimated_cost_usd', sa.Float(), server_default='0', nullable=False),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['result_media_id'], ['media_assets.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_media_id'], ['media_assets.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tryon_jobs_account', 'virtual_tryon_jobs', ['account_id', 'status'], unique=False)
    op.create_table('refunds',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=True),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('provider_refund_id', sa.String(length=80), nullable=True),
    sa.Column('amount_inr', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(length=240), server_default='', nullable=False),
    sa.Column('entitlements_revoked', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_refund_id', name='uq_refund_provider_ref')
    )
    op.create_index('ix_refunds_account', 'refunds', ['account_id', 'created_at'], unique=False)

    _seed_catalogue()


def downgrade() -> None:
    op.drop_index('ix_refunds_account', table_name='refunds')
    op.drop_table('refunds')
    op.drop_index('ix_tryon_jobs_account', table_name='virtual_tryon_jobs')
    op.drop_table('virtual_tryon_jobs')
    op.drop_index('ix_orders_account_status', table_name='orders')
    op.drop_table('orders')
    op.drop_index('ix_entitlement_usage_account', table_name='entitlement_usage')
    op.drop_table('entitlement_usage')
    op.drop_index('ix_support_cases_account', table_name='support_cases')
    op.drop_table('support_cases')
    op.drop_index('ix_subscriptions_account_status', table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index('ix_stylist_reviews_account', table_name='stylist_reviews')
    op.drop_table('stylist_reviews')
    op.drop_index('ix_payment_events_account', table_name='payment_events')
    op.drop_table('payment_events')
    op.drop_table('offers')
    op.drop_table('experiment_assignments')
    op.drop_index('ix_entitlements_account_feature', table_name='entitlements')
    op.drop_table('entitlements')
    op.drop_index('ix_billing_audit_account', table_name='billing_audit_events')
    op.drop_table('billing_audit_events')
    op.drop_table('plans')
    op.drop_index('ix_model_evaluations_feature', table_name='model_evaluations')
    op.drop_table('model_evaluations')
    op.drop_index('ix_feature_cost_date', table_name='feature_cost_daily')
    op.drop_table('feature_cost_daily')
    op.drop_table('experiments')


# --- Seeding the catalogue ----------------------------------------------------


def _seed_catalogue() -> None:
    """Write the plans and offers into the database.

    Imported from ``catalogue.py`` rather than copied, so what the paywall
    shows and what the entitlement engine grants are the same definition. The
    amounts come from configuration, so a price change is an environment change.
    """
    from app.domains.billing import catalogue

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

    insert("plans", [{
        "id": str(uuid.uuid4()), "key": plan.key, "label": plan.label,
        "tagline": plan.tagline, "recurring": plan.recurring,
        "valid_days": plan.valid_days,
        "limits": json.dumps(plan.limits),
        "benefits": json.dumps([
            {"headline": row.headline, "detail": row.detail} for row in plan.benefits
        ]),
        "catalogue_version": catalogue.CATALOGUE_VERSION,
    } for plan in catalogue.PLANS], "key")

    insert("offers", [{
        "id": str(uuid.uuid4()), "key": offer.key, "plan_key": offer.plan_key,
        "label": offer.label, "amount_inr": offer.amount_inr, "currency": offer.currency,
        "period": offer.period, "compare_at_inr": offer.compare_at_inr, "active": True,
    } for offer in catalogue.offers()], "key")
