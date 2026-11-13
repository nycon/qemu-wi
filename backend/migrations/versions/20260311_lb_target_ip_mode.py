"""Add ip_mode to lb_targets (nat vs public).

Revision ID: 20260311_lb_ip_mode
Revises: 20260320_project_users
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa


revision = "20260311_lb_ip_mode"
down_revision = "20260320_project_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lb_targets",
        sa.Column("ip_mode", sa.String(20), nullable=False, server_default="nat"),
    )


def downgrade() -> None:
    op.drop_column("lb_targets", "ip_mode")
