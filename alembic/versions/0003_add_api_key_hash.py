"""add api_key_hash to project

Revision ID: 0003_add_api_key_hash
Revises: 0002_add_interval_next_run
Create Date: 2026-01-12 00:30:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0003_add_api_key_hash'
down_revision = '0002_add_interval_next_run'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE "project" ADD COLUMN "api_key_hash" TEXT'))
        except Exception:
            pass


def downgrade() -> None:
    # SQLite: no easy DROP COLUMN; noop
    pass
