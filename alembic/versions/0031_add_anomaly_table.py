"""add anomaly table

Revision ID: 0031_add_anomaly_table
Revises: 0030_add_check_results
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0031_add_anomaly_table"
down_revision = "0030_add_check_results"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    indexes = {idx["name"] for idx in inspector.get_indexes("anomaly")} if "anomaly" in tables else set()

    if "anomaly" not in tables:
        op.create_table(
            "anomaly",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("check_id", sa.Integer(), sa.ForeignKey("check.id"), nullable=False),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incident.id"), nullable=True),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("severity", sa.Float(), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=False),
            sa.Column("window_end", sa.DateTime(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        indexes = set()
    if "ix_anomaly_check_id" not in indexes:
        op.create_index("ix_anomaly_check_id", "anomaly", ["check_id"])
    if "ix_anomaly_incident_id" not in indexes:
        op.create_index("ix_anomaly_incident_id", "anomaly", ["incident_id"])
    if "ix_anomaly_type" not in indexes:
        op.create_index("ix_anomaly_type", "anomaly", ["type"])
    if "ix_anomaly_created_at" not in indexes:
        op.create_index("ix_anomaly_created_at", "anomaly", ["created_at"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "anomaly" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("anomaly")}
    if "ix_anomaly_created_at" in indexes:
        op.drop_index("ix_anomaly_created_at", table_name="anomaly")
    if "ix_anomaly_type" in indexes:
        op.drop_index("ix_anomaly_type", table_name="anomaly")
    if "ix_anomaly_incident_id" in indexes:
        op.drop_index("ix_anomaly_incident_id", table_name="anomaly")
    if "ix_anomaly_check_id" in indexes:
        op.drop_index("ix_anomaly_check_id", table_name="anomaly")
    op.drop_table("anomaly")
