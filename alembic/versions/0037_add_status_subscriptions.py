"""add public status subscriptions

Revision ID: 0037_add_status_subscriptions
Revises: 0036_add_incident_workflow_fields
Create Date: 2026-03-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0037_add_status_subscriptions"
down_revision = "0036_add_incident_workflow_fields"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    indexes = {idx["name"] for idx in inspector.get_indexes("status_subscription")} if "status_subscription" in tables else set()

    if "status_subscription" not in tables:
        op.create_table(
            "status_subscription",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column("target", sa.String(length=1024), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        indexes = set()
    if "ix_status_subscription_project_id" not in indexes:
        op.create_index("ix_status_subscription_project_id", "status_subscription", ["project_id"], unique=False)
    if "ix_status_subscription_channel" not in indexes:
        op.create_index("ix_status_subscription_channel", "status_subscription", ["channel"], unique=False)
    if "ix_status_subscription_target" not in indexes:
        op.create_index("ix_status_subscription_target", "status_subscription", ["target"], unique=False)
    if "ix_status_subscription_active" not in indexes:
        op.create_index("ix_status_subscription_active", "status_subscription", ["active"], unique=False)
    if "ix_status_subscription_created_at" not in indexes:
        op.create_index("ix_status_subscription_created_at", "status_subscription", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "status_subscription" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("status_subscription")}
    if "ix_status_subscription_created_at" in indexes:
        op.drop_index("ix_status_subscription_created_at", table_name="status_subscription")
    if "ix_status_subscription_active" in indexes:
        op.drop_index("ix_status_subscription_active", table_name="status_subscription")
    if "ix_status_subscription_target" in indexes:
        op.drop_index("ix_status_subscription_target", table_name="status_subscription")
    if "ix_status_subscription_channel" in indexes:
        op.drop_index("ix_status_subscription_channel", table_name="status_subscription")
    if "ix_status_subscription_project_id" in indexes:
        op.drop_index("ix_status_subscription_project_id", table_name="status_subscription")
    op.drop_table("status_subscription")
