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
            # get existing columns
            res = conn.execute(text("PRAGMA table_info('project')")).fetchall()
            cols = [r[1] for r in res]
            if 'api_key' in cols:
                keep = [c for c in cols if c != 'api_key']
                cols_csv = ','.join(keep)
                conn.execute(text(f"CREATE TABLE project_new AS SELECT {cols_csv} FROM project WHERE 0"))
                conn.execute(text(f"INSERT INTO project_new ({cols_csv}) SELECT {cols_csv} FROM project"))
                conn.execute(text('DROP TABLE project'))
                conn.execute(text('ALTER TABLE project_new RENAME TO project'))
        else:
            # for databases that support DROP COLUMN
            try:
                conn.execute(text('ALTER TABLE project DROP COLUMN api_key'))
            except Exception:
                # best-effort; if it fails, raise to ensure migration author inspects
                raise


def downgrade() -> None:
    # No-op: this migration deliberately drops data/column permanently.
    pass
