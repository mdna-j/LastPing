"""add audit_log table

Revision ID: 0009_add_audit_log
Revises: 0008_add_api_key_usage
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from alembic import op

revision = '0009_add_audit_log'
down_revision = '0008_add_api_key_usage'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('audit_log')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE audit_log ('
                'id INTEGER PRIMARY KEY,'
                'actor TEXT,'
                'action TEXT NOT NULL,'
                'target_type TEXT,'
                'target_id INTEGER,'
                'details TEXT,'
                'created_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS audit_log ('
                'id SERIAL PRIMARY KEY,'
                'actor TEXT,'
                'action TEXT NOT NULL,'
                'target_type TEXT,'
                'target_id INTEGER,'
                'details TEXT,'
                'created_at TIMESTAMP'
                ')' ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(text('DROP TABLE IF EXISTS audit_log'))
    except Exception:
        pass
