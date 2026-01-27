"""add oncall, remediation, and lease tables

Revision ID: 0021_add_oncall_remediation_and_leases
Revises: 0020_add_oncall_sms_settings
Create Date: 2026-01-26 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '0021_add_oncall_remediation_and_leases'
down_revision = '0020_add_oncall_sms_settings'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if 'check_lease' not in existing:
        op.create_table(
            'check_lease',
            sa.Column('check_id', sa.Integer(), primary_key=True),
            sa.Column('lease_owner', sa.String(), nullable=True),
            sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['check_id'], ['check.id']),
        )
    if 'remediation_hook' not in existing:
        op.create_table(
            'remediation_hook',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('check_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(), nullable=False),
            sa.Column('url', sa.String(), nullable=False),
            sa.Column('method', sa.String(), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.Column('cooldown_seconds', sa.Integer(), nullable=False),
            sa.Column('last_triggered_at', sa.DateTime(), nullable=True),
            sa.Column('secret', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['project_id'], ['project.id']),
            sa.ForeignKeyConstraint(['check_id'], ['check.id']),
        )
    if 'remediation_log' not in existing:
        op.create_table(
            'remediation_log',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('hook_id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('check_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(), nullable=False),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('response_code', sa.Integer(), nullable=True),
            sa.Column('message', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['hook_id'], ['remediation_hook.id']),
            sa.ForeignKeyConstraint(['project_id'], ['project.id']),
            sa.ForeignKeyConstraint(['check_id'], ['check.id']),
        )
    if 'oncall_rotation' not in existing:
        op.create_table(
            'oncall_rotation',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('interval_minutes', sa.Integer(), nullable=False),
            sa.Column('start_at', sa.DateTime(), nullable=False),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(['project_id'], ['project.id']),
        )
    if 'oncall_member' not in existing:
        op.create_table(
            'oncall_member',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('rotation_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('email', sa.String(), nullable=True),
            sa.Column('phone', sa.String(), nullable=True),
            sa.Column('order', sa.Integer(), nullable=False),
            sa.Column('active', sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(['rotation_id'], ['oncall_rotation.id']),
        )
    if 'oncall_escalation' not in existing:
        op.create_table(
            'oncall_escalation',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('level', sa.Integer(), nullable=False),
            sa.Column('delay_minutes', sa.Integer(), nullable=False),
            sa.Column('target_type', sa.String(), nullable=False),
            sa.Column('rotation_id', sa.Integer(), nullable=True),
            sa.Column('target_value', sa.String(), nullable=True),
            sa.Column('enabled', sa.Boolean(), nullable=False),
            sa.ForeignKeyConstraint(['project_id'], ['project.id']),
            sa.ForeignKeyConstraint(['rotation_id'], ['oncall_rotation.id']),
        )
    if 'oncall_alert' not in existing:
        op.create_table(
            'oncall_alert',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('check_id', sa.Integer(), nullable=False),
            sa.Column('event_type', sa.String(), nullable=False),
            sa.Column('message', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_notified_at', sa.DateTime(), nullable=True),
            sa.Column('escalation_level', sa.Integer(), nullable=False),
            sa.Column('next_escalation_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['project_id'], ['project.id']),
            sa.ForeignKeyConstraint(['check_id'], ['check.id']),
        )


def downgrade():
    op.drop_table('oncall_alert')
    op.drop_table('oncall_escalation')
    op.drop_table('oncall_member')
    op.drop_table('oncall_rotation')
    op.drop_table('remediation_log')
    op.drop_table('remediation_hook')
    op.drop_table('check_lease')
