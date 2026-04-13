"""add managed identity sync fields to memberships

Revision ID: 0052_add_managed_membership_fields
Revises: 0051_add_scim_and_group_sync
Create Date: 2026-04-13 01:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0052_add_managed_membership_fields"
down_revision = "0051_add_scim_and_group_sync"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_membership_columns(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _columns(inspector, table_name)
    indexes = _indexes(inspector, table_name)

    if "managed_provider" not in columns:
        op.add_column(table_name, sa.Column("managed_provider", sa.String(), nullable=True))
    if "managed_group" not in columns:
        op.add_column(table_name, sa.Column("managed_group", sa.String(), nullable=True))
    if "managed_fallback_role" not in columns:
        op.add_column(table_name, sa.Column("managed_fallback_role", sa.String(), nullable=True))
    if "managed_last_synced_at" not in columns:
        op.add_column(table_name, sa.Column("managed_last_synced_at", sa.DateTime(), nullable=True))
    if f"ix_{table_name}_managed_provider" not in indexes:
        op.create_index(f"ix_{table_name}_managed_provider", table_name, ["managed_provider"], unique=False)


def _drop_membership_columns(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = _columns(inspector, table_name)
    indexes = _indexes(inspector, table_name)

    index_name = f"ix_{table_name}_managed_provider"
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)
    if "managed_last_synced_at" in columns:
        op.drop_column(table_name, "managed_last_synced_at")
    if "managed_fallback_role" in columns:
        op.drop_column(table_name, "managed_fallback_role")
    if "managed_group" in columns:
        op.drop_column(table_name, "managed_group")
    if "managed_provider" in columns:
        op.drop_column(table_name, "managed_provider")


def upgrade():
    _add_membership_columns("organization_membership")
    _add_membership_columns("team_membership")


def downgrade():
    _drop_membership_columns("team_membership")
    _drop_membership_columns("organization_membership")
