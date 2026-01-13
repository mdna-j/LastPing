"""drop plaintext api_key column

Revision ID: 0005_drop_plaintext_api_key
Revises: 0004_migrate_api_keys_and_webhooks
Create Date: 2026-01-12 01:00:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0005_drop_plaintext_api_key'
down_revision = '0004_migrate_api_keys_and_webhooks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove the legacy `api_key` column. For SQLite we need to recreate the table.
    with engine.connect() as conn:
        dialect = conn.dialect.name
        if dialect == 'sqlite':
            # Recreate the `project` table without the legacy `api_key` column.
            # We create a new table with the expected schema (omitting `api_key`),
            # copy existing data for the kept columns, then swap tables.
            res = conn.execute(text("PRAGMA table_info('project')")).fetchall()
            cols = [r[1] for r in res]
            if 'api_key' in cols:
                # columns we want to keep (explicit list to avoid surprises)
                keep = [
                    'id', 'name', 'api_key_hash', 'created_at', 'owner_email',
                    'alert_rate_limit_count', 'alert_rate_limit_window', 'last_escalated_at',
                    'discord_webhook_url', 'slack_webhook_url', 'pagerduty_integration_key', 'generic_webhook_url'
                ]
                keep_existing = [c for c in keep if c in cols]
                cols_csv = ','.join(keep_existing)
                # ensure any leftover temp table is removed
                conn.execute(text('DROP TABLE IF EXISTS project_new'))
                # create new table with explicit column types matching the model
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
                    'generic_webhook_url TEXT'
                    ')' ))
                # copy data for existing columns
                conn.execute(text(f"INSERT INTO project_new ({cols_csv}) SELECT {cols_csv} FROM project"))
                conn.execute(text('DROP TABLE project'))
                conn.execute(text('ALTER TABLE project_new RENAME TO project'))
        else:
            # for databases that support DROP COLUMN
            try:
                conn.execute(text('ALTER TABLE project DROP COLUMN api_key'))
            except Exception:
                # ignore if column is already removed or unsupported
                pass


def downgrade() -> None:
    # No-op: this migration deliberately drops data/column permanently.
    pass
