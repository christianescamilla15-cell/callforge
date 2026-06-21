"""knowledge document embedding column

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("embedding", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.drop_column("embedding")
