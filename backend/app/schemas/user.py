"""
Pydantic schemas for User CRUD.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class UserBase(BaseModel):
    email: str
    username: str = Field(..., min_length=3, max_length=150)
    full_name: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=150)
    full_name: str | None = None
    password: str = Field(..., min_length=8, max_length=128)
    is_superadmin: bool = False
    tenant_id: str | None = None  # Optional: assign to tenant on creation
    tenant_role: str = "user"  # Role within the tenant (user, tenant_admin, readonly)
    max_vms: int | None = Field(None, ge=1, le=1000)
    assigned_vm_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    is_active: bool | None = None


class UserSshKeyItem(BaseModel):
    id: str
    name: str
    public_key: str
    fingerprint: str
    created_at: str


class UserResponse(UserBase):
    id: uuid.UUID
    is_active: bool
    is_superadmin: bool
    totp_enabled: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserProfileResponse(UserResponse):
    """Current-user profile including decrypted SSH public keys."""

    ssh_keys: list[UserSshKeyItem] = []


class UserWithRoles(UserResponse):
    tenant_memberships: list["TenantMembership"] = []


class TenantMembership(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}
