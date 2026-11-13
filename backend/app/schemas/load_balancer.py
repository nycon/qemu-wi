"""Pydantic schemas for Load Balancer."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LBTargetCreate(BaseModel):
    vm_id: uuid.UUID
    target_port: int = Field(..., ge=1, le=65535)
    weight: int = Field(default=1, ge=1, le=100)
    # Backends are always reached via NAT/internal IP
    ip_mode: str = Field(default="nat", pattern=r"^(nat|public)$")
    preferred_ip: str | None = None


class LBTargetUpdate(BaseModel):
    target_port: int | None = Field(default=None, ge=1, le=65535)
    weight: int | None = Field(default=None, ge=1, le=100)
    is_active: bool | None = None


class LBTargetResponse(BaseModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    vm_name: str | None = None
    vm_ip: str | None = None
    ip_mode: str = "nat"
    preferred_ip: str | None = None
    available_ips: dict[str, list[str]] | None = None
    target_port: int
    weight: int
    is_active: bool
    created_at: datetime


class LBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    protocol: str = Field(default="http")
    algorithm: str = Field(default="round_robin")
    listen_port: int = Field(..., ge=1, le=65535)
    # One public VIP for the whole LB (clients → VIP → NAT backends)
    public_ip: str | None = None
    health_check_path: str | None = "/"
    health_check_interval: int = Field(default=10, ge=5, le=300)
    tenant_id: uuid.UUID | None = None
    targets: list[LBTargetCreate] = []


class LBUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    algorithm: str | None = None
    public_ip: str | None = None
    health_check_path: str | None = None
    health_check_interval: int | None = Field(None, ge=5, le=300)
    is_active: bool | None = None


class LBResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None
    protocol: str
    algorithm: str
    listen_port: int
    public_ip: str | None = None
    health_check_path: str | None
    health_check_interval: int
    is_active: bool
    targets: list[LBTargetResponse] = []
    created_at: datetime
    updated_at: datetime
