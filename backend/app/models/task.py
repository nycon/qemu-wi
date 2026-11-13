"""
Task / Job tracking model for async operations.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskType(str, enum.Enum):
    VM_CREATE = "vm_create"
    VM_START = "vm_start"
    VM_STOP = "vm_stop"
    VM_REBOOT = "vm_reboot"
    VM_DELETE = "vm_delete"
    VM_FORCE_STOP = "vm_force_stop"
    DISK_CREATE = "disk_create"
    DISK_RESIZE = "disk_resize"
    DISK_DELETE = "disk_delete"
    VM_CLONE = "vm_clone"
    IMAGE_IMPORT = "image_import"
    BACKUP_CREATE = "backup_create"
    BACKUP_RESTORE = "backup_restore"
    VM_EXPORT = "vm_export"
    VM_IMPORT = "vm_import"


class Task(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tasks"

    # Task identity
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, values_callable=lambda x: [e.value for e in x]), nullable=False,
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, values_callable=lambda x: [e.value for e in x]),
        default=TaskStatus.QUEUED, nullable=False, index=True,
    )

    # Actor
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Resource
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Execution
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    progress_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Output
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(default=3, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )
