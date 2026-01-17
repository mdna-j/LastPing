"""add incident grouping fields

Revision ID: 0017_add_incident_grouping
Revises: 0016_add_user_usage
Create Date: 2026-01-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0017_add_incident_grouping'
down_revision = '0016_add_user_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('incident', sa.Column('group_id', sa.Integer(), sa.ForeignKey('incident.id'), nullable=True))
    op.create_index(op.f('ix_incident_group_id'), 'incident', ['group_id'], unique=False)
    op.add_column('incident', sa.Column('merged_into', sa.Integer(), sa.ForeignKey('incident.id'), nullable=True))
    op.create_index(op.f('ix_incident_merged_into'), 'incident', ['merged_into'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_incident_merged_into'), table_name='incident')
    op.drop_column('incident', 'merged_into')
    op.drop_index(op.f('ix_incident_group_id'), table_name='incident')
    op.drop_column('incident', 'group_id')
