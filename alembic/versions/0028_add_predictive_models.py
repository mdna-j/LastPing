"""add predictive model storage

Revision ID: 0028_add_predictive_models
Revises: 0027_add_oncall_escalation_event_types
Create Date: 2026-02-04 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0028_add_predictive_models"
down_revision = "0027_add_oncall_escalation_event_types"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    indexes = {idx["name"] for idx in inspector.get_indexes("predictive_model")} if "predictive_model" in tables else set()

    if "predictive_model" not in tables:
        op.create_table(
            "predictive_model",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
            sa.Column("check_id", sa.Integer(), sa.ForeignKey("check.id"), nullable=True),
            sa.Column("model_type", sa.String(), nullable=False, server_default="seasonal_hourly_v1"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("trained_at", sa.DateTime(), nullable=False),
            sa.Column("window_start", sa.DateTime(), nullable=True),
            sa.Column("window_end", sa.DateTime(), nullable=True),
            sa.Column("params_json", sa.Text(), nullable=False),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        indexes = set()

    if "ix_predictive_model_project_id" not in indexes:
        op.create_index("ix_predictive_model_project_id", "predictive_model", ["project_id"])
    if "ix_predictive_model_check_id" not in indexes:
        op.create_index("ix_predictive_model_check_id", "predictive_model", ["check_id"])
    if "ix_predictive_model_type" not in indexes:
        op.create_index("ix_predictive_model_type", "predictive_model", ["model_type"])
    if "ix_predictive_model_active" not in indexes:
        op.create_index("ix_predictive_model_active", "predictive_model", ["active"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "predictive_model" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("predictive_model")}
    if "ix_predictive_model_active" in indexes:
        op.drop_index("ix_predictive_model_active", table_name="predictive_model")
    if "ix_predictive_model_type" in indexes:
        op.drop_index("ix_predictive_model_type", table_name="predictive_model")
    if "ix_predictive_model_check_id" in indexes:
        op.drop_index("ix_predictive_model_check_id", table_name="predictive_model")
    if "ix_predictive_model_project_id" in indexes:
        op.drop_index("ix_predictive_model_project_id", table_name="predictive_model")
    op.drop_table("predictive_model")
