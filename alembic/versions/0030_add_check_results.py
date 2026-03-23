"""add canonical check execution evidence table

Revision ID: 0030_add_check_results
Revises: 0029_add_script_checks
Create Date: 2026-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0030_add_check_results"
down_revision = "0029_add_script_checks"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    indexes = {idx["name"] for idx in inspector.get_indexes("check_result")} if "check_result" in tables else set()

    if "check_result" not in tables:
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
        indexes = set()
    if "ix_check_result_check_id" not in indexes:
        op.create_index("ix_check_result_check_id", "check_result", ["check_id"])
    if "ix_check_result_project_id" not in indexes:
        op.create_index("ix_check_result_project_id", "check_result", ["project_id"])
    if "ix_check_result_incident_id" not in indexes:
        op.create_index("ix_check_result_incident_id", "check_result", ["incident_id"])
    if "ix_check_result_created_at" not in indexes:
        op.create_index("ix_check_result_created_at", "check_result", ["created_at"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "check_result" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("check_result")}
    if "ix_check_result_created_at" in indexes:
        op.drop_index("ix_check_result_created_at", table_name="check_result")
    if "ix_check_result_incident_id" in indexes:
        op.drop_index("ix_check_result_incident_id", table_name="check_result")
    if "ix_check_result_project_id" in indexes:
        op.drop_index("ix_check_result_project_id", table_name="check_result")
    if "ix_check_result_check_id" in indexes:
        op.drop_index("ix_check_result_check_id", table_name="check_result")
    op.drop_table("check_result")
