"""Routine and shelf intelligence.

Forward-only and purely additive. It creates the eighteen tables Phase 6 needs
and touches nothing migrations 0001-0005 created: no existing table is renamed,
altered or dropped, so an existing deployment upgrades without any data
movement.

Two kinds of table are created here and they are governed differently.

**Reviewed reference data** — ``ingredients``, ``ingredient_aliases``,
``ingredient_rules``, ``compatibility_rules``, ``contraindication_rules``,
``routine_templates``, ``perfume_context_rules`` and
``appearance_nutrition_rules`` — has no ``account_id`` and is *seeded here* from
``app.domains.routines.ontology`` and ``.nutrition``. Seeding from the modules
rather than from a copied literal is deliberate: the code and the rows cannot
drift apart, and every ``rule_id`` a warning carries is provably a row in one of
these tables. The seed is keyed on ``rule_id``/``key``, so re-running it is a
no-op rather than a duplicate.

**User data** — everything else — is account-scoped and starts empty.

Revision ID: 0006_routine_intelligence
Revises: 0005_today_engine_and_planner
"""
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0006_routine_intelligence'
down_revision: Union[str, None] = '0005_today_engine_and_planner'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('appearance_nutrition_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('nutrient_key', sa.String(length=48), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('appearance_context', sa.Text(), nullable=False),
    sa.Column('foods', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('note', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_table('compatibility_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('family_a', sa.String(length=48), nullable=False),
    sa.Column('family_b', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('headline', sa.String(length=200), nullable=False),
    sa.Column('guidance', sa.Text(), nullable=False),
    sa.Column('evidence_note', sa.Text(), server_default='', nullable=False),
    sa.Column('knowledge_version', sa.String(length=24), server_default='phase6-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_index('ix_compatibility_rules_families', 'compatibility_rules', ['family_a', 'family_b'], unique=False)
    op.create_table('contraindication_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.String(length=48), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('headline', sa.String(length=200), nullable=False),
    sa.Column('guidance', sa.Text(), nullable=False),
    sa.Column('evidence_note', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_table('ingredients',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=False),
    sa.Column('inci_name', sa.String(length=160), nullable=True),
    sa.Column('family', sa.String(length=48), nullable=False),
    sa.Column('summary', sa.Text(), server_default='', nullable=False),
    sa.Column('common_use', sa.Text(), server_default='', nullable=False),
    sa.Column('knowledge_version', sa.String(length=24), server_default='phase6-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_index('ix_ingredients_family', 'ingredients', ['family'], unique=False)
    op.create_table('perfume_context_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('factor', sa.String(length=32), nullable=False),
    sa.Column('match_value', sa.String(length=48), nullable=False),
    sa.Column('families', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_table('routine_templates',
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('frequency', sa.String(length=80), nullable=False),
    sa.Column('slots', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('knowledge_version', sa.String(length=24), server_default='phase6-v1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind')
    )
    op.create_table('hydration_preferences',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('remind_in_hot_weather_only', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id')
    )
    op.create_table('ingredient_aliases',
    sa.Column('ingredient_key', sa.String(length=64), nullable=False),
    sa.Column('alias', sa.String(length=160), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ingredient_key'], ['ingredients.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('alias', name='uq_ingredient_alias')
    )
    op.create_index('ix_ingredient_aliases_key', 'ingredient_aliases', ['ingredient_key'], unique=False)
    op.create_table('ingredient_rules',
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('ingredient_key', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), server_default='info', nullable=False),
    sa.Column('headline', sa.String(length=200), nullable=False),
    sa.Column('guidance', sa.Text(), nullable=False),
    sa.Column('evidence_note', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ingredient_key'], ['ingredients.key'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('rule_id')
    )
    op.create_table('nutrition_preferences',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('diet', sa.String(length=32), server_default='non_vegetarian', nullable=False),
    sa.Column('avoid_foods', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('focus_nutrients', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id')
    )
    op.create_table('routines',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('frequency', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('climate', sa.String(length=24), nullable=True),
    sa.Column('engine_version', sa.String(length=24), server_default='phase6-v1', nullable=False),
    sa.Column('explanation_source', sa.String(length=24), server_default='deterministic', nullable=False),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('climate_notes', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('skipped_for_allergy', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'kind', name='uq_routine_account_kind')
    )
    op.create_index('ix_routines_account', 'routines', ['account_id', 'status'], unique=False)
    op.create_table('routine_recommendation_runs',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='succeeded', nullable=False),
    sa.Column('engine_version', sa.String(length=24), server_default='phase6-v1', nullable=False),
    sa.Column('explanation_source', sa.String(length=24), server_default='deterministic', nullable=False),
    sa.Column('ai_run_id', sa.Uuid(), nullable=True),
    sa.Column('products_considered', sa.Integer(), server_default='0', nullable=False),
    sa.Column('routines_built', sa.Integer(), server_default='0', nullable=False),
    sa.Column('warnings_raised', sa.Integer(), server_default='0', nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ai_run_id'], ['ai_runs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_routine_runs_account', 'routine_recommendation_runs', ['account_id', 'created_at'], unique=False)
    op.create_table('product_expiry_events',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('rule_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('effective_expiry', sa.Date(), nullable=True),
    sa.Column('days_to_expiry', sa.Integer(), nullable=True),
    sa.Column('detail', sa.Text(), server_default='', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_product_expiry_account_item', 'product_expiry_events', ['account_id', 'item_id', 'created_at'], unique=False)
    op.create_table('product_ingredients',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('ingredient_key', sa.String(length=64), nullable=False),
    sa.Column('matched_text', sa.String(length=160), server_default='', nullable=False),
    sa.Column('position', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), server_default='1', nullable=False),
    sa.Column('source', sa.String(length=32), server_default='user_declared', nullable=False),
    sa.Column('needs_confirmation', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['ingredient_key'], ['ingredients.key'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'ingredient_key', name='uq_product_ingredient')
    )
    op.create_index('ix_product_ingredients_account', 'product_ingredients', ['account_id', 'ingredient_key'], unique=False)
    op.create_table('routine_steps',
    sa.Column('routine_id', sa.Uuid(), nullable=False),
    sa.Column('slot', sa.String(length=32), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('required', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('why', sa.Text(), nullable=False),
    sa.Column('frequency', sa.String(length=80), server_default='', nullable=False),
    sa.Column('inventory_item_id', sa.Uuid(), nullable=True),
    sa.Column('product_name', sa.String(length=200), nullable=True),
    sa.Column('safety_note', sa.Text(), server_default='', nullable=False),
    sa.Column('alternative', sa.Text(), server_default='', nullable=False),
    sa.Column('climate_note', sa.Text(), server_default='', nullable=False),
    sa.Column('is_gap', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['routine_id'], ['routines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_routine_steps_routine', 'routine_steps', ['routine_id', 'position'], unique=False)
    op.create_table('supplement_safety_flags',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=False),
    sa.Column('flag', sa.String(length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('item_id', 'flag', name='uq_supplement_flag')
    )
    op.create_index('ix_supplement_flags_account', 'supplement_safety_flags', ['account_id', 'flag'], unique=False)
    op.create_table('user_reported_observations',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('observed_on', sa.Date(), nullable=False),
    sa.Column('area', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=False),
    sa.Column('item_id', sa.Uuid(), nullable=True),
    sa.Column('routed_to_professional', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['inventory_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_observations_account_date', 'user_reported_observations', ['account_id', 'observed_on'], unique=False)
    op.create_table('routine_adherence',
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('routine_id', sa.Uuid(), nullable=False),
    sa.Column('step_id', sa.Uuid(), nullable=False),
    sa.Column('done_on', sa.Date(), nullable=False),
    sa.Column('completed', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('note', sa.String(length=240), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['account_links.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['routine_id'], ['routines.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['step_id'], ['routine_steps.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('step_id', 'done_on', name='uq_routine_adherence_step_day')
    )
    op.create_index('ix_routine_adherence_account_date', 'routine_adherence', ['account_id', 'done_on'], unique=False)

    _seed_reference_data()


def downgrade() -> None:
    op.drop_index('ix_routine_adherence_account_date', table_name='routine_adherence')
    op.drop_table('routine_adherence')
    op.drop_index('ix_user_observations_account_date', table_name='user_reported_observations')
    op.drop_table('user_reported_observations')
    op.drop_index('ix_supplement_flags_account', table_name='supplement_safety_flags')
    op.drop_table('supplement_safety_flags')
    op.drop_index('ix_routine_steps_routine', table_name='routine_steps')
    op.drop_table('routine_steps')
    op.drop_index('ix_product_ingredients_account', table_name='product_ingredients')
    op.drop_table('product_ingredients')
    op.drop_index('ix_product_expiry_account_item', table_name='product_expiry_events')
    op.drop_table('product_expiry_events')
    op.drop_index('ix_routine_runs_account', table_name='routine_recommendation_runs')
    op.drop_table('routine_recommendation_runs')
    op.drop_index('ix_routines_account', table_name='routines')
    op.drop_table('routines')
    op.drop_table('nutrition_preferences')
    op.drop_table('ingredient_rules')
    op.drop_index('ix_ingredient_aliases_key', table_name='ingredient_aliases')
    op.drop_table('ingredient_aliases')
    op.drop_table('hydration_preferences')
    op.drop_table('routine_templates')
    op.drop_table('perfume_context_rules')
    op.drop_index('ix_ingredients_family', table_name='ingredients')
    op.drop_table('ingredients')
    op.drop_table('contraindication_rules')
    op.drop_index('ix_compatibility_rules_families', table_name='compatibility_rules')
    op.drop_table('compatibility_rules')
    op.drop_table('appearance_nutrition_rules')


# --- Seeding the reviewed reference data ------------------------------------


def _seed_reference_data() -> None:
    """Write the reviewed knowledge into the database.

    Imported from the domain modules rather than copied, so a rule the engine
    can raise and a rule row a user can be shown are the same rule by
    construction. ``ON CONFLICT DO NOTHING`` on the natural key makes this safe
    to run against a database that already has some of it.
    """
    from app.domains.routines import nutrition, ontology
    from app.domains.routines.compiler import ROUTINE_FREQUENCY, ROUTINE_KINDS, ROUTINE_LABELS, ROUTINE_SLOTS
    from app.domains.routines.rules import ENGINE_RULES, RULE_ALLERGY, RULE_EXPIRED

    version = ontology.ONTOLOGY_VERSION
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

    insert("ingredients", [{
        "id": str(uuid.uuid4()), "key": row.key, "display_name": row.display_name,
        "inci_name": row.inci_name, "family": row.family, "summary": row.summary,
        "common_use": row.common_use, "knowledge_version": version,
    } for row in ontology.INGREDIENTS], "key")

    insert("ingredient_aliases", [{
        "id": str(uuid.uuid4()), "ingredient_key": key, "alias": alias,
    } for alias, key in sorted(ontology.alias_index().items())], "alias")

    # One plain note per ingredient, so "what is this?" always has an answer
    # that came from a reviewed row rather than from a model.
    insert("ingredient_rules", [{
        "id": str(uuid.uuid4()), "rule_id": f"ingredient.{row.key}", "ingredient_key": row.key,
        "severity": ontology.SEVERITY_INFO,
        "headline": f"About {row.display_name}",
        "guidance": row.summary,
        "evidence_note": row.common_use,
    } for row in ontology.INGREDIENTS], "rule_id")

    insert("compatibility_rules", [{
        "id": str(uuid.uuid4()), "rule_id": row.rule_id, "family_a": row.family_a,
        "family_b": row.family_b, "severity": row.severity, "headline": row.headline,
        "guidance": row.guidance, "evidence_note": row.evidence_note,
        "knowledge_version": version,
    } for row in ontology.COMPATIBILITY_RULES], "rule_id")

    # Only two things ever remove a product from a routine, and neither is a
    # clinical judgement: the user's own allergy list, and a date in the past.
    insert("contraindication_rules", [
        {
            "id": str(uuid.uuid4()), "rule_id": RULE_ALLERGY, "trigger": "user_allergy",
            "severity": ontology.SEVERITY_AVOID,
            "headline": "An ingredient you told us to avoid",
            "guidance": ENGINE_RULES[RULE_ALLERGY],
            "evidence_note": "Matched against the allergies you entered on your profile.",
        },
        {
            "id": str(uuid.uuid4()), "rule_id": RULE_EXPIRED, "trigger": "expired",
            "severity": ontology.SEVERITY_CAUTION,
            "headline": "Past the date you recorded",
            "guidance": ENGINE_RULES[RULE_EXPIRED],
            "evidence_note": "Computed from the expiry date, or the opened date plus period-after-opening, that you recorded.",
        },
    ], "rule_id")

    insert("routine_templates", [{
        "id": str(uuid.uuid4()), "kind": kind, "label": ROUTINE_LABELS[kind],
        "frequency": ROUTINE_FREQUENCY[kind],
        "slots": json.dumps(ROUTINE_SLOTS[kind]), "knowledge_version": version,
    } for kind in ROUTINE_KINDS], "kind")

    insert("perfume_context_rules", [{
        "id": str(uuid.uuid4()), "rule_id": row.rule_id, "factor": row.factor,
        "match_value": row.match, "families": json.dumps(list(row.families)), "note": row.note,
    } for row in ontology.PERFUME_RULES], "rule_id")

    insert("appearance_nutrition_rules", [{
        "id": str(uuid.uuid4()), "rule_id": row.rule_id, "nutrient_key": row.key,
        "display_name": row.display_name, "appearance_context": row.appearance_context,
        "foods": json.dumps([
            {"name": food.name, "tags": sorted(food.tags)} for food in row.foods
        ]),
        "note": row.note,
    } for row in nutrition.NUTRIENT_RULES], "rule_id")
