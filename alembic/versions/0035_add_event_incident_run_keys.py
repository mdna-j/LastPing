"""add event/incident run_key idempotency columns

Revision ID: 0035_add_event_incident_run_keys
Revises: 0034_add_check_result_run_key
Create Date: 2026-03-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0035_add_event_incident_run_keys"
down_revision = "0034_add_check_result_run_key"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    event_columns = {col["name"] for col in inspector.get_columns("event")}
    event_indexes = {idx["name"] for idx in inspector.get_indexes("event")}
    incident_columns = {col["name"] for col in inspector.get_columns("incident")}
    incident_indexes = {idx["name"] for idx in inspector.get_indexes("incident")}

    if "run_key" not in event_columns:
        op.add_column("event", sa.Column("run_key", sa.String(length=160), nullable=True))
    if "ix_event_run_key" not in event_indexes:
        op.create_index("ix_event_run_key", "event", ["run_key"], unique=False)
    if "ux_event_check_id_event_type_run_key" not in event_indexes:
        op.create_index(
            "ux_event_check_id_event_type_run_key",
            "event",
            ["check_id", "event_type", "run_key"],
            unique=True,
        )

    if "open_run_key" not in incident_columns:
        op.add_column("incident", sa.Column("open_run_key", sa.String(length=160), nullable=True))
    if "resolve_run_key" not in incident_columns:
        op.add_column("incident", sa.Column("resolve_run_key", sa.String(length=160), nullable=True))
    if "ix_incident_open_run_key" not in incident_indexes:
        op.create_index("ix_incident_open_run_key", "incident", ["open_run_key"], unique=False)
    if "ix_incident_resolve_run_key" not in incident_indexes:
        op.create_index("ix_incident_resolve_run_key", "incident", ["resolve_run_key"], unique=False)
    if "ux_incident_check_id_open_run_key" not in incident_indexes:
        op.create_index(
            "ux_incident_check_id_open_run_key",
            "incident",
            ["check_id", "open_run_key"],
            unique=True,
        )
    if "ux_incident_check_id_resolve_run_key" not in incident_indexes:
        op.create_index(
            "ux_incident_check_id_resolve_run_key",
            "incident",
            ["check_id", "resolve_run_key"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    event_columns = {col["name"] for col in inspector.get_columns("event")}
    event_indexes = {idx["name"] for idx in inspector.get_indexes("event")}
    incident_columns = {col["name"] for col in inspector.get_columns("incident")}
    incident_indexes = {idx["name"] for idx in inspector.get_indexes("incident")}

    if "ux_event_check_id_event_type_run_key" in event_indexes:
        op.drop_index("ux_event_check_id_event_type_run_key", table_name="event")
    if "ix_event_run_key" in event_indexes:
        op.drop_index("ix_event_run_key", table_name="event")
    if "run_key" in event_columns:
        op.drop_column("event", "run_key")

    if "ux_incident_check_id_resolve_run_key" in incident_indexes:
        op.drop_index("ux_incident_check_id_resolve_run_key", table_name="incident")
    if "ux_incident_check_id_open_run_key" in incident_indexes:
        op.drop_index("ux_incident_check_id_open_run_key", table_name="incident")
    if "ix_incident_resolve_run_key" in incident_indexes:
        op.drop_index("ix_incident_resolve_run_key", table_name="incident")
    if "ix_incident_open_run_key" in incident_indexes:
        op.drop_index("ix_incident_open_run_key", table_name="incident")
    if "resolve_run_key" in incident_columns:
        op.drop_column("incident", "resolve_run_key")
    if "open_run_key" in incident_columns:
        op.drop_column("incident", "open_run_key")
