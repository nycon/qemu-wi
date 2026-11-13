"""Add internal DHCP toggle to IP pools.

Revision ID: 20260226_internal_dhcp
Revises: 20260225_iso_format
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260226_internal_dhcp"
down_revision = "20260225_iso_format"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ip_pools",
        sa.Column(
            "internal_dhcp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ip_pools", "internal_dhcp_enabled")
