"""add check escalation policies

Revision ID: 0022_add_check_escalation_policies
Revises: 0021_add_oncall_remediation_and_leases
Create Date: 2026-01-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '0022_add_check_escalation_policies'
down_revision = '0021_add_oncall_remediation_and_leases'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    check_cols = {col["name"] for col in inspector.get_columns("check")}

    if "escalation_after_minutes" not in check_cols:
        op.add_column('check', sa.Column('escalation_after_minutes', sa.Integer(), nullable=True))
    if "escalation_cooldown_seconds" not in check_cols:
        op.add_column('check', sa.Column('escalation_cooldown_seconds', sa.Integer(), nullable=True))
    if "last_escalated_at" not in check_cols:
        op.add_column('check', sa.Column('last_escalated_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('check', 'last_escalated_at')
    op.drop_column('check', 'escalation_cooldown_seconds')
    op.drop_column('check', 'escalation_after_minutes')
