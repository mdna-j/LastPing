"""add slack threading fields

Revision ID: 0040_add_slack_threading_fields
Revises: 0039_add_enterprise_rbac
Create Date: 2026-04-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_add_slack_threading_fields"
down_revision = "0039_add_enterprise_rbac"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str):
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    project_cols = _columns(inspector, "project")
    if "slack_channel" not in project_cols:
        op.add_column("project", sa.Column("slack_channel", sa.String(length=120), nullable=True))

    inspector = sa.inspect(bind)
    check_cols = _columns(inspector, "check")
    if "alert_slack_channel" not in check_cols:
        op.add_column("check", sa.Column("alert_slack_channel", sa.String(length=120), nullable=True))

    inspector = sa.inspect(bind)
    incident_cols = _columns(inspector, "incident")
    if "slack_thread_ts" not in incident_cols:
        op.add_column("incident", sa.Column("slack_thread_ts", sa.String(length=64), nullable=True))
    if "slack_channel_id" not in incident_cols:
        op.add_column("incident", sa.Column("slack_channel_id", sa.String(length=120), nullable=True))

    inspector = sa.inspect(bind)
    incident_indexes = _indexes(inspector, "incident")
    if "ix_incident_slack_thread_ts" not in incident_indexes:
        op.create_index("ix_incident_slack_thread_ts", "incident", ["slack_thread_ts"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ix_incident_slack_thread_ts" in _indexes(inspector, "incident"):
        op.drop_index("ix_incident_slack_thread_ts", table_name="incident")

    incident_cols = _columns(inspector, "incident")
    if "slack_channel_id" in incident_cols:
        op.drop_column("incident", "slack_channel_id")
        inspector = sa.inspect(bind)
        incident_cols = _columns(inspector, "incident")
    if "slack_thread_ts" in incident_cols:
        op.drop_column("incident", "slack_thread_ts")

    inspector = sa.inspect(bind)
    check_cols = _columns(inspector, "check")
    if "alert_slack_channel" in check_cols:
        op.drop_column("check", "alert_slack_channel")

    inspector = sa.inspect(bind)
    project_cols = _columns(inspector, "project")
    if "slack_channel" in project_cols:
        op.drop_column("project", "slack_channel")
