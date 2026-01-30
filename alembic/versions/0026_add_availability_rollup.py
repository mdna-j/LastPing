"""add availability rollup table

Revision ID: 0026_add_availability_rollup
Revises: 0025_add_remediation_approvals
Create Date: 2026-01-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0026_add_availability_rollup"
down_revision = "0025_add_remediation_approvals"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "availability_rollup" not in inspector.get_table_names():
        op.create_table(
            "availability_rollup",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=True),
            sa.Column("period_type", sa.String(), nullable=False),
            sa.Column("period", sa.String(), nullable=False),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("period_end", sa.DateTime(), nullable=False),
            sa.Column("uptime_percent", sa.Float(), nullable=False),
            sa.Column("slo_met", sa.Boolean(), nullable=True),
            sa.Column("sla_met", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["check_id"], ["check.id"]),
        )
        op.create_index("ix_availability_rollup_project_id", "availability_rollup", ["project_id"])
        op.create_index("ix_availability_rollup_check_id", "availability_rollup", ["check_id"])
        op.create_index("ix_availability_rollup_period_type", "availability_rollup", ["period_type"])
        op.create_index("ix_availability_rollup_period", "availability_rollup", ["period"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "availability_rollup" in inspector.get_table_names():
        op.drop_index("ix_availability_rollup_period", table_name="availability_rollup")
        op.drop_index("ix_availability_rollup_period_type", table_name="availability_rollup")
        op.drop_index("ix_availability_rollup_check_id", table_name="availability_rollup")
        op.drop_index("ix_availability_rollup_project_id", table_name="availability_rollup")
        op.drop_table("availability_rollup")
