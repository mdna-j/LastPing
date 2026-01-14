"""add ApiKeyUsage table for rate limiting

Revision ID: 0008_add_api_key_usage
Revises: 0007_add_api_key_model
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0008_add_api_key_usage'
down_revision = '0007_add_api_key_model'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == 'sqlite':
            res = conn.execute(text("PRAGMA table_info('api_key_usage')")).fetchall()
            if not res:
                conn.execute(text(
                    'CREATE TABLE api_key_usage ('
                    'id INTEGER PRIMARY KEY,'
                    'api_key_id INTEGER,'
                    'minute_start DATETIME,'
                    'count INTEGER'
                    ')' ))
        else:
            try:
                conn.execute(text(
                    'CREATE TABLE IF NOT EXISTS api_key_usage ('
                    'id SERIAL PRIMARY KEY,'
                    'api_key_id INTEGER REFERENCES api_key(id),' 
                    'minute_start TIMESTAMP,'
                    'count INTEGER'
                    ')' ))
            except Exception:
                pass


def downgrade() -> None:
    with engine.connect() as conn:
        try:
            conn.execute(text('DROP TABLE IF EXISTS api_key_usage'))
        except Exception:
            pass
