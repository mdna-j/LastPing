"""add enterprise auth and sso tables

Revision ID: 0050_add_enterprise_auth
Revises: 0049_add_incident_resolution_fields
Create Date: 2026-04-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0050_add_enterprise_auth"
down_revision = "0049_add_incident_resolution_fields"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = _columns(inspector, "user")
    if "display_name" not in user_columns:
        op.add_column("user", sa.Column("display_name", sa.String(), nullable=True))
    if "mfa_secret" not in user_columns:
        op.add_column("user", sa.Column("mfa_secret", sa.Text(), nullable=True))
    if "mfa_enabled_at" not in user_columns:
        op.add_column("user", sa.Column("mfa_enabled_at", sa.DateTime(), nullable=True))
    if "last_login_at" not in user_columns:
        op.add_column("user", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    token_columns = _columns(inspector, "user_token")
    if "revoked_at" not in token_columns:
        op.add_column("user_token", sa.Column("revoked_at", sa.DateTime(), nullable=True))
    if "last_seen_at" not in token_columns:
        op.add_column("user_token", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    if "session_name" not in token_columns:
        op.add_column("user_token", sa.Column("session_name", sa.String(), nullable=True))
    if "auth_method" not in token_columns:
        op.add_column("user_token", sa.Column("auth_method", sa.String(), nullable=True))
    if "auth_provider" not in token_columns:
        op.add_column("user_token", sa.Column("auth_provider", sa.String(), nullable=True))
    if "issued_from_ip" not in token_columns:
        op.add_column("user_token", sa.Column("issued_from_ip", sa.String(), nullable=True))
    if "issued_user_agent" not in token_columns:
        op.add_column("user_token", sa.Column("issued_user_agent", sa.Text(), nullable=True))
    if "mfa_verified_at" not in token_columns:
        op.add_column("user_token", sa.Column("mfa_verified_at", sa.DateTime(), nullable=True))

    token_indexes = _indexes(inspector, "user_token")
    if "ix_user_token_revoked_at" not in token_indexes:
        op.create_index("ix_user_token_revoked_at", "user_token", ["revoked_at"], unique=False)
    if "ix_user_token_auth_method" not in token_indexes:
        op.create_index("ix_user_token_auth_method", "user_token", ["auth_method"], unique=False)
    if "ix_user_token_auth_provider" not in token_indexes:
        op.create_index("ix_user_token_auth_provider", "user_token", ["auth_provider"], unique=False)

    if "user_identity" not in inspector.get_table_names():
        op.create_table(
            "user_identity",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("provider_subject", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "provider_subject", name="uq_user_identity_provider_subject"),
        )
        op.create_index("ix_user_identity_user_id", "user_identity", ["user_id"], unique=False)
        op.create_index("ix_user_identity_provider", "user_identity", ["provider"], unique=False)
        op.create_index("ix_user_identity_provider_subject", "user_identity", ["provider_subject"], unique=False)
        op.create_index("ix_user_identity_email", "user_identity", ["email"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "user_identity" in inspector.get_table_names():
        op.drop_index("ix_user_identity_email", table_name="user_identity")
        op.drop_index("ix_user_identity_provider_subject", table_name="user_identity")
        op.drop_index("ix_user_identity_provider", table_name="user_identity")
        op.drop_index("ix_user_identity_user_id", table_name="user_identity")
        op.drop_table("user_identity")

    token_columns = _columns(inspector, "user_token")
    token_indexes = _indexes(inspector, "user_token")
    if "ix_user_token_auth_provider" in token_indexes:
        op.drop_index("ix_user_token_auth_provider", table_name="user_token")
    if "ix_user_token_auth_method" in token_indexes:
        op.drop_index("ix_user_token_auth_method", table_name="user_token")
    if "ix_user_token_revoked_at" in token_indexes:
        op.drop_index("ix_user_token_revoked_at", table_name="user_token")
    for column_name in (
        "mfa_verified_at",
        "issued_user_agent",
        "issued_from_ip",
        "auth_provider",
        "auth_method",
        "session_name",
        "last_seen_at",
        "revoked_at",
    ):
        if column_name in token_columns:
            op.drop_column("user_token", column_name)

    user_columns = _columns(inspector, "user")
    for column_name in ("last_login_at", "mfa_enabled_at", "mfa_secret", "display_name"):
        if column_name in user_columns:
            op.drop_column("user", column_name)
