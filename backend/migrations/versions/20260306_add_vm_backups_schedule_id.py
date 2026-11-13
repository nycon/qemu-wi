"""Add schedule_id to vm_backups for scheduled backups.

Revision ID: 20260306_vm_backups_schedule_id
Revises: 20260306_cloud_image_userdata
Create Date: 2026-03-06
"""

from alembic import op


revision = "20260306_vm_backups_schedule_id"
down_revision = "20260306_cloud_image_userdata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Column (idempotent)
    op.execute(
        """
        ALTER TABLE vm_backups
        ADD COLUMN IF NOT EXISTS schedule_id UUID
        """
    )

    # Index (idempotent)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_vm_backups_schedule_id
        ON vm_backups (schedule_id)
        """
    )

    # FK (idempotent)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_vm_backups_schedule_id_backup_schedules'
            ) THEN
                ALTER TABLE vm_backups
                ADD CONSTRAINT fk_vm_backups_schedule_id_backup_schedules
                FOREIGN KEY (schedule_id)
                REFERENCES backup_schedules(id)
                ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE vm_backups
        DROP CONSTRAINT IF EXISTS fk_vm_backups_schedule_id_backup_schedules
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_vm_backups_schedule_id")
    op.execute("ALTER TABLE vm_backups DROP COLUMN IF EXISTS schedule_id")

