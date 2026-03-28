"""add enterprise rbac models and scopes

Revision ID: 0039_add_enterprise_rbac
Revises: 0038_add_browser_checks
Create Date: 2026-03-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0039_add_enterprise_rbac"
down_revision = "0038_add_browser_checks"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def _columns(inspector, table_name: str):
    if table_name not in _tables(inspector):
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str):
    if table_name not in _tables(inspector):
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _ensure_index(inspector, name: str, table_name: str, columns: list[str]):
    if name not in _indexes(inspector, table_name):
        op.create_index(name, table_name, columns, unique=False)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    if "organization" not in tables:
        op.create_table(
            "organization",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_organization_name", "organization", ["name"])
    _ensure_index(inspector, "ix_organization_slug", "organization", ["slug"])

    project_cols = _columns(inspector, "project")
    if "org_id" not in project_cols:
        op.add_column("project", sa.Column("org_id", sa.Integer(), nullable=True))
        inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_project_org_id", "project", ["org_id"])

    api_key_cols = _columns(inspector, "api_key")
    if "name" not in api_key_cols:
        op.add_column("api_key", sa.Column("name", sa.String(length=120), nullable=True))
    if "role" not in api_key_cols:
        op.add_column("api_key", sa.Column("role", sa.String(length=32), nullable=False, server_default="owner"))
    if "is_active" not in api_key_cols:
        op.add_column("api_key", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "revoked_at" not in api_key_cols:
        op.add_column("api_key", sa.Column("revoked_at", sa.DateTime(), nullable=True))
    if "created_by_user_id" not in api_key_cols:
        op.add_column("api_key", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_api_key_role", "api_key", ["role"])
    _ensure_index(inspector, "ix_api_key_is_active", "api_key", ["is_active"])
    _ensure_index(inspector, "ix_api_key_revoked_at", "api_key", ["revoked_at"])
    _ensure_index(inspector, "ix_api_key_created_by_user_id", "api_key", ["created_by_user_id"])

    audit_cols = _columns(inspector, "audit_log")
    if "org_id" not in audit_cols:
        op.add_column("audit_log", sa.Column("org_id", sa.Integer(), nullable=True))
    if "team_id" not in audit_cols:
        op.add_column("audit_log", sa.Column("team_id", sa.Integer(), nullable=True))
    if "project_id" not in audit_cols:
        op.add_column("audit_log", sa.Column("project_id", sa.Integer(), nullable=True))
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_audit_log_org_id", "audit_log", ["org_id"])
    _ensure_index(inspector, "ix_audit_log_team_id", "audit_log", ["team_id"])
    _ensure_index(inspector, "ix_audit_log_project_id", "audit_log", ["project_id"])

    if "organization_membership" not in _tables(inspector):
        op.create_table(
            "organization_membership",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_organization_membership_organization_id", "organization_membership", ["organization_id"])
    _ensure_index(inspector, "ix_organization_membership_user_id", "organization_membership", ["user_id"])
    _ensure_index(inspector, "ix_organization_membership_role", "organization_membership", ["role"])

    if "team" not in _tables(inspector):
        op.create_table(
            "team",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_team_organization_id", "team", ["organization_id"])
    _ensure_index(inspector, "ix_team_name", "team", ["name"])
    _ensure_index(inspector, "ix_team_slug", "team", ["slug"])

    if "team_membership" not in _tables(inspector):
        op.create_table(
            "team_membership",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("team.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="member"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_team_membership_team_id", "team_membership", ["team_id"])
    _ensure_index(inspector, "ix_team_membership_user_id", "team_membership", ["user_id"])
    _ensure_index(inspector, "ix_team_membership_role", "team_membership", ["role"])

    if "project_team_access" not in _tables(inspector):
        op.create_table(
            "project_team_access",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("team.id"), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    inspector = sa.inspect(bind)
    _ensure_index(inspector, "ix_project_team_access_project_id", "project_team_access", ["project_id"])
    _ensure_index(inspector, "ix_project_team_access_team_id", "project_team_access", ["team_id"])
    _ensure_index(inspector, "ix_project_team_access_role", "project_team_access", ["role"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, index_names in [
        ("project_team_access", ["ix_project_team_access_role", "ix_project_team_access_team_id", "ix_project_team_access_project_id"]),
        ("team_membership", ["ix_team_membership_role", "ix_team_membership_user_id", "ix_team_membership_team_id"]),
        ("team", ["ix_team_slug", "ix_team_name", "ix_team_organization_id"]),
        ("organization_membership", ["ix_organization_membership_role", "ix_organization_membership_user_id", "ix_organization_membership_organization_id"]),
        ("organization", ["ix_organization_slug", "ix_organization_name"]),
    ]:
        if table_name in _tables(inspector):
            existing_indexes = _indexes(inspector, table_name)
            for index_name in index_names:
                if index_name in existing_indexes:
                    op.drop_index(index_name, table_name=table_name)
            op.drop_table(table_name)
            inspector = sa.inspect(bind)

    api_key_cols = _columns(inspector, "api_key")
    for index_name in ["ix_api_key_created_by_user_id", "ix_api_key_revoked_at", "ix_api_key_is_active", "ix_api_key_role"]:
        if index_name in _indexes(inspector, "api_key"):
            op.drop_index(index_name, table_name="api_key")
    for column_name in ["created_by_user_id", "revoked_at", "is_active", "role", "name"]:
        if column_name in api_key_cols:
            op.drop_column("api_key", column_name)
            inspector = sa.inspect(bind)
            api_key_cols = _columns(inspector, "api_key")

    audit_cols = _columns(inspector, "audit_log")
    for index_name in ["ix_audit_log_project_id", "ix_audit_log_team_id", "ix_audit_log_org_id"]:
        if index_name in _indexes(inspector, "audit_log"):
            op.drop_index(index_name, table_name="audit_log")
    for column_name in ["project_id", "team_id", "org_id"]:
        if column_name in audit_cols:
            op.drop_column("audit_log", column_name)
            inspector = sa.inspect(bind)
            audit_cols = _columns(inspector, "audit_log")

    project_cols = _columns(inspector, "project")
    if "ix_project_org_id" in _indexes(inspector, "project"):
        op.drop_index("ix_project_org_id", table_name="project")
    if "org_id" in project_cols:
        op.drop_column("project", "org_id")
