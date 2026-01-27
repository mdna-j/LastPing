"""harden incident migration: add share_token, FK constraints, backfill events

Revision ID: 0015_harden_incident_migration
Revises: 0014_add_incident
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text, inspect
from alembic import op
import secrets

revision = '0015_harden_incident_migration'
down_revision = '0014_add_incident'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    # add share_token column
    try:
        if dialect == 'sqlite':
            cols = conn.execute(text("PRAGMA table_info('incident')")).fetchall()
            names = [c[1] for c in cols]
            if 'share_token' not in names:
                conn.execute(text("ALTER TABLE incident ADD COLUMN share_token TEXT"))
        else:
            inspector = inspect(conn)
            if "incident" in inspector.get_table_names():
                incident_cols = {col["name"] for col in inspector.get_columns("incident")}
                if "share_token" not in incident_cols:
                    conn.execute(text("ALTER TABLE incident ADD COLUMN share_token TEXT"))
    except Exception:
        pass

    # Add FK constraint on event.incident_id where supported
    if dialect != 'sqlite':
        inspector = inspect(conn)
        fk_names = set()
        if "event" in inspector.get_table_names():
            for fk in inspector.get_foreign_keys("event"):
                if fk.get("name"):
                    fk_names.add(fk["name"])
        if "fk_event_incident" not in fk_names:
            try:
                conn.execute(text(
                    "ALTER TABLE event ADD CONSTRAINT fk_event_incident FOREIGN KEY (incident_id) REFERENCES incident(id)"
                ))
            except Exception:
                # may already exist or not possible
                pass

    # Best-effort backfill: for events lacking incident_id, create incidents grouping down->up sequences
    try:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        if "event" not in tables or "incident" not in tables:
            return
        # fetch checks that have events without incident_id
        rows = conn.execute(text("SELECT DISTINCT check_id, project_id FROM event WHERE incident_id IS NULL AND event_type IN ('down','http_failure')")).fetchall()
        for check_id, project_id in rows:
            # get down events without incident for this check
            downs = conn.execute(text(f"SELECT id, created_at, event_type FROM event WHERE check_id = {check_id} AND incident_id IS NULL AND event_type IN ('down','http_failure') ORDER BY created_at" )).fetchall()
            for d in downs:
                d_id, d_created, d_type = d
                # find next up event
                ups = conn.execute(text(f"SELECT id, created_at FROM event WHERE check_id = {check_id} AND created_at > '{d_created}' AND event_type = 'up' ORDER BY created_at LIMIT 1")).fetchall()
                started_at = d_created
                resolved_at = None
                if ups:
                    resolved_at = ups[0][1]
                # create incident
                token = secrets.token_urlsafe(16)
                conn.execute(text("INSERT INTO incident (project_id, check_id, started_at, resolved_at, status, share_token, created_at) VALUES (:proj, :chk, :start, :res, :status, :token, :created)"), {
                    'proj': project_id,
                    'chk': check_id,
                    'start': started_at,
                    'res': resolved_at,
                    'status': 'resolved' if resolved_at else 'open',
                    'token': token,
                    'created': started_at
                })
                # fetch the incident id we just created
                iid = conn.execute(text("SELECT id FROM incident WHERE check_id = :chk AND started_at = :start ORDER BY id DESC LIMIT 1"), {'chk': check_id, 'start': started_at}).fetchone()[0]
                # attach incident_id to events between start and resolved (or forward)
                if resolved_at:
                    conn.execute(text("UPDATE event SET incident_id = :iid WHERE check_id = :chk AND created_at >= :start AND created_at <= :end"), {'iid': iid, 'chk': check_id, 'start': started_at, 'end': resolved_at})
                else:
                    conn.execute(text("UPDATE event SET incident_id = :iid WHERE check_id = :chk AND created_at >= :start"), {'iid': iid, 'chk': check_id, 'start': started_at})
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(text('ALTER TABLE event DROP CONSTRAINT IF EXISTS fk_event_incident'))
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE incident DROP COLUMN IF EXISTS share_token"))
    except Exception:
        pass
