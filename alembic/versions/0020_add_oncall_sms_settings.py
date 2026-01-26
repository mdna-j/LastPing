"""add oncall and sms settings

Revision ID: 0020_add_oncall_sms_settings
Revises: 0019_add_advanced_checks_and_slo
Create Date: 2026-01-25 22:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0020_add_oncall_sms_settings'
down_revision = '0019_add_advanced_checks_and_slo'
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
