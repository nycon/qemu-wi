"""Add per-user VM limit in tenant membership.

Revision ID: 20260227_tenant_user_max_vms
Revises: 20260227_vm_assigned_user
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260227_tenant_user_max_vms"
down_revision = "20260227_vm_assigned_user"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_users", sa.Column("max_vms", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant_users", "max_vms")
