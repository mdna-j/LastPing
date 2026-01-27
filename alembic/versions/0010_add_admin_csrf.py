"""add admin_csrf table

Revision ID: 0010_add_admin_csrf
Revises: 0009_add_audit_log
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from alembic import op

revision = '0010_add_admin_csrf'
down_revision = '0009_add_audit_log'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('admin_csrf')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE admin_csrf ('
                'id INTEGER PRIMARY KEY,'
                'token TEXT,'
                'created_at DATETIME,'
                'expires_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS admin_csrf ('
                'id SERIAL PRIMARY KEY,'
                'token TEXT,'
                'created_at TIMESTAMP,'
                'expires_at TIMESTAMP'
                ')' ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(text('DROP TABLE IF EXISTS admin_csrf'))
    except Exception:
        pass
