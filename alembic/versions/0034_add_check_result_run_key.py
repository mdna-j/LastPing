"""add check_result run_key idempotency column

Revision ID: 0034_add_check_result_run_key
Revises: 0033_add_lease_fence
Create Date: 2026-02-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0034_add_check_result_run_key"
down_revision = "0033_add_lease_fence"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("check_result")}
    indexes = {idx["name"] for idx in inspector.get_indexes("check_result")}

    if "run_key" not in columns:
        op.add_column("check_result", sa.Column("run_key", sa.String(length=160), nullable=True))
    if "ix_check_result_run_key" not in indexes:
        op.create_index("ix_check_result_run_key", "check_result", ["run_key"], unique=False)
    if "ux_check_result_check_id_run_key" not in indexes:
        op.create_index("ux_check_result_check_id_run_key", "check_result", ["check_id", "run_key"], unique=True)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("check_result")}
    indexes = {idx["name"] for idx in inspector.get_indexes("check_result")}

    if "ux_check_result_check_id_run_key" in indexes:
        op.drop_index("ux_check_result_check_id_run_key", table_name="check_result")
    if "ix_check_result_run_key" in indexes:
        op.drop_index("ix_check_result_run_key", table_name="check_result")
    if "run_key" in columns:
        op.drop_column("check_result", "run_key")
