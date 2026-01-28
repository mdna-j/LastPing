"""add remediation approvals

Revision ID: 0025_add_remediation_approvals
Revises: 0024_add_check_alert_routing_and_oncall_check_id
Create Date: 2026-01-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0025_add_remediation_approvals"
down_revision = "0024_add_check_alert_routing_and_oncall_check_id"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    hook_cols = {col["name"] for col in inspector.get_columns("remediation_hook")}

    if "require_approval" not in hook_cols:
        op.add_column("remediation_hook", sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.false()))

    if "remediation_approval" not in inspector.get_table_names():
        op.create_table(
            "remediation_approval",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("hook_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("reason", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decided_by", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.Column("execution_status", sa.String(), nullable=True),
            sa.Column("execution_message", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["hook_id"], ["remediation_hook.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["check_id"], ["check.id"]),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "remediation_approval" in inspector.get_table_names():
        op.drop_table("remediation_approval")
    hook_cols = {col["name"] for col in inspector.get_columns("remediation_hook")}
    if "require_approval" in hook_cols:
        op.drop_column("remediation_hook", "require_approval")
