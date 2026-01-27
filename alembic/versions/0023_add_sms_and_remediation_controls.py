"""add sms provider settings and remediation safety controls

Revision ID: 0023_add_sms_and_remediation_controls
Revises: 0022_add_check_escalation_policies
Create Date: 2026-01-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '0023_add_sms_and_remediation_controls'
down_revision = '0022_add_check_escalation_policies'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    project_cols = {col["name"] for col in inspector.get_columns("project")}
    hook_cols = {col["name"] for col in inspector.get_columns("remediation_hook")}

    if "sms_from" not in project_cols:
        op.add_column('project', sa.Column('sms_from', sa.String(), nullable=True))
    if "sms_provider" not in project_cols:
        op.add_column('project', sa.Column('sms_provider', sa.String(), nullable=True))
    if "sms_account_sid" not in project_cols:
        op.add_column('project', sa.Column('sms_account_sid', sa.String(), nullable=True))
    if "sms_auth_token" not in project_cols:
        op.add_column('project', sa.Column('sms_auth_token', sa.String(), nullable=True))

    if "require_secret" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('require_secret', sa.Boolean(), nullable=True))
    if "max_triggers_per_day" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('max_triggers_per_day', sa.Integer(), nullable=True))
    if "failure_count" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('failure_count', sa.Integer(), nullable=True))
    if "disable_on_failure_count" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('disable_on_failure_count', sa.Integer(), nullable=True))
    if "disabled_at" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('disabled_at', sa.DateTime(), nullable=True))
    if "disabled_reason" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('disabled_reason', sa.String(), nullable=True))
    if "allow_during_maintenance" not in hook_cols:
        op.add_column('remediation_hook', sa.Column('allow_during_maintenance', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('remediation_hook', 'allow_during_maintenance')
    op.drop_column('remediation_hook', 'disabled_reason')
    op.drop_column('remediation_hook', 'disabled_at')
    op.drop_column('remediation_hook', 'disable_on_failure_count')
    op.drop_column('remediation_hook', 'failure_count')
    op.drop_column('remediation_hook', 'max_triggers_per_day')
    op.drop_column('remediation_hook', 'require_secret')
    op.drop_column('project', 'sms_auth_token')
    op.drop_column('project', 'sms_account_sid')
    op.drop_column('project', 'sms_provider')
    op.drop_column('project', 'sms_from')
