"""add api key and project secret lifecycle fields

Revision ID: 0047_add_secret_lifecycle_fields
Revises: 0046_add_browser_check_artifacts
Create Date: 2026-04-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0047_add_secret_lifecycle_fields"
down_revision = "0046_add_browser_check_artifacts"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def _columns(inspector, table_name: str):
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "api_key" in _tables(inspector):
        existing = _columns(inspector, "api_key")
        if "last_used_at" not in existing:
            op.add_column("api_key", sa.Column("last_used_at", sa.DateTime(), nullable=True))
        if "expires_at" not in existing:
            op.add_column("api_key", sa.Column("expires_at", sa.DateTime(), nullable=True))
        if "last_rotated_at" not in existing:
            op.add_column("api_key", sa.Column("last_rotated_at", sa.DateTime(), nullable=True))
        if "rotation_interval_days" not in existing:
            op.add_column("api_key", sa.Column("rotation_interval_days", sa.Integer(), nullable=True))
        if "replaced_by_api_key_id" not in existing:
            op.add_column("api_key", sa.Column("replaced_by_api_key_id", sa.Integer(), nullable=True))

        api_key = sa.table(
            "api_key",
            sa.column("id", sa.Integer),
            sa.column("created_at", sa.DateTime),
            sa.column("last_rotated_at", sa.DateTime),
        )
        bind.execute(
            api_key.update()
            .where(api_key.c.last_rotated_at.is_(None))
            .values(last_rotated_at=api_key.c.created_at)
        )

        inspector = sa.inspect(bind)
        indexes = _indexes(inspector, "api_key")
        if "ix_api_key_last_used_at" not in indexes:
            op.create_index("ix_api_key_last_used_at", "api_key", ["last_used_at"], unique=False)
        if "ix_api_key_expires_at" not in indexes:
            op.create_index("ix_api_key_expires_at", "api_key", ["expires_at"], unique=False)
        if "ix_api_key_replaced_by_api_key_id" not in indexes:
            op.create_index("ix_api_key_replaced_by_api_key_id", "api_key", ["replaced_by_api_key_id"], unique=False)

    inspector = sa.inspect(bind)
    if "project_secret_lifecycle" not in _tables(inspector):
        op.create_table(
            "project_secret_lifecycle",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("secret_name", sa.String(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_rotated_at", sa.DateTime(), nullable=True),
            sa.Column("rotation_interval_days", sa.Integer(), nullable=True),
            sa.Column("previous_secret_value", sa.Text(), nullable=True),
            sa.Column("previous_secret_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "secret_name", name="uq_project_secret_lifecycle_project_secret"),
        )

    inspector = sa.inspect(bind)
    if "project_secret_lifecycle" in _tables(inspector):
        indexes = _indexes(inspector, "project_secret_lifecycle")
        if "ix_project_secret_lifecycle_project_id" not in indexes:
            op.create_index("ix_project_secret_lifecycle_project_id", "project_secret_lifecycle", ["project_id"], unique=False)
        if "ix_project_secret_lifecycle_secret_name" not in indexes:
            op.create_index("ix_project_secret_lifecycle_secret_name", "project_secret_lifecycle", ["secret_name"], unique=False)
        if "ix_project_secret_lifecycle_last_used_at" not in indexes:
            op.create_index("ix_project_secret_lifecycle_last_used_at", "project_secret_lifecycle", ["last_used_at"], unique=False)
        if "ix_project_secret_lifecycle_expires_at" not in indexes:
            op.create_index("ix_project_secret_lifecycle_expires_at", "project_secret_lifecycle", ["expires_at"], unique=False)
        if "ix_project_secret_lifecycle_previous_secret_expires_at" not in indexes:
            op.create_index(
                "ix_project_secret_lifecycle_previous_secret_expires_at",
                "project_secret_lifecycle",
                ["previous_secret_expires_at"],
                unique=False,
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "project_secret_lifecycle" in _tables(inspector):
        indexes = _indexes(inspector, "project_secret_lifecycle")
        for name in (
            "ix_project_secret_lifecycle_previous_secret_expires_at",
            "ix_project_secret_lifecycle_expires_at",
            "ix_project_secret_lifecycle_last_used_at",
            "ix_project_secret_lifecycle_secret_name",
            "ix_project_secret_lifecycle_project_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="project_secret_lifecycle")
        op.drop_table("project_secret_lifecycle")

    inspector = sa.inspect(bind)
    if "api_key" in _tables(inspector):
        indexes = _indexes(inspector, "api_key")
        for name in (
            "ix_api_key_replaced_by_api_key_id",
            "ix_api_key_expires_at",
            "ix_api_key_last_used_at",
        ):
            if name in indexes:
                op.drop_index(name, table_name="api_key")
        existing = _columns(inspector, "api_key")
        for column_name in (
            "replaced_by_api_key_id",
            "rotation_interval_days",
            "last_rotated_at",
            "expires_at",
            "last_used_at",
        ):
            if column_name in existing:
                op.drop_column("api_key", column_name)
