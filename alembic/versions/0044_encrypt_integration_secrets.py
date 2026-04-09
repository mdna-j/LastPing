"""encrypt integration secrets at rest

Revision ID: 0044_encrypt_integration_secrets
Revises: 0043_add_user_token_fingerprint
Create Date: 2026-04-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

from src.security import encrypt_secret, is_encrypted_secret


revision = "0044_encrypt_integration_secrets"
down_revision = "0043_add_user_token_fingerprint"
branch_labels = None
depends_on = None


def _columns(inspector, table_name: str):
    return {col["name"] for col in inspector.get_columns(table_name)}


def _backfill_secret_columns(bind, inspector, table_name: str, pk_name: str, candidate_columns: list[str]) -> None:
    existing_columns = _columns(inspector, table_name)
    secret_columns = [name for name in candidate_columns if name in existing_columns]
    if not secret_columns:
        return

    table = sa.table(
        table_name,
        sa.column(pk_name, sa.Integer),
        *[sa.column(name, sa.Text) for name in secret_columns],
    )
    rows = bind.execute(
        sa.select(
            table.c[pk_name],
            *[table.c[name] for name in secret_columns],
        )
    ).fetchall()
    for row in rows:
        updates = {}
        for column_name in secret_columns:
            value = getattr(row, column_name)
            if not value or is_encrypted_secret(value):
                continue
            encrypted = encrypt_secret(value)
            if encrypted and encrypted != value:
                updates[column_name] = encrypted
        if updates:
            bind.execute(
                table.update()
                .where(table.c[pk_name] == getattr(row, pk_name))
                .values(**updates)
            )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _backfill_secret_columns(
        bind,
        inspector,
        "project",
        "id",
        [
            "discord_webhook_url",
            "slack_webhook_url",
            "pagerduty_integration_key",
            "generic_webhook_url",
            "jira_user_email",
            "jira_api_token",
            "sms_to",
            "sms_from",
            "sms_account_sid",
            "sms_auth_token",
            "oncall_email",
        ],
    )
    _backfill_secret_columns(
        bind,
        inspector,
        "check",
        "id",
        [
            "alert_sms_to",
            "alert_oncall_email",
            "alert_slack_webhook_url",
            "alert_discord_webhook_url",
            "alert_pagerduty_integration_key",
            "alert_generic_webhook_url",
        ],
    )
    _backfill_secret_columns(
        bind,
        inspector,
        "remediation_hook",
        "id",
        ["secret"],
    )


def downgrade():
    # Secret encryption is intentionally non-reversible at the schema level.
    pass
