"""add User and ProjectMembership models

Revision ID: 0011_add_user_and_membership
Revises: 0010_add_admin_csrf
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from alembic import op

revision = '0011_add_user_and_membership'
down_revision = '0010_add_admin_csrf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    # create `user` table
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('user')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE user ('
                'id INTEGER PRIMARY KEY,'
                'email TEXT NOT NULL,'
                'hashed_password TEXT NOT NULL,'
                'is_active INTEGER,'
                'created_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS "user" ('
                'id SERIAL PRIMARY KEY,'
                'email TEXT NOT NULL,'
                'hashed_password TEXT NOT NULL,'
                'is_active BOOLEAN,'
                'created_at TIMESTAMP'
                ')' ))
        except Exception:
            pass

    # create `project_membership` table
    if dialect == 'sqlite':
        res = conn.execute(text("PRAGMA table_info('project_membership')")).fetchall()
        if not res:
            conn.execute(text(
                'CREATE TABLE project_membership ('
                'id INTEGER PRIMARY KEY,'
                'user_id INTEGER,'
                'project_id INTEGER,'
                'role TEXT,'
                'created_at DATETIME'
                ')' ))
    else:
        try:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS project_membership ('
                'id SERIAL PRIMARY KEY,'
                'user_id INTEGER REFERENCES "user"(id),'
                'project_id INTEGER REFERENCES project(id),'
                'role TEXT,'
                'created_at TIMESTAMP'
                ')' ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    try:
        conn.execute(text('DROP TABLE IF EXISTS project_membership'))
    except Exception:
        pass
    try:
        conn.execute(text('DROP TABLE IF EXISTS "user"'))
    except Exception:
        pass
