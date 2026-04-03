"""add incident pagerduty dedup key

Revision ID: 0041_add_incident_pagerduty_dedup_key
Revises: 0040_add_slack_threading_fields
Create Date: 2026-04-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_add_incident_pagerduty_dedup_key"
down_revision = "0040_add_slack_threading_fields"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str):
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    incident_cols = _columns(inspector, "incident")
    if "pagerduty_dedup_key" not in incident_cols:
        op.add_column("incident", sa.Column("pagerduty_dedup_key", sa.String(length=255), nullable=True))

    inspector = sa.inspect(bind)
    if "ix_incident_pagerduty_dedup_key" not in _indexes(inspector, "incident"):
        op.create_index("ix_incident_pagerduty_dedup_key", "incident", ["pagerduty_dedup_key"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ix_incident_pagerduty_dedup_key" in _indexes(inspector, "incident"):
        op.drop_index("ix_incident_pagerduty_dedup_key", table_name="incident")

    inspector = sa.inspect(bind)
    incident_cols = _columns(inspector, "incident")
    if "pagerduty_dedup_key" in incident_cols:
        op.drop_column("incident", "pagerduty_dedup_key")
