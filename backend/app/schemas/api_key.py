"""
Pydantic schemas for API key management.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    # Required for non-superadmin. Superadmin may omit for a global (unscoped) key.
    tenant_id: uuid.UUID | None = None
    expires_in_days: int | None = Field(None, ge=1, le=365)  # None = no expiry


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    key_prefix: str
    tenant_id: uuid.UUID | None
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class APIKeyCreatedResponse(APIKeyResponse):
    """Response when a key is first created – includes the plaintext key (shown only once!)."""
    api_key: str  # The full plaintext key – shown only at creation!


class APIKeyListResponse(BaseModel):
    items: list[APIKeyResponse]
    total: int
