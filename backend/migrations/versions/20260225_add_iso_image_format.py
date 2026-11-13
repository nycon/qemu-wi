"""Add ISO value to imageformat enum.

Revision ID: 20260225_iso_format
Revises: 20260220_cluster
Create Date: 2026-02-25
"""

from alembic import op


revision = "20260225_iso_format"
down_revision = "20260220_cluster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE imageformat ADD VALUE IF NOT EXISTS 'iso'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the type.
    pass

