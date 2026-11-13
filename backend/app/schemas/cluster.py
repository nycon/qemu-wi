"""Pydantic schemas for Cluster Node management."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ClusterNodeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)


class ClusterNodeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class ClusterNodeResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    url: str
    api_key_prefix: str
    status: str
    is_active: bool
    last_seen_at: datetime | None
    last_error: str | None
    total_vms: int
    running_vms: int
    total_vcpus: int
    total_ram_mb: int
    used_vcpus: int
    used_ram_mb: int
    total_storage_gb: int
    used_storage_gb: int
    app_version: str | None
    created_at: datetime
    updated_at: datetime


class ClusterVMItem(BaseModel):
    id: str
    name: str
    hostname: str
    status: str
    vcpus: int
    ram_mb: int
    disk_size_gb: int
    node_name: str
    node_id: str


class ClusterOverview(BaseModel):
    total_nodes: int
    online_nodes: int
    total_vms: int
    running_vms: int
    total_vcpus: int
    used_vcpus: int
    total_ram_mb: int
    used_ram_mb: int
    total_storage_gb: int
    used_storage_gb: int
    nodes: list[ClusterNodeResponse]
    vms: list[ClusterVMItem]
