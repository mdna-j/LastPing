"""add lease fencing token

Revision ID: 0033_add_lease_fence
Revises: 0032_add_predictive_model_quality
Create Date: 2026-02-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0033_add_lease_fence"
down_revision = "0032_add_predictive_model_quality"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("check_lease")}

    if "lease_fence" not in columns:
        op.add_column(
            "check_lease",
            sa.Column("lease_fence", sa.Integer(), nullable=False, server_default="0"),
        )

    # SQLite does not support ALTER COLUMN ... DROP DEFAULT.
    if bind.dialect.name != "sqlite":
        op.alter_column("check_lease", "lease_fence", server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("check_lease")}
    if "lease_fence" in columns:
        op.drop_column("check_lease", "lease_fence")
