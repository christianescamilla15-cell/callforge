"""companion memories table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companion_memories",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("source_conversation_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("companion_memories", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_companion_memories_tenant_id"), ["tenant_id"], unique=False
        )


def downgrade() -> None:
    op.drop_table("companion_memories")
