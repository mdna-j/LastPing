"""add per-check alert routing and oncall escalation check_id

Revision ID: 0024_add_check_alert_routing_and_oncall_check_id
Revises: 0023_add_sms_and_remediation_controls
Create Date: 2026-01-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0024_add_check_alert_routing_and_oncall_check_id"
down_revision = "0023_add_sms_and_remediation_controls"
branch_labels = None
depends_on = None


def _fk_exists(inspector, table_name: str, column: str, referred_table: str) -> bool:
    try:
        fks = inspector.get_foreign_keys(table_name)
    except Exception:
        return False
    for fk in fks:
        if column in (fk.get("constrained_columns") or []) and fk.get("referred_table") == referred_table:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    dialect = bind.dialect.name
    check_cols = {col["name"] for col in inspector.get_columns("check")}
    esc_cols = {col["name"] for col in inspector.get_columns("oncall_escalation")}

    # per-check channel enablement
    if "alert_sms_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_sms_enabled", sa.Boolean(), nullable=True))
    if "alert_oncall_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_oncall_enabled", sa.Boolean(), nullable=True))
    if "alert_slack_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_slack_enabled", sa.Boolean(), nullable=True))
    if "alert_discord_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_discord_enabled", sa.Boolean(), nullable=True))
    if "alert_pagerduty_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_pagerduty_enabled", sa.Boolean(), nullable=True))
    if "alert_webhook_enabled" not in check_cols:
        op.add_column("check", sa.Column("alert_webhook_enabled", sa.Boolean(), nullable=True))

    # per-check routing overrides
    if "alert_sms_to" not in check_cols:
        op.add_column("check", sa.Column("alert_sms_to", sa.String(), nullable=True))
    if "alert_oncall_email" not in check_cols:
        op.add_column("check", sa.Column("alert_oncall_email", sa.String(), nullable=True))
    if "alert_slack_webhook_url" not in check_cols:
        op.add_column("check", sa.Column("alert_slack_webhook_url", sa.String(), nullable=True))
    if "alert_discord_webhook_url" not in check_cols:
        op.add_column("check", sa.Column("alert_discord_webhook_url", sa.String(), nullable=True))
    if "alert_pagerduty_integration_key" not in check_cols:
        op.add_column("check", sa.Column("alert_pagerduty_integration_key", sa.String(), nullable=True))
    if "alert_generic_webhook_url" not in check_cols:
        op.add_column("check", sa.Column("alert_generic_webhook_url", sa.String(), nullable=True))

    if "check_id" not in esc_cols:
        op.add_column("oncall_escalation", sa.Column("check_id", sa.Integer(), nullable=True))
    if dialect != "sqlite" and not _fk_exists(inspector, "oncall_escalation", "check_id", "check"):
        op.create_foreign_key(
            "fk_oncall_escalation_check_id_check",
            "oncall_escalation",
            "check",
            ["check_id"],
            ["id"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    dialect = bind.dialect.name
    check_cols = {col["name"] for col in inspector.get_columns("check")}
    esc_cols = {col["name"] for col in inspector.get_columns("oncall_escalation")}

    # drop FK if present
    if dialect != "sqlite":
        try:
            for fk in inspector.get_foreign_keys("oncall_escalation"):
                if "check_id" in (fk.get("constrained_columns") or []) and fk.get("referred_table") == "check":
                    op.drop_constraint(fk["name"], "oncall_escalation", type_="foreignkey")
                    break
        except Exception:
            pass

    if "check_id" in esc_cols:
        op.drop_column("oncall_escalation", "check_id")

    if "alert_generic_webhook_url" in check_cols:
        op.drop_column("check", "alert_generic_webhook_url")
    if "alert_pagerduty_integration_key" in check_cols:
        op.drop_column("check", "alert_pagerduty_integration_key")
    if "alert_discord_webhook_url" in check_cols:
        op.drop_column("check", "alert_discord_webhook_url")
    if "alert_slack_webhook_url" in check_cols:
        op.drop_column("check", "alert_slack_webhook_url")
    if "alert_oncall_email" in check_cols:
        op.drop_column("check", "alert_oncall_email")
    if "alert_sms_to" in check_cols:
        op.drop_column("check", "alert_sms_to")
    if "alert_webhook_enabled" in check_cols:
        op.drop_column("check", "alert_webhook_enabled")
    if "alert_pagerduty_enabled" in check_cols:
        op.drop_column("check", "alert_pagerduty_enabled")
    if "alert_discord_enabled" in check_cols:
        op.drop_column("check", "alert_discord_enabled")
    if "alert_slack_enabled" in check_cols:
        op.drop_column("check", "alert_slack_enabled")
    if "alert_oncall_enabled" in check_cols:
        op.drop_column("check", "alert_oncall_enabled")
    if "alert_sms_enabled" in check_cols:
        op.drop_column("check", "alert_sms_enabled")
