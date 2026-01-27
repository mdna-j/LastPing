"""add uptime_snapshot table

Revision ID: 0013_add_uptime_snapshot
Revises: 0012_add_user_token
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from alembic import op

revision = '0013_add_uptime_snapshot'
down_revision = '0012_add_user_token'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('uptime_snapshot')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE uptime_snapshot ('
                'id INTEGER PRIMARY KEY,'
                'project_id INTEGER,'
                'check_id INTEGER,'
                'window_start DATETIME,'
                'window_end DATETIME,'
                'uptime_percent REAL,'
                'mttr_seconds REAL,'
                'created_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS uptime_snapshot ('
                'id SERIAL PRIMARY KEY,'
                'project_id INTEGER REFERENCES project(id),'
                'check_id INTEGER REFERENCES "check"(id),'
                'window_start TIMESTAMP,'
                'window_end TIMESTAMP,'
                'uptime_percent REAL,'
                'mttr_seconds REAL,'
                'created_at TIMESTAMP'
                ')' ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(text('DROP TABLE IF EXISTS uptime_snapshot'))
    except Exception:
        pass
