"""
VM Backup model for tracking backup/restore operations and scheduled backups.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class BackupStatus(str, enum.Enum):
    CREATING = "creating"
    READY = "ready"
    RESTORING = "restoring"
    FAILED = "failed"
    DELETING = "deleting"


class BackupType(str, enum.Enum):
    FULL = "full"           # Full disk copy
    SNAPSHOT = "snapshot"   # libvirt snapshot-based


class ScheduleFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class VMBackup(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "vm_backups"

    vm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    backup_type: Mapped[BackupType] = mapped_column(
        Enum(BackupType, values_callable=lambda x: [e.value for e in x]),
        default=BackupType.FULL, nullable=False,
    )
    status: Mapped[BackupStatus] = mapped_column(
        Enum(BackupStatus, values_callable=lambda x: [e.value for e in x]),
        default=BackupStatus.CREATING, nullable=False,
    )

    # File info (local path and optional remote backend)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # internal | ftp | s3 — where the backup object lives after create
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False, default="internal", server_default="internal")
    remote_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # VM state at backup time
    vm_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vm_vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vm_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vm_disk_size_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Link to schedule (if created by scheduler)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Relationships
    virtual_machine: Mapped["VirtualMachine"] = relationship("VirtualMachine", back_populates="backups")

    __table_args__ = (
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
        ForeignKeyConstraint(["schedule_id"], ["backup_schedules.id"], ondelete="SET NULL"),
    )


class BackupSchedule(Base, UUIDMixin, TimestampMixin):
    """Scheduled backup configuration for a VM."""

    __tablename__ = "backup_schedules"

    vm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Schedule config
    frequency: Mapped[ScheduleFrequency] = mapped_column(
        Enum(ScheduleFrequency, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    time_utc: Mapped[str] = mapped_column(String(5), nullable=False, default="03:00")  # HH:MM
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon..6=Sun (for weekly)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-28 (for monthly)

    # Retention
    retention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=7)

    # State
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # success / failed

    # Relationships
    virtual_machine: Mapped["VirtualMachine"] = relationship("VirtualMachine", back_populates="backup_schedules")

    __table_args__ = (
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
