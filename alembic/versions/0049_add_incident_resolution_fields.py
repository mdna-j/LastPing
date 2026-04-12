"""add incident resolution fields

Revision ID: 0049_add_incident_resolution_fields
Revises: 0048_add_notification_delivery_queue
Create Date: 2026-04-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0049_add_incident_resolution_fields"
down_revision = "0048_add_notification_delivery_queue"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str):
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _columns(inspector, "incident")

    if "resolved_by" not in columns:
        op.add_column("incident", sa.Column("resolved_by", sa.String(), nullable=True))
    if "resolution_summary" not in columns:
        op.add_column("incident", sa.Column("resolution_summary", sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _columns(inspector, "incident")

    if "resolution_summary" in columns:
        op.drop_column("incident", "resolution_summary")
    if "resolved_by" in columns:
        op.drop_column("incident", "resolved_by")
