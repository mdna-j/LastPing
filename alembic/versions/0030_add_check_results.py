"""add canonical check execution evidence table

Revision ID: 0030_add_check_results
Revises: 0029_add_script_checks
Create Date: 2026-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0030_add_check_results"
down_revision = "0029_add_script_checks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "check_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("check.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incident.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_check_result_check_id", "check_result", ["check_id"])
    op.create_index("ix_check_result_project_id", "check_result", ["project_id"])
    op.create_index("ix_check_result_incident_id", "check_result", ["incident_id"])
    op.create_index("ix_check_result_created_at", "check_result", ["created_at"])


def downgrade():
    op.drop_index("ix_check_result_created_at", table_name="check_result")
    op.drop_index("ix_check_result_incident_id", table_name="check_result")
    op.drop_index("ix_check_result_project_id", table_name="check_result")
    op.drop_index("ix_check_result_check_id", table_name="check_result")
    op.drop_table("check_result")

