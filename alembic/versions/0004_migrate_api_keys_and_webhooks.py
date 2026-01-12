"""migrate api keys to hashes and add per-project webhooks

Revision ID: 0004_migrate_api_keys_and_webhooks
Revises: 0003_add_api_key_hash
Create Date: 2026-01-12 00:45:00.000000
"""
from sqlalchemy import text
from src.db import engine

revision = '0004_migrate_api_keys_and_webhooks'
down_revision = '0003_add_api_key_hash'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add per-project webhook columns
    with engine.connect() as conn:
        conn.execute(text('ALTER TABLE "project" ADD COLUMN "discord_webhook_url" TEXT'))
        conn.execute(text('ALTER TABLE "project" ADD COLUMN "slack_webhook_url" TEXT'))
        conn.execute(text('ALTER TABLE "project" ADD COLUMN "pagerduty_integration_key" TEXT'))
        conn.execute(text('ALTER TABLE "project" ADD COLUMN "generic_webhook_url" TEXT'))

        # For any project with plaintext api_key and missing api_key_hash, compute and store the hash
        from src.security import hash_api_key
        res = conn.execute(text('SELECT id, api_key, api_key_hash FROM "project"')).fetchall()
        for row in res:
            pid = row[0]
            api_key = row[1]
            api_key_hash = row[2]
            if api_key and not api_key_hash:
                h = hash_api_key(api_key)
                conn.execute(text('UPDATE "project" SET api_key_hash = :h WHERE id = :id'), {"h": h, "id": pid})

        # Remove plaintext storage by zeroing the column (we keep column in schema for compatibility)
        conn.execute(text('UPDATE "project" SET api_key = NULL'))


def downgrade() -> None:
    # No-op downgrade for destructive migration
    pass
