"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-01-12 00:00:00.000000
"""
from sqlmodel import SQLModel
from src.db import engine
import src.models  # ensure models are imported and registered

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    SQLModel.metadata.create_all(engine)


def downgrade() -> None:
    SQLModel.metadata.drop_all(engine)
