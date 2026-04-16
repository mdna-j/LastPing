"""add browser check secrets

Revision ID: 0053_add_browser_check_secrets
Revises: 0052_add_managed_membership_fields
Create Date: 2026-04-15 14:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0053_add_browser_check_secrets"
down_revision = "0052_add_managed_membership_fields"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "browser_check_secret" not in tables:
        op.create_table(
            "browser_check_secret",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("project_id", "name", name="uq_browser_check_secret_project_name"),
        )
    indexes = {index["name"] for index in inspector.get_indexes("browser_check_secret")} if "browser_check_secret" in set(sa.inspect(bind).get_table_names()) else set()
    if "ix_browser_check_secret_project_id" not in indexes:
        op.create_index("ix_browser_check_secret_project_id", "browser_check_secret", ["project_id"], unique=False)
    if "ix_browser_check_secret_name" not in indexes:
        op.create_index("ix_browser_check_secret_name", "browser_check_secret", ["name"], unique=False)
    if "ix_browser_check_secret_last_used_at" not in indexes:
        op.create_index("ix_browser_check_secret_last_used_at", "browser_check_secret", ["last_used_at"], unique=False)
    if "ix_browser_check_secret_created_at" not in indexes:
        op.create_index("ix_browser_check_secret_created_at", "browser_check_secret", ["created_at"], unique=False)
    if "ix_browser_check_secret_updated_at" not in indexes:
        op.create_index("ix_browser_check_secret_updated_at", "browser_check_secret", ["updated_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "browser_check_secret" not in tables:
        return
    indexes = {index["name"] for index in inspector.get_indexes("browser_check_secret")}
    for index_name in (
        "ix_browser_check_secret_updated_at",
        "ix_browser_check_secret_created_at",
        "ix_browser_check_secret_last_used_at",
        "ix_browser_check_secret_name",
        "ix_browser_check_secret_project_id",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="browser_check_secret")
    op.drop_table("browser_check_secret")
