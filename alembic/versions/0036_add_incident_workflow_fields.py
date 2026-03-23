"""add incident workflow fields and notes

Revision ID: 0036_add_incident_workflow_fields
Revises: 0035_add_event_incident_run_keys
Create Date: 2026-03-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0036_add_incident_workflow_fields"
down_revision = "0035_add_event_incident_run_keys"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    incident_columns = {col["name"] for col in inspector.get_columns("incident")}

    if "owner" not in incident_columns:
        op.add_column("incident", sa.Column("owner", sa.String(length=255), nullable=True))
    if "acknowledged_at" not in incident_columns:
        op.add_column("incident", sa.Column("acknowledged_at", sa.DateTime(), nullable=True))
    if "acknowledged_by" not in incident_columns:
        op.add_column("incident", sa.Column("acknowledged_by", sa.String(length=255), nullable=True))
    if "silenced_until" not in incident_columns:
        op.add_column("incident", sa.Column("silenced_until", sa.DateTime(), nullable=True))
    if "silenced_by" not in incident_columns:
        op.add_column("incident", sa.Column("silenced_by", sa.String(length=255), nullable=True))

    tables = set(inspector.get_table_names())
    if "incident_note" not in tables:
        op.create_table(
            "incident_note",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("incident_id", sa.Integer(), sa.ForeignKey("incident.id"), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("author", sa.String(length=255), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_incident_note_incident_id", "incident_note", ["incident_id"], unique=False)
        op.create_index("ix_incident_note_project_id", "incident_note", ["project_id"], unique=False)
        op.create_index("ix_incident_note_created_at", "incident_note", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "incident_note" in tables:
        note_indexes = {idx["name"] for idx in inspector.get_indexes("incident_note")}
        if "ix_incident_note_created_at" in note_indexes:
            op.drop_index("ix_incident_note_created_at", table_name="incident_note")
        if "ix_incident_note_project_id" in note_indexes:
            op.drop_index("ix_incident_note_project_id", table_name="incident_note")
        if "ix_incident_note_incident_id" in note_indexes:
            op.drop_index("ix_incident_note_incident_id", table_name="incident_note")
        op.drop_table("incident_note")

    incident_columns = {col["name"] for col in inspector.get_columns("incident")}
    if "silenced_by" in incident_columns:
        op.drop_column("incident", "silenced_by")
    if "silenced_until" in incident_columns:
        op.drop_column("incident", "silenced_until")
    if "acknowledged_by" in incident_columns:
        op.drop_column("incident", "acknowledged_by")
    if "acknowledged_at" in incident_columns:
        op.drop_column("incident", "acknowledged_at")
    if "owner" in incident_columns:
        op.drop_column("incident", "owner")
