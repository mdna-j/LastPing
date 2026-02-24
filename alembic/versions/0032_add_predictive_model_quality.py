"""add predictive model quality monitoring table

Revision ID: 0032_add_predictive_model_quality
Revises: 0031_add_anomaly_table
Create Date: 2026-02-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0032_add_predictive_model_quality"
down_revision = "0031_add_anomaly_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "predictive_model_quality",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("predictive_model_id", sa.Integer(), sa.ForeignKey("predictive_model.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("check.id"), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mae", sa.Float(), nullable=True),
        sa.Column("rmse", sa.Float(), nullable=True),
        sa.Column("mape", sa.Float(), nullable=True),
        sa.Column("drift_ratio", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_predictive_model_quality_model_id", "predictive_model_quality", ["predictive_model_id"])
    op.create_index("ix_predictive_model_quality_project_id", "predictive_model_quality", ["project_id"])
    op.create_index("ix_predictive_model_quality_check_id", "predictive_model_quality", ["check_id"])
    op.create_index("ix_predictive_model_quality_status", "predictive_model_quality", ["status"])
    op.create_index("ix_predictive_model_quality_created_at", "predictive_model_quality", ["created_at"])


def downgrade():
    op.drop_index("ix_predictive_model_quality_created_at", table_name="predictive_model_quality")
    op.drop_index("ix_predictive_model_quality_status", table_name="predictive_model_quality")
    op.drop_index("ix_predictive_model_quality_check_id", table_name="predictive_model_quality")
    op.drop_index("ix_predictive_model_quality_project_id", table_name="predictive_model_quality")
    op.drop_index("ix_predictive_model_quality_model_id", table_name="predictive_model_quality")
    op.drop_table("predictive_model_quality")

