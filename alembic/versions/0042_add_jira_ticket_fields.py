"""add jira ticket fields

Revision ID: 0042_add_jira_ticket_fields
Revises: 0041_add_incident_pagerduty_dedup_key
Create Date: 2026-04-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0042_add_jira_ticket_fields"
down_revision = "0041_add_incident_pagerduty_dedup_key"
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
    if "jira_base_url" not in project_cols:
        op.add_column("project", sa.Column("jira_base_url", sa.String(length=255), nullable=True))
    if "jira_user_email" not in project_cols:
        op.add_column("project", sa.Column("jira_user_email", sa.String(length=255), nullable=True))
    if "jira_api_token" not in project_cols:
        op.add_column("project", sa.Column("jira_api_token", sa.String(length=255), nullable=True))
    if "jira_project_key" not in project_cols:
        op.add_column("project", sa.Column("jira_project_key", sa.String(length=64), nullable=True))
    if "jira_issue_type" not in project_cols:
        op.add_column("project", sa.Column("jira_issue_type", sa.String(length=120), nullable=True))

    inspector = sa.inspect(bind)
    incident_cols = _columns(inspector, "incident")
    if "jira_issue_key" not in incident_cols:
        op.add_column("incident", sa.Column("jira_issue_key", sa.String(length=64), nullable=True))
    if "jira_issue_url" not in incident_cols:
        op.add_column("incident", sa.Column("jira_issue_url", sa.String(length=255), nullable=True))

    inspector = sa.inspect(bind)
    incident_indexes = _indexes(inspector, "incident")
    if "ix_incident_jira_issue_key" not in incident_indexes:
        op.create_index("ix_incident_jira_issue_key", "incident", ["jira_issue_key"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "ix_incident_jira_issue_key" in _indexes(inspector, "incident"):
        op.drop_index("ix_incident_jira_issue_key", table_name="incident")

    inspector = sa.inspect(bind)
    incident_cols = _columns(inspector, "incident")
    if "jira_issue_url" in incident_cols:
        op.drop_column("incident", "jira_issue_url")
        inspector = sa.inspect(bind)
        incident_cols = _columns(inspector, "incident")
    if "jira_issue_key" in incident_cols:
        op.drop_column("incident", "jira_issue_key")

    inspector = sa.inspect(bind)
    project_cols = _columns(inspector, "project")
    for name in [
        "jira_issue_type",
        "jira_project_key",
        "jira_api_token",
        "jira_user_email",
        "jira_base_url",
    ]:
        if name in project_cols:
            op.drop_column("project", name)
            inspector = sa.inspect(bind)
            project_cols = _columns(inspector, "project")
