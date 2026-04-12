"""add notification delivery queue

Revision ID: 0048_add_notification_delivery_queue
Revises: 0047_add_secret_lifecycle_fields
Create Date: 2026-04-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0048_add_notification_delivery_queue"
down_revision = "0047_add_secret_lifecycle_fields"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "notification_delivery" not in _tables(inspector):
        op.create_table(
            "notification_delivery",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=True),
            sa.Column("incident_id", sa.Integer(), nullable=True),
            sa.Column("subscription_id", sa.Integer(), nullable=True),
            sa.Column("channel", sa.String(), nullable=False),
            sa.Column("event", sa.String(), nullable=False),
            sa.Column("request_kind", sa.String(), nullable=False),
            sa.Column("target", sa.String(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column("max_attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
            sa.Column("claimed_by", sa.String(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_status_code", sa.Integer(), nullable=True),
            sa.Column("delivered_at", sa.DateTime(), nullable=True),
            sa.Column("dead_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["check_id"], ["check.id"]),
            sa.ForeignKeyConstraint(["incident_id"], ["incident.id"]),
            sa.ForeignKeyConstraint(["subscription_id"], ["status_subscription.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    if "notification_delivery" in _tables(inspector):
        indexes = _indexes(inspector, "notification_delivery")
        for name, columns in (
            ("ix_notification_delivery_project_id", ["project_id"]),
            ("ix_notification_delivery_check_id", ["check_id"]),
            ("ix_notification_delivery_incident_id", ["incident_id"]),
            ("ix_notification_delivery_subscription_id", ["subscription_id"]),
            ("ix_notification_delivery_channel", ["channel"]),
            ("ix_notification_delivery_event", ["event"]),
            ("ix_notification_delivery_request_kind", ["request_kind"]),
            ("ix_notification_delivery_status", ["status"]),
            ("ix_notification_delivery_next_attempt_at", ["next_attempt_at"]),
            ("ix_notification_delivery_claimed_by", ["claimed_by"]),
            ("ix_notification_delivery_claimed_at", ["claimed_at"]),
            ("ix_notification_delivery_delivered_at", ["delivered_at"]),
            ("ix_notification_delivery_dead_at", ["dead_at"]),
            ("ix_notification_delivery_created_at", ["created_at"]),
            ("ix_notification_delivery_updated_at", ["updated_at"]),
        ):
            if name not in indexes:
                op.create_index(name, "notification_delivery", columns, unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "notification_delivery" in _tables(inspector):
        indexes = _indexes(inspector, "notification_delivery")
        for name in (
            "ix_notification_delivery_updated_at",
            "ix_notification_delivery_created_at",
            "ix_notification_delivery_dead_at",
            "ix_notification_delivery_delivered_at",
            "ix_notification_delivery_claimed_at",
            "ix_notification_delivery_claimed_by",
            "ix_notification_delivery_next_attempt_at",
            "ix_notification_delivery_status",
            "ix_notification_delivery_request_kind",
            "ix_notification_delivery_event",
            "ix_notification_delivery_channel",
            "ix_notification_delivery_subscription_id",
            "ix_notification_delivery_incident_id",
            "ix_notification_delivery_check_id",
            "ix_notification_delivery_project_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="notification_delivery")
        op.drop_table("notification_delivery")
