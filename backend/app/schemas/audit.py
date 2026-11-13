"""
Pydantic schemas for Audit Logs.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    actor_id: uuid.UUID | None
    actor_email: str | None
    actor_ip: str | None
    action: str
    resource_type: str
    resource_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    description: str | None
    metadata_json: dict | None
    status: str

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
