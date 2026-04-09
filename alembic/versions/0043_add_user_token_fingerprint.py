"""add user token fingerprint and hash existing session tokens

Revision ID: 0043_add_user_token_fingerprint
Revises: 0042_add_jira_ticket_fields
Create Date: 2026-04-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

from src.security import fingerprint_token, hash_api_key


revision = "0043_add_user_token_fingerprint"
down_revision = "0042_add_jira_ticket_fields"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str):
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_token_cols = _columns(inspector, "user_token")
    if "token_fingerprint" not in user_token_cols:
        op.add_column("user_token", sa.Column("token_fingerprint", sa.String(length=64), nullable=True))

    inspector = sa.inspect(bind)
    user_token_indexes = _indexes(inspector, "user_token")
    if "ix_user_token_token_fingerprint" not in user_token_indexes:
        op.create_index("ix_user_token_token_fingerprint", "user_token", ["token_fingerprint"], unique=False)

    user_token_table = sa.table(
        "user_token",
        sa.column("id", sa.Integer),
        sa.column("token", sa.String),
        sa.column("token_fingerprint", sa.String),
    )

    rows = bind.execute(
        sa.select(
            user_token_table.c.id,
            user_token_table.c.token,
            user_token_table.c.token_fingerprint,
        )
    ).fetchall()
    for row in rows:
        raw_token = row.token
        if not raw_token:
            continue
        if row.token_fingerprint:
            continue
        if str(raw_token).startswith("pbkdf2_sha256$"):
            continue
        bind.execute(
            user_token_table.update()
            .where(user_token_table.c.id == row.id)
            .values(
                token=hash_api_key(raw_token),
                token_fingerprint=fingerprint_token(raw_token),
            )
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ix_user_token_token_fingerprint" in _indexes(inspector, "user_token"):
        op.drop_index("ix_user_token_token_fingerprint", table_name="user_token")

    inspector = sa.inspect(bind)
    if "token_fingerprint" in _columns(inspector, "user_token"):
        op.drop_column("user_token", "token_fingerprint")
