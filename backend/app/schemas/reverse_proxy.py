"""Pydantic schemas for Reverse Proxy rules."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ProxyRuleCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255)
    target_ip: str = Field(..., min_length=7, max_length=50)
    target_port: int = Field(80, ge=1, le=65535)
    vm_id: uuid.UUID | None = None
    ssl_mode: str = Field("none", pattern=r"^(none|auto)$")
    websocket_support: bool = False


class ProxyRuleUpdate(BaseModel):
    target_ip: str | None = Field(None, min_length=7, max_length=50)
    target_port: int | None = Field(None, ge=1, le=65535)
    vm_id: uuid.UUID | None = None
    ssl_mode: str | None = Field(None, pattern=r"^(none|auto)$")
    websocket_support: bool | None = None
    is_active: bool | None = None


class ProxyRuleResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    vm_id: uuid.UUID | None
    domain: str
    target_ip: str
    target_port: int
    ssl_mode: str
    ssl_enabled: bool
    websocket_support: bool
    is_active: bool
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProxyRuleListResponse(BaseModel):
    items: list[ProxyRuleResponse]
    total: int
