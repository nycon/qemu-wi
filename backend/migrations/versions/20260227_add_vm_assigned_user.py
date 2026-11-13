"""Add assigned_user_id to virtual_machines.

Revision ID: 20260227_vm_assigned_user
Revises: 20260226_internal_dhcp
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260227_vm_assigned_user"
down_revision = "20260226_internal_dhcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "virtual_machines",
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_virtual_machines_assigned_user_id_users",
        "virtual_machines",
        "users",
        ["assigned_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_virtual_machines_assigned_user_id",
        "virtual_machines",
        ["assigned_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_virtual_machines_assigned_user_id", table_name="virtual_machines")
    op.drop_constraint("fk_virtual_machines_assigned_user_id_users", "virtual_machines", type_="foreignkey")
    op.drop_column("virtual_machines", "assigned_user_id")
