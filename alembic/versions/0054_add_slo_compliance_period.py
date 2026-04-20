"""add slo compliance period

Revision ID: 0054_add_slo_compliance_period
Revises: 0053_add_browser_check_secrets
Create Date: 2026-04-20 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0054_add_slo_compliance_period"
down_revision = "0053_add_browser_check_secrets"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "slo_compliance_period" not in tables:
        op.create_table(
            "slo_compliance_period",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=True),
            sa.Column("period_type", sa.String(), nullable=False),
            sa.Column("period", sa.String(), nullable=False),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("period_end", sa.DateTime(), nullable=False),
            sa.Column("slo_target", sa.Float(), nullable=True),
            sa.Column("sla_target", sa.Float(), nullable=True),
            sa.Column("uptime_percent", sa.Float(), nullable=False),
            sa.Column("error_budget_percent", sa.Float(), nullable=True),
            sa.Column("error_rate_percent", sa.Float(), nullable=True),
            sa.Column("budget_seconds", sa.Float(), nullable=True),
            sa.Column("consumed_seconds", sa.Float(), nullable=True),
            sa.Column("remaining_seconds", sa.Float(), nullable=True),
            sa.Column("consumed_percent", sa.Float(), nullable=True),
            sa.Column("remaining_percent", sa.Float(), nullable=True),
            sa.Column("slo_met", sa.Boolean(), nullable=True),
            sa.Column("sla_met", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["check_id"], ["check.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "project_id",
                "check_id",
                "period_type",
                "period",
                name="uq_slo_compliance_period_scope",
            ),
        )

    indexes = (
        {index["name"] for index in inspector.get_indexes("slo_compliance_period")}
        if "slo_compliance_period" in set(sa.inspect(bind).get_table_names())
        else set()
    )
    for index_name, columns in (
        ("ix_slo_compliance_period_project_id", ["project_id"]),
        ("ix_slo_compliance_period_check_id", ["check_id"]),
        ("ix_slo_compliance_period_period_type", ["period_type"]),
        ("ix_slo_compliance_period_period", ["period"]),
        ("ix_slo_compliance_period_period_start", ["period_start"]),
        ("ix_slo_compliance_period_period_end", ["period_end"]),
        ("ix_slo_compliance_period_created_at", ["created_at"]),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "slo_compliance_period", columns, unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "slo_compliance_period" not in tables:
        return

    indexes = {index["name"] for index in inspector.get_indexes("slo_compliance_period")}
    for index_name in (
        "ix_slo_compliance_period_created_at",
        "ix_slo_compliance_period_period_end",
        "ix_slo_compliance_period_period_start",
        "ix_slo_compliance_period_period",
        "ix_slo_compliance_period_period_type",
        "ix_slo_compliance_period_check_id",
        "ix_slo_compliance_period_project_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="slo_compliance_period")
    op.drop_table("slo_compliance_period")
