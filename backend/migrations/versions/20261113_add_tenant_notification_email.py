"""Add tenants.notification_email for tenant-wide alerts.

Revision ID: 20261113_tenant_notify_email
Revises: 20260311_lb_ip_mode
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20261113_tenant_notify_email"
down_revision = "20260311_lb_ip_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("notification_email", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "notification_email")
