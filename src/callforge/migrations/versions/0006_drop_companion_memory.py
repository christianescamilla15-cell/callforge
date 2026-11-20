"""drop companion memories (companion mode retired)

The companion mode left the product (support platform only). 0005 stays in
the chain so existing databases keep a linear history; this revision removes
its table. ``downgrade`` recreates the table exactly as 0005 created it (the
rows themselves are not restorable: back the database up before upgrading
if they matter).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companion_memories", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_companion_memories_tenant_id"))
    op.drop_table("companion_memories")


def downgrade() -> None:
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
