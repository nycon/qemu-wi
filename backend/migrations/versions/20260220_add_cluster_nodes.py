"""Create cluster_nodes table.

Revision ID: 20260220_cluster
Revises: 20260220_load_balancer
Create Date: 2026-02-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "20260220_cluster"
down_revision = "20260220_load_balancer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cluster_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("api_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("total_vms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("running_vms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_vcpus", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_ram_mb", sa.Integer, nullable=False, server_default="0"),
        sa.Column("used_vcpus", sa.Integer, nullable=False, server_default="0"),
        sa.Column("used_ram_mb", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_storage_gb", sa.Integer, nullable=False, server_default="0"),
        sa.Column("used_storage_gb", sa.Integer, nullable=False, server_default="0"),
        sa.Column("app_version", sa.String(50), nullable=True),
        sa.Column("cached_data", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cluster_nodes")
