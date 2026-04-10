"""add browser check artifacts

Revision ID: 0046_add_browser_check_artifacts
Revises: 0045_add_webhook_receipts
Create Date: 2026-04-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0046_add_browser_check_artifacts"
down_revision = "0045_add_webhook_receipts"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "browser_check_artifact" not in _tables(inspector):
        op.create_table(
            "browser_check_artifact",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=False),
            sa.Column("check_result_id", sa.Integer(), nullable=True),
            sa.Column("incident_id", sa.Integer(), nullable=True),
            sa.Column("artifact_type", sa.String(), nullable=False),
            sa.Column("file_path", sa.String(), nullable=False),
            sa.Column("content_type", sa.String(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["check_id"], ["check.id"]),
            sa.ForeignKeyConstraint(["check_result_id"], ["check_result.id"]),
            sa.ForeignKeyConstraint(["incident_id"], ["incident.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = _indexes(inspector, "browser_check_artifact")
    if "ix_browser_check_artifact_project_id" not in indexes:
        op.create_index("ix_browser_check_artifact_project_id", "browser_check_artifact", ["project_id"], unique=False)
    if "ix_browser_check_artifact_check_id" not in indexes:
        op.create_index("ix_browser_check_artifact_check_id", "browser_check_artifact", ["check_id"], unique=False)
    if "ix_browser_check_artifact_check_result_id" not in indexes:
        op.create_index("ix_browser_check_artifact_check_result_id", "browser_check_artifact", ["check_result_id"], unique=False)
    if "ix_browser_check_artifact_incident_id" not in indexes:
        op.create_index("ix_browser_check_artifact_incident_id", "browser_check_artifact", ["incident_id"], unique=False)
    if "ix_browser_check_artifact_artifact_type" not in indexes:
        op.create_index("ix_browser_check_artifact_artifact_type", "browser_check_artifact", ["artifact_type"], unique=False)
    if "ix_browser_check_artifact_created_at" not in indexes:
        op.create_index("ix_browser_check_artifact_created_at", "browser_check_artifact", ["created_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "browser_check_artifact" not in _tables(inspector):
        return
    indexes = _indexes(inspector, "browser_check_artifact")
    for name in (
        "ix_browser_check_artifact_created_at",
        "ix_browser_check_artifact_artifact_type",
        "ix_browser_check_artifact_incident_id",
        "ix_browser_check_artifact_check_result_id",
        "ix_browser_check_artifact_check_id",
        "ix_browser_check_artifact_project_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="browser_check_artifact")
    op.drop_table("browser_check_artifact")
