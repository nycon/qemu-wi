"""
Pydantic schemas for VM Templates / Presets.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class VMTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    icon: str = "server"

    # Hardware
    vcpus: int = Field(default=2, ge=1, le=128)
    ram_mb: int = Field(default=2048, ge=256, le=524288)
    disk_size_gb: int = Field(default=20, ge=5, le=10000)

    # Cloud image
    cloud_image_id: uuid.UUID | None = None

    # Cloud-Init
    default_user: str = "cloud"
    packages: list[str] = []
    runcmd: list[str] = []
    timezone: str = "Europe/Berlin"
    keyboard_layout: str = "de"
    locale: str | None = None
    ssh_keys: list[str] = []

    # Visibility
    is_global: bool = False


class VMTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    icon: str | None = None
    vcpus: int | None = Field(None, ge=1, le=128)
    ram_mb: int | None = Field(None, ge=256, le=524288)
    disk_size_gb: int | None = Field(None, ge=5, le=10000)
    cloud_image_id: uuid.UUID | None = None
    default_user: str | None = None
    packages: list[str] | None = None
    runcmd: list[str] | None = None
    timezone: str | None = None
    keyboard_layout: str | None = None
    locale: str | None = None
    ssh_keys: list[str] | None = None
    is_global: bool | None = None
    is_active: bool | None = None


class VMTemplateResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    created_by: uuid.UUID
    name: str
    description: str | None
    icon: str
    vcpus: int
    ram_mb: int
    disk_size_gb: int
    cloud_image_id: uuid.UUID | None
    default_user: str
    packages: list | None
    runcmd: list | None
    timezone: str
    keyboard_layout: str
    locale: str | None
    ssh_keys: list | None
    is_global: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
