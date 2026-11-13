"""
Pydantic schemas for VM backup operations and scheduled backups.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class BackupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1024)


class BackupResponse(BaseModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    description: str | None
    backup_type: str
    status: str
    file_path: str | None
    file_size_bytes: int | None
    storage_backend: str = "internal"
    remote_path: str | None = None
    vm_name: str | None
    vm_vcpus: int | None
    vm_ram_mb: int | None
    vm_disk_size_gb: int | None
    error_message: str | None
    schedule_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BackupListResponse(BaseModel):
    items: list[BackupResponse]
    total: int


# ─── Backup Schedules ───────────────────────────────────────────────────────


class BackupScheduleCreate(BaseModel):
    frequency: str = Field(..., pattern=r"^(daily|weekly|monthly)$")
    time_utc: str = Field("03:00", pattern=r"^\d{2}:\d{2}$")
    day_of_week: int | None = Field(None, ge=0, le=6)  # 0=Mon..6=Sun
    day_of_month: int | None = Field(None, ge=1, le=28)
    retention_count: int = Field(7, ge=1, le=365)


class BackupScheduleUpdate(BaseModel):
    frequency: str | None = Field(None, pattern=r"^(daily|weekly|monthly)$")
    time_utc: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    day_of_week: int | None = None
    day_of_month: int | None = None
    retention_count: int | None = Field(None, ge=1, le=365)
    is_active: bool | None = None


class BackupScheduleResponse(BaseModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    frequency: str
    time_utc: str
    day_of_week: int | None
    day_of_month: int | None
    retention_count: int
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
