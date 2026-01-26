"""add oncall and sms settings

Revision ID: d2f1a3b4c5d6
Revises: 95c78d065a98
Create Date: 2026-01-25 22:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2f1a3b4c5d6'
down_revision = '95c78d065a98'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('project', sa.Column('sms_enabled', sa.Boolean(), nullable=True))
    op.add_column('project', sa.Column('sms_to', sa.String(), nullable=True))
    op.add_column('project', sa.Column('oncall_enabled', sa.Boolean(), nullable=True))
    op.add_column('project', sa.Column('oncall_email', sa.String(), nullable=True))


def downgrade():
    op.drop_column('project', 'oncall_email')
    op.drop_column('project', 'oncall_enabled')
    op.drop_column('project', 'sms_to')
    op.drop_column('project', 'sms_enabled')
