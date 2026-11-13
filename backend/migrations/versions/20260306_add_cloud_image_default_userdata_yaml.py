"""Add default user-data YAML field to cloud images.

Revision ID: 20260306_cloud_image_userdata
Revises: 20260227_tenant_user_max_vms
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260306_cloud_image_userdata"
down_revision = "20260227_tenant_user_max_vms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cloud_images",
        sa.Column("default_userdata_yaml", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_images", "default_userdata_yaml")

