"""add ApiKey model with rate limits

Revision ID: 0007_add_api_key_model
Revises: 0006_add_maintenance_windows
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0007_add_api_key_model'
down_revision = '0006_add_maintenance_windows'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        # create a simple api_key table; SQLite and other DBs can use CREATE TABLE
        if dialect == 'sqlite':
            # ensure table does not already exist
            res = conn.execute(text("PRAGMA table_info('api_key')")).fetchall()
            if not res:
                conn.execute(text(
                    'CREATE TABLE api_key ('
                    'id INTEGER PRIMARY KEY,'
                    'project_id INTEGER,'
                    'key_hash TEXT NOT NULL,'
                    'rate_limit_per_minute INTEGER,'
                    'created_at DATETIME'
                    ')' ))
        else:
            try:
                conn.execute(text(
                    'CREATE TABLE IF NOT EXISTS api_key ('
                    'id SERIAL PRIMARY KEY,'
                    'project_id INTEGER REFERENCES project(id),' 
                    'key_hash TEXT NOT NULL,'
                    'rate_limit_per_minute INTEGER,'
                    'created_at TIMESTAMP'
                    ')' ))
            except Exception:
                pass


def downgrade() -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == 'sqlite':
            # SQLite: drop table if exists
            try:
                conn.execute(text('DROP TABLE IF EXISTS api_key'))
            except Exception:
                pass
        else:
            try:
                conn.execute(text('DROP TABLE IF EXISTS api_key'))
            except Exception:
                pass
