"""add user_usage table

Revision ID: 0016_add_user_usage
Revises: 0015_harden_incident_migration
Create Date: 2026-01-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0016_add_user_usage'
down_revision = '0015_harden_incident_migration'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('minute_start', sa.DateTime(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, default=0),
    )


def downgrade():
    op.drop_table('user_usage')
