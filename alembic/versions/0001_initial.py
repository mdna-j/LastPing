"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-01-12 00:00:00.000000
"""
from alembic import op
from sqlmodel import SQLModel

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind)
