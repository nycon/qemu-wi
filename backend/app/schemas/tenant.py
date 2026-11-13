"""
Pydantic schemas for Tenant and Project CRUD.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_optional_email(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 255 or not _EMAIL_RE.match(cleaned):
        raise ValueError("Invalid email address")
    return cleaned.lower()


class TenantBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None


class TenantCreate(TenantBase):
    slug: str = Field(..., min_length=2, max_length=255, pattern=r"^[a-z0-9-]+$")
    max_vms: int = Field(default=10, ge=1)
    max_vcpus: int = Field(default=32, ge=1)
    max_ram_mb: int = Field(default=65536, ge=512)
    max_storage_gb: int = Field(default=500, ge=10)
    # Ceiling / display; also synced from public_ip_allocation_ids length when provided
    max_ips: int = Field(default=0, ge=0)
    notification_email: str | None = Field(None, max_length=255)
    # Optional: assign an existing user as tenant_admin on create
    admin_user_id: uuid.UUID | None = None
    # Specific public IPs (allocation UUIDs) to reserve for this tenant
    public_ip_allocation_ids: list[uuid.UUID] | None = None

    @field_validator("notification_email")
    @classmethod
    def _email_create(cls, v: str | None) -> str | None:
        return _validate_optional_email(v)


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    max_vms: int | None = Field(None, ge=1)
    max_vcpus: int | None = Field(None, ge=1)
    max_ram_mb: int | None = Field(None, ge=512)
    max_storage_gb: int | None = Field(None, ge=10)
    max_ips: int | None = Field(None, ge=0)
    notification_email: str | None = Field(None, max_length=255)
    # Optional: assign / change tenant_admin (omit = leave memberships unchanged)
    admin_user_id: uuid.UUID | None = None
    # None = leave assignments unchanged; list = replace set (empty = unassign all free)
    public_ip_allocation_ids: list[uuid.UUID] | None = None

    @field_validator("notification_email")
    @classmethod
    def _email_update(cls, v: str | None) -> str | None:
        return _validate_optional_email(v)


class TenantNotificationEmailUpdate(BaseModel):
    """Tenant-admin scoped update for the notification inbox only."""

    notification_email: str | None = Field(None, max_length=255)

    @field_validator("notification_email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        return _validate_optional_email(v)


class TenantMemberResponse(BaseModel):
    user_id: uuid.UUID
    username: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class TenantResponse(TenantBase):
    id: uuid.UUID
    slug: str
    is_active: bool
    max_vms: int
    max_vcpus: int
    max_ram_mb: int
    max_storage_gb: int
    max_ips: int
    notification_email: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Project ---


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = None
    is_active: bool | None = None


class ProjectResponse(ProjectBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
