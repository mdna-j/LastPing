"""add event_types to oncall escalation

Revision ID: 0027_add_oncall_escalation_event_types
Revises: 0026_add_availability_rollup
Create Date: 2026-01-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0027_add_oncall_escalation_event_types"
down_revision = "0026_add_availability_rollup"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("oncall_escalation")}
    if "event_types" not in cols:
        op.add_column("oncall_escalation", sa.Column("event_types", sa.String(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("oncall_escalation")}
    if "event_types" in cols:
        op.drop_column("oncall_escalation", "event_types")
