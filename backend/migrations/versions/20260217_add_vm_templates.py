"""Create vm_templates table.

Revision ID: 20260217_templates
Revises: 20260213_disk_mgmt
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "20260217_templates"
down_revision = "20260213_disk_mgmt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vm_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon", sa.String(50), nullable=False, server_default="server"),
        sa.Column("vcpus", sa.Integer, nullable=False, server_default="2"),
        sa.Column("ram_mb", sa.Integer, nullable=False, server_default="2048"),
        sa.Column("disk_size_gb", sa.Integer, nullable=False, server_default="20"),
        sa.Column("cloud_image_id", UUID(as_uuid=True), nullable=True),
        sa.Column("default_user", sa.String(50), nullable=False, server_default="cloud"),
        sa.Column("packages", JSONB, nullable=True),
        sa.Column("runcmd", JSONB, nullable=True),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="Europe/Berlin"),
        sa.Column("keyboard_layout", sa.String(10), nullable=False, server_default="de"),
        sa.Column("locale", sa.String(20), nullable=True),
        sa.Column("ssh_keys", JSONB, nullable=True),
        sa.Column("is_global", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["cloud_image_id"], ["cloud_images.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("vm_templates")
