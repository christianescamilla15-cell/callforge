"""resolution steps table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resolution_steps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("expected_check", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("customer_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("resolution_steps", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_resolution_steps_conversation_id"),
            ["conversation_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("resolution_steps")
