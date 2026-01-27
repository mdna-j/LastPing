"""add incident grouping fields

Revision ID: 0017_add_incident_grouping
Revises: 0016_add_user_usage
Create Date: 2026-01-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '0017_add_incident_grouping'
down_revision = '0016_add_user_usage'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_cols = {col["name"] for col in inspector.get_columns("incident")}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("incident")}

    if "group_id" not in existing_cols:
        op.add_column('incident', sa.Column('group_id', sa.Integer(), sa.ForeignKey('incident.id'), nullable=True))
    if op.f('ix_incident_group_id') not in existing_indexes:
        op.create_index(op.f('ix_incident_group_id'), 'incident', ['group_id'], unique=False)

    if "merged_into" not in existing_cols:
        op.add_column('incident', sa.Column('merged_into', sa.Integer(), sa.ForeignKey('incident.id'), nullable=True))
    if op.f('ix_incident_merged_into') not in existing_indexes:
        op.create_index(op.f('ix_incident_merged_into'), 'incident', ['merged_into'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_incident_merged_into'), table_name='incident')
    op.drop_column('incident', 'merged_into')
    op.drop_index(op.f('ix_incident_group_id'), table_name='incident')
    op.drop_column('incident', 'group_id')
