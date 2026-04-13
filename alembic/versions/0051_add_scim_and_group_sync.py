"""add scim and idp group sync tables

Revision ID: 0051_add_scim_and_group_sync
Revises: 0050_add_enterprise_auth
Create Date: 2026-04-13 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0051_add_scim_and_group_sync"
down_revision = "0050_add_enterprise_auth"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    org_columns = _columns(inspector, "organization")
    if "scim_bearer_token" not in org_columns:
        op.add_column("organization", sa.Column("scim_bearer_token", sa.Text(), nullable=True))
    if "scim_last_rotated_at" not in org_columns:
        op.add_column("organization", sa.Column("scim_last_rotated_at", sa.DateTime(), nullable=True))

    identity_columns = _columns(inspector, "user_identity")
    if "last_groups_json" not in identity_columns:
        op.add_column("user_identity", sa.Column("last_groups_json", sa.Text(), nullable=True))

    tables = set(inspector.get_table_names())
    if "organization_group_mapping" not in tables:
        op.create_table(
            "organization_group_mapping",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("external_group", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_organization_group_mapping_organization_id", "organization_group_mapping", ["organization_id"], unique=False)
        op.create_index("ix_organization_group_mapping_provider", "organization_group_mapping", ["provider"], unique=False)
        op.create_index("ix_organization_group_mapping_external_group", "organization_group_mapping", ["external_group"], unique=False)
        op.create_index("ix_organization_group_mapping_role", "organization_group_mapping", ["role"], unique=False)

    if "team_group_mapping" not in tables:
        op.create_table(
            "team_group_mapping",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("external_group", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
            sa.ForeignKeyConstraint(["team_id"], ["team.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_team_group_mapping_organization_id", "team_group_mapping", ["organization_id"], unique=False)
        op.create_index("ix_team_group_mapping_team_id", "team_group_mapping", ["team_id"], unique=False)
        op.create_index("ix_team_group_mapping_provider", "team_group_mapping", ["provider"], unique=False)
        op.create_index("ix_team_group_mapping_external_group", "team_group_mapping", ["external_group"], unique=False)
        op.create_index("ix_team_group_mapping_role", "team_group_mapping", ["role"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "team_group_mapping" in tables:
        for index_name in (
            "ix_team_group_mapping_role",
            "ix_team_group_mapping_external_group",
            "ix_team_group_mapping_provider",
            "ix_team_group_mapping_team_id",
            "ix_team_group_mapping_organization_id",
        ):
            op.drop_index(index_name, table_name="team_group_mapping")
        op.drop_table("team_group_mapping")

    if "organization_group_mapping" in tables:
        for index_name in (
            "ix_organization_group_mapping_role",
            "ix_organization_group_mapping_external_group",
            "ix_organization_group_mapping_provider",
            "ix_organization_group_mapping_organization_id",
        ):
            op.drop_index(index_name, table_name="organization_group_mapping")
        op.drop_table("organization_group_mapping")

    identity_columns = _columns(inspector, "user_identity")
    if "last_groups_json" in identity_columns:
        op.drop_column("user_identity", "last_groups_json")

    org_columns = _columns(inspector, "organization")
    if "scim_last_rotated_at" in org_columns:
        op.drop_column("organization", "scim_last_rotated_at")
    if "scim_bearer_token" in org_columns:
        op.drop_column("organization", "scim_bearer_token")
