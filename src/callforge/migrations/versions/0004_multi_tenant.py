"""tenants table + tenant_id on root aggregates

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("customers", "conversations", "tickets", "knowledge_documents")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("api_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tenants", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tenants_api_key"), ["api_key"], unique=True)

    # Default tenant: empty api_key = reachable only via the global API_TOKEN
    # (or open local mode), never via tenant-key auth.
    op.execute(
        "INSERT INTO tenants (id, name, api_key, created_at) "
        "VALUES ('default', 'Default', '', CURRENT_TIMESTAMP)"
    )

    for table in _TENANT_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tenant_id",
                    sa.String(length=32),
                    nullable=False,
                    server_default="default",
                )
            )
            batch_op.create_index(
                batch_op.f(f"ix_{table}_tenant_id"), ["tenant_id"], unique=False
            )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table}_tenant_id"))
            batch_op.drop_column("tenant_id")
    op.drop_table("tenants")
