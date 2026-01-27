"""add interval and next_run

Revision ID: 0002_add_interval_next_run
Revises: 0001_initial
Create Date: 2026-01-12 00:00:00.000000
"""
from sqlalchemy import text, inspect
from alembic import op

revision = '0002_add_interval_next_run'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add `interval` and `next_run` columns to the `check` table.
    # Use simple ALTER TABLE ADD COLUMN which is supported by SQLite.
    conn = op.get_bind()
    inspector = inspect(conn)
    if "check" not in inspector.get_table_names():
        return
    cols = {col["name"] for col in inspector.get_columns("check")}
    if "interval" not in cols:
        conn.execute(text('ALTER TABLE "check" ADD COLUMN "interval" INTEGER DEFAULT 60'))
    if "next_run" not in cols:
        conn.execute(text('ALTER TABLE "check" ADD COLUMN "next_run" TIMESTAMP'))


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN easily; downgrade is a no-op.
    pass
