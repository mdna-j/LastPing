"""add api_key_hash to project

Revision ID: 0003_add_api_key_hash
Revises: 0002_add_interval_next_run
Create Date: 2026-01-12 00:30:00.000000
"""
from sqlalchemy import text, inspect
from alembic import op

revision = '0003_add_api_key_hash'
down_revision = '0002_add_interval_next_run'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    if "project" not in inspector.get_table_names():
        return
    cols = {col["name"] for col in inspector.get_columns("project")}
    if "api_key_hash" not in cols:
        conn.execute(text('ALTER TABLE "project" ADD COLUMN "api_key_hash" TEXT'))


def downgrade() -> None:
    # SQLite: no easy DROP COLUMN; noop
    pass
