"""add interval and next_run

Revision ID: 0002_add_interval_next_run
Revises: 0001_initial
Create Date: 2026-01-12 00:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0002_add_interval_next_run'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add `interval` and `next_run` columns to the `check` table.
    # Use simple ALTER TABLE ADD COLUMN which is supported by SQLite.
    with engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE "check" ADD COLUMN "interval" INTEGER DEFAULT 60'))
        except Exception:
            # column may already exist on some databases; ignore
            pass
        try:
            conn.execute(text('ALTER TABLE "check" ADD COLUMN "next_run" DATETIME'))
        except Exception:
            pass


def downgrade() -> None:
    # SQLite doesn't support DROP COLUMN easily; downgrade is a no-op.
    pass
