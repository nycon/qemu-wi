"""Add name, target_dev, status columns to vm_disks table.

Revision ID: 20260213_disk_mgmt
Revises: None
Create Date: 2026-02-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260213_disk_mgmt"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns with defaults so existing rows are handled
    op.add_column(
        "vm_disks",
        sa.Column("name", sa.String(255), nullable=False, server_default="data"),
    )
    op.add_column(
        "vm_disks",
        sa.Column("target_dev", sa.String(10), nullable=False, server_default="vdb"),
    )
    op.add_column(
        "vm_disks",
        sa.Column(
            "status",
            sa.Enum("creating", "available", "attached", "detaching", "error", "deleting", name="diskstatus"),
            nullable=False,
            server_default="attached",
        ),
    )


def downgrade() -> None:
    op.drop_column("vm_disks", "status")
    op.drop_column("vm_disks", "target_dev")
    op.drop_column("vm_disks", "name")
    sa.Enum(name="diskstatus").drop(op.get_bind(), checkfirst=True)
