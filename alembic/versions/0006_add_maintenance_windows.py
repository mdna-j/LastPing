"""add maintenance windows to project and check

Revision ID: 0006_add_maintenance_windows
Revises: 0005_drop_plaintext_api_key
Create Date: 2026-01-14 00:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0006_add_maintenance_windows'
down_revision = '0005_drop_plaintext_api_key'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == 'sqlite':
            # For SQLite, recreate tables adding the new columns if needed
            # Project table
            res = conn.execute(text("PRAGMA table_info('project')")).fetchall()
            cols = [r[1] for r in res]
            if 'maintenance_starts_at' not in cols or 'maintenance_ends_at' not in cols:
                # create new project table with the added columns
                conn.execute(text('DROP TABLE IF EXISTS project_new'))
                conn.execute(text(
                    'CREATE TABLE project_new ('
                    'id INTEGER PRIMARY KEY,'
                    'name TEXT NOT NULL,'
                    'api_key_hash TEXT,'
                    'created_at DATETIME,'
                    'owner_email TEXT,'
                    'alert_rate_limit_count INTEGER,'
                    'alert_rate_limit_window INTEGER,'
                    'last_escalated_at DATETIME,'
                    'discord_webhook_url TEXT,'
                    'slack_webhook_url TEXT,'
                    'pagerduty_integration_key TEXT,'
                    'generic_webhook_url TEXT,'
                    'maintenance_starts_at DATETIME,'
                    'maintenance_ends_at DATETIME'
                    ')' ))
                keep = [c for c in cols if c in (
                    'id', 'name', 'api_key_hash', 'created_at', 'owner_email',
                    'alert_rate_limit_count', 'alert_rate_limit_window', 'last_escalated_at',
                    'discord_webhook_url', 'slack_webhook_url', 'pagerduty_integration_key', 'generic_webhook_url'
                )]
                cols_csv = ','.join(keep)
                conn.execute(text(f"INSERT INTO project_new ({cols_csv}) SELECT {cols_csv} FROM project"))
                conn.execute(text('DROP TABLE project'))
                conn.execute(text('ALTER TABLE project_new RENAME TO project'))

            # Check table
            res = conn.execute(text("PRAGMA table_info('check')")).fetchall()
            cols = [r[1] for r in res]
            if 'maintenance_starts_at' not in cols or 'maintenance_ends_at' not in cols:
                conn.execute(text('DROP TABLE IF EXISTS check_new'))
                conn.execute(text(
                    'CREATE TABLE check_new ('
                    'id INTEGER PRIMARY KEY,'
                    'project_id INTEGER,'
                    'name TEXT NOT NULL,'
                    'type TEXT,'
                    'expected_interval INTEGER,'
                    'grace_period INTEGER,'
                    'alert_enabled BOOLEAN,'
                    'alert_after INTEGER,'
                    'alert_cooldown INTEGER,'
                    'last_alerted_at DATETIME,'
                    'last_alert_type TEXT,'
                    'url TEXT,'
                    'timeout INTEGER,'
                    'retries INTEGER,'
                    'interval INTEGER,'
                    'next_run DATETIME,'
                    'status TEXT,'
                    'last_ping DATETIME,'
                    'consecutive_failures INTEGER,'
                    'created_at DATETIME,'
                    'maintenance_starts_at DATETIME,'
                    'maintenance_ends_at DATETIME'
                    ')' ))
                keep = [c for c in cols if c in (
                    'id','project_id','name','type','expected_interval','grace_period',
                    'alert_enabled','alert_after','alert_cooldown','last_alerted_at','last_alert_type',
                    'url','timeout','retries','interval','next_run','status','last_ping','consecutive_failures','created_at'
                )]
                cols_csv = ','.join(keep)
                conn.execute(text(f"INSERT INTO check_new ({cols_csv}) SELECT {cols_csv} FROM check"))
                conn.execute(text('DROP TABLE check'))
                conn.execute(text('ALTER TABLE check_new RENAME TO check'))
        else:
            # for databases that support ALTER TABLE ADD COLUMN
            try:
                conn.execute(text('ALTER TABLE project ADD COLUMN maintenance_starts_at DATETIME'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE project ADD COLUMN maintenance_ends_at DATETIME'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE "check" ADD COLUMN maintenance_starts_at DATETIME'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE "check" ADD COLUMN maintenance_ends_at DATETIME'))
            except Exception:
                pass


def downgrade() -> None:
    # Downgrade: remove added columns where possible. For SQLite this is a no-op.
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect != 'sqlite':
            try:
                conn.execute(text('ALTER TABLE project DROP COLUMN maintenance_starts_at'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE project DROP COLUMN maintenance_ends_at'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE "check" DROP COLUMN maintenance_starts_at'))
            except Exception:
                pass
            try:
                conn.execute(text('ALTER TABLE "check" DROP COLUMN maintenance_ends_at'))
            except Exception:
                pass
