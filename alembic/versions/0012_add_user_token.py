"""add user_token table

Revision ID: 0012_add_user_token
Revises: 0011_add_user_and_membership
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from alembic import op

revision = '0012_add_user_token'
down_revision = '0011_add_user_and_membership'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('user_token')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE user_token ('
                'id INTEGER PRIMARY KEY,'
                'user_id INTEGER,'
                'token TEXT,'
                'created_at DATETIME,'
                'expires_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS user_token ('
                'id SERIAL PRIMARY KEY,'
                'user_id INTEGER REFERENCES "user"(id),'
                'token TEXT,'
                'created_at TIMESTAMP,'
                'expires_at TIMESTAMP'
                ')' ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(text('DROP TABLE IF EXISTS user_token'))
    except Exception:
        pass
