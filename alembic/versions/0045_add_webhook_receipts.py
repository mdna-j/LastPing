"""add webhook receipts for signed inbound replay protection

Revision ID: 0045_add_webhook_receipts
Revises: 0044_encrypt_integration_secrets
Create Date: 2026-04-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0045_add_webhook_receipts"
down_revision = "0044_encrypt_integration_secrets"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def _indexes(inspector, table_name: str):
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "webhook_receipt" not in _tables(inspector):
        op.create_table(
            "webhook_receipt",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("signature", sa.String(), nullable=False),
            sa.Column("request_timestamp", sa.DateTime(), nullable=False),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source", "signature", name="uq_webhook_receipt_source_signature"),
        )

    inspector = sa.inspect(bind)
    indexes = _indexes(inspector, "webhook_receipt")
    if "ix_webhook_receipt_source" not in indexes:
        op.create_index("ix_webhook_receipt_source", "webhook_receipt", ["source"], unique=False)
    if "ix_webhook_receipt_signature" not in indexes:
        op.create_index("ix_webhook_receipt_signature", "webhook_receipt", ["signature"], unique=False)
    if "ix_webhook_receipt_request_timestamp" not in indexes:
        op.create_index("ix_webhook_receipt_request_timestamp", "webhook_receipt", ["request_timestamp"], unique=False)
    if "ix_webhook_receipt_received_at" not in indexes:
        op.create_index("ix_webhook_receipt_received_at", "webhook_receipt", ["received_at"], unique=False)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "webhook_receipt" not in _tables(inspector):
        return
    indexes = _indexes(inspector, "webhook_receipt")
    for name in (
        "ix_webhook_receipt_received_at",
        "ix_webhook_receipt_request_timestamp",
        "ix_webhook_receipt_signature",
        "ix_webhook_receipt_source",
    ):
        if name in indexes:
            op.drop_index(name, table_name="webhook_receipt")
    op.drop_table("webhook_receipt")
