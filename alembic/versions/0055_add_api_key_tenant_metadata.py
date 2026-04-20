"""add api key tenant metadata

Revision ID: 0055_add_api_key_tenant_metadata
Revises: 0054_add_slo_compliance_period
Create Date: 2026-04-20 18:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0055_add_api_key_tenant_metadata"
down_revision = "0054_add_slo_compliance_period"
branch_labels = None
depends_on = None


def _column_names(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name
    tables = set(inspector.get_table_names())
    if "api_key" not in tables:
        return

    columns = _column_names(inspector, "api_key")
    if "description" not in columns:
        op.add_column("api_key", sa.Column("description", sa.String(length=240), nullable=True))
    if "token_type" not in columns:
        op.add_column("api_key", sa.Column("token_type", sa.String(length=40), nullable=True, server_default="project_token"))
    if "managed_by_team_id" not in columns:
        op.add_column("api_key", sa.Column("managed_by_team_id", sa.Integer(), nullable=True))
        if dialect != "sqlite":
            op.create_foreign_key(
                "fk_api_key_managed_by_team_id_team",
                "api_key",
                "team",
                ["managed_by_team_id"],
                ["id"],
            )

    op.execute("UPDATE api_key SET token_type = 'project_token' WHERE token_type IS NULL")

    indexes = {index["name"] for index in inspector.get_indexes("api_key")}
    if "ix_api_key_token_type" not in indexes:
        op.create_index("ix_api_key_token_type", "api_key", ["token_type"], unique=False)
    if "ix_api_key_managed_by_team_id" not in indexes:
        op.create_index("ix_api_key_managed_by_team_id", "api_key", ["managed_by_team_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name
    tables = set(inspector.get_table_names())
    if "api_key" not in tables:
        return

    indexes = {index["name"] for index in inspector.get_indexes("api_key")}
    if "ix_api_key_managed_by_team_id" in indexes:
        op.drop_index("ix_api_key_managed_by_team_id", table_name="api_key")
    if "ix_api_key_token_type" in indexes:
        op.drop_index("ix_api_key_token_type", table_name="api_key")

    if dialect != "sqlite":
        foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("api_key")}
        if "fk_api_key_managed_by_team_id_team" in foreign_keys:
            op.drop_constraint("fk_api_key_managed_by_team_id_team", "api_key", type_="foreignkey")

    columns = _column_names(inspector, "api_key")
    if "managed_by_team_id" in columns:
        op.drop_column("api_key", "managed_by_team_id")
    if "token_type" in columns:
        op.drop_column("api_key", "token_type")
    if "description" in columns:
        op.drop_column("api_key", "description")
