"""add script-based checks

Revision ID: 0029_add_script_checks
Revises: 0028_add_predictive_models
Create Date: 2026-02-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0029_add_script_checks"
down_revision = "0028_add_predictive_models"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("check")}
    # Custom script checks (operator-provided scripts under CUSTOM_CHECKS_DIR)
    if "script_path" not in cols:
        op.add_column("check", sa.Column("script_path", sa.String(length=255), nullable=True))
    # JSON-encoded list of args (List[str])
    if "script_args" not in cols:
        op.add_column("check", sa.Column("script_args", sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("check")}
    if "script_args" in cols:
        op.drop_column("check", "script_args")
    if "script_path" in cols:
        op.drop_column("check", "script_path")
