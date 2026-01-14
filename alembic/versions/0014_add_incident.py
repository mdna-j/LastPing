"""add Incident model and link Event to incident

Revision ID: 0014_add_incident
Revises: 0013_add_uptime_snapshot
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0014_add_incident'
down_revision = '0013_add_uptime_snapshot'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        # create incident table
        if dialect == 'sqlite':
            res = conn.execute(text("PRAGMA table_info('incident')")).fetchall()
            if not res:
                conn.execute(text(
                    'CREATE TABLE incident ('
                    'id INTEGER PRIMARY KEY,'
                    'project_id INTEGER,'
                    'check_id INTEGER,'
                    'started_at DATETIME,'
                    'resolved_at DATETIME,'
                    'status TEXT,'
                    'created_at DATETIME'
                    ')' ))
        else:
            try:
                conn.execute(text(
                    'CREATE TABLE IF NOT EXISTS incident ('
                    'id SERIAL PRIMARY KEY,'
                    'project_id INTEGER REFERENCES project(id),'
                    'check_id INTEGER REFERENCES "check"(id),'
                    'started_at TIMESTAMP,'
                    'resolved_at TIMESTAMP,'
                    'status TEXT,'
                    'created_at TIMESTAMP'
                    ')' ))
            except Exception:
                pass

        # add incident_id column to event table if missing
        if dialect == 'sqlite':
            cols = conn.execute(text("PRAGMA table_info('event')")).fetchall()
            names = [c[1] for c in cols]
            if 'incident_id' not in names:
                try:
                    conn.execute(text('ALTER TABLE event ADD COLUMN incident_id INTEGER'))
                except Exception:
                    pass
        else:
            try:
                conn.execute(text('ALTER TABLE event ADD COLUMN incident_id INTEGER'))
            except Exception:
                pass


def downgrade() -> None:
    with engine.connect() as conn:
        try:
            conn.execute(text('ALTER TABLE event DROP COLUMN incident_id'))
        except Exception:
            pass
        try:
            conn.execute(text('DROP TABLE IF EXISTS incident'))
        except Exception:
            pass
