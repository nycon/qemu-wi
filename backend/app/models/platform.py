"""
Platform-wide settings and database backup tracking.
"""

import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class PlatformSetting(Base, UUIDMixin, TimestampMixin):
    """Key/value JSON settings (singleton keys, e.g. vm_backup_storage)."""

    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DatabaseBackupStatus(str, enum.Enum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    RESTORING = "restoring"
    DELETED = "deleted"


class DatabaseBackup(Base, UUIDMixin, TimestampMixin):
    """PostgreSQL dump created by Superadmin."""

    __tablename__ = "database_backups"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[DatabaseBackupStatus] = mapped_column(
        Enum(DatabaseBackupStatus, values_callable=lambda x: [e.value for e in x]),
        default=DatabaseBackupStatus.CREATING,
        nullable=False,
    )
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )
