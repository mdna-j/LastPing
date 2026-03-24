"""add browser checks

Revision ID: 0038_add_browser_checks
Revises: 0037_add_status_subscriptions
Create Date: 2026-03-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0038_add_browser_checks"
down_revision = "0037_add_status_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("check")}

    if "browser_steps" not in cols:
        op.add_column("check", sa.Column("browser_steps", sa.Text(), nullable=True))
    if "browser_capture_screenshot" not in cols:
        op.add_column(
            "check",
            sa.Column("browser_capture_screenshot", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("check")}

    if "browser_capture_screenshot" in cols:
        op.drop_column("check", "browser_capture_screenshot")
    if "browser_steps" in cols:
        op.drop_column("check", "browser_steps")
