"""
Pydantic schemas for Storage Pools and Cloud Images.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.storage import ImageFormat, StoragePoolType


# --- Storage Pool ---


class StoragePoolCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    pool_type: StoragePoolType = StoragePoolType.DIRECTORY
    path: str = Field(..., min_length=1, max_length=1024)
    total_size_gb: int = Field(..., ge=10)
    is_default: bool = False
    description: str | None = None
    # None = shared across all tenants; UUID = exclusive to that tenant
    tenant_id: uuid.UUID | None = None


class StoragePoolUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    path: str | None = Field(None, min_length=1, max_length=1024)
    total_size_gb: int | None = Field(None, ge=10)
    is_active: bool | None = None
    is_default: bool | None = None
    description: str | None = None
    tenant_id: uuid.UUID | None = None


class StoragePoolResponse(BaseModel):
    id: uuid.UUID
    name: str
    pool_type: StoragePoolType
    path: str
    total_size_gb: int
    used_size_gb: int
    free_size_gb: int = 0
    is_active: bool
    is_default: bool
    description: str | None
    tenant_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Cloud Image ---


class CloudImageCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    os_type: str = Field(..., max_length=50)
    os_version: str = Field(..., max_length=50)
    architecture: str = Field(default="x86_64", max_length=20)
    format: ImageFormat = ImageFormat.QCOW2
    description: str | None = None
    min_vcpus: int = Field(default=1, ge=1)
    min_ram_mb: int = Field(default=512, ge=256)
    min_disk_gb: int = Field(default=5, ge=1)
    storage_pool_id: uuid.UUID | None = None
    download_url: str | None = Field(None, max_length=2048)
    default_userdata_yaml: str | None = Field(None, max_length=65535)


class CloudImageResponse(BaseModel):
    id: uuid.UUID
    name: str
    os_type: str
    os_version: str
    architecture: str
    format: ImageFormat
    file_path: str
    file_size_bytes: int
    checksum_sha256: str | None
    is_active: bool
    is_public: bool
    description: str | None
    min_vcpus: int
    min_ram_mb: int
    min_disk_gb: int
    storage_pool_id: uuid.UUID | None
    download_url: str | None
    default_userdata_yaml: str | None
    download_status: str
    download_progress: int
    download_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
