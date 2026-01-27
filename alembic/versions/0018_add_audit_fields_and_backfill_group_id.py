"""add audit fields and backfill group_id

Revision ID: 0018_add_audit_fields_and_backfill_group_id
Revises: 0017_add_incident_grouping
Create Date: 2026-01-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '0018_add_audit_fields_and_backfill_group_id'
down_revision = '0017_add_incident_grouping'
branch_labels = None
depends_on = None


def upgrade():
    # add audit columns
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("audit_log")}
    if "actor_ip" not in existing_cols:
        op.add_column('audit_log', sa.Column('actor_ip', sa.String(), nullable=True))
    if "user_agent" not in existing_cols:
        op.add_column('audit_log', sa.Column('user_agent', sa.String(), nullable=True))

    # backfill group_id from merged_into where present
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE incident SET group_id = merged_into WHERE merged_into IS NOT NULL"))


def downgrade():
    op.drop_column('audit_log', 'user_agent')
    op.drop_column('audit_log', 'actor_ip')
