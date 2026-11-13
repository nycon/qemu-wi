"""Add project_users mapping table for user project assignments.

Revision ID: 20260320_project_users
Revises: 20260306_vm_backups_schedule_id
Create Date: 2026-03-20
"""

from alembic import op


revision = "20260320_project_users"
down_revision = "20260306_vm_backups_schedule_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_users (
            project_id UUID NOT NULL,
            user_id UUID NOT NULL,
            id UUID NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_project_user'
            ) THEN
                ALTER TABLE project_users
                ADD CONSTRAINT uq_project_user UNIQUE (project_id, user_id);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'project_users_project_id_fkey'
            ) THEN
                ALTER TABLE project_users
                ADD CONSTRAINT project_users_project_id_fkey
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'project_users_user_id_fkey'
            ) THEN
                ALTER TABLE project_users
                ADD CONSTRAINT project_users_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_project_users_project_id
        ON project_users (project_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_project_users_user_id
        ON project_users (user_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_project_users_user_id")
    op.execute("DROP INDEX IF EXISTS ix_project_users_project_id")
    op.execute(
        """
        ALTER TABLE project_users
        DROP CONSTRAINT IF EXISTS project_users_project_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE project_users
        DROP CONSTRAINT IF EXISTS project_users_user_id_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE project_users
        DROP CONSTRAINT IF EXISTS uq_project_user
        """
    )
    op.execute("DROP TABLE IF EXISTS project_users")
