"""Create load_balancers and lb_targets tables.

Revision ID: 20260220_load_balancer
Revises: 20260217_templates
Create Date: 2026-02-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260220_load_balancer"
down_revision = "20260217_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "load_balancers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("protocol", sa.String(10), nullable=False, server_default="http"),
        sa.Column("algorithm", sa.String(20), nullable=False, server_default="round_robin"),
        sa.Column("listen_port", sa.Integer, nullable=False),
        sa.Column("health_check_path", sa.String(255), nullable=True, server_default="/"),
        sa.Column("health_check_interval", sa.Integer, nullable=False, server_default="10"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )

    op.create_table(
        "lb_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("load_balancer_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("vm_id", UUID(as_uuid=True), nullable=False),
        sa.Column("target_port", sa.Integer, nullable=False),
        sa.Column("weight", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["load_balancer_id"], ["load_balancers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("lb_targets")
    op.drop_table("load_balancers")
