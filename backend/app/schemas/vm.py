"""
Pydantic schemas for Virtual Machine management.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.vm import DiskFormat, DiskStatus, VMStatus


# --- Cloud-Init Sub-schemas ---


def _validate_vm_password(password: str) -> str:
    """Enforce minimum password complexity for VM accounts."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if not any(c.isupper() for c in password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~" for c in password):
        raise ValueError("Password must contain at least one special character")
    if not any(c.isdigit() for c in password):
        raise ValueError("Password must contain at least one digit")
    return password


class CloudInitIdentity(BaseModel):
    hostname: str = Field(..., max_length=255)
    fqdn: str | None = None
    default_user: str = Field(default="cloud", max_length=50)
    password: str | None = Field(None, max_length=128)
    ssh_authorized_keys: list[str] = []
    disable_password_login: bool = True

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_vm_password(v)


class CloudInitNetworkV2(BaseModel):
    """Optional manual network override (seldom used - pools handle this)."""
    ipv4_address: str | None = None  # e.g. 10.0.0.5/24
    ipv4_gateway: str | None = None
    dns_servers: list[str] = []
    ipv6_address: str | None = None
    ipv6_gateway: str | None = None
    interface_name: str = "eth0"
    vlan_id: int | None = None


class CloudInitUserData(BaseModel):
    packages: list[str] = []
    write_files: list[dict] = []
    runcmd: list[str | list[str]] = []
    bootcmd: list[str | list[str]] = []
    timezone: str | None = None
    locale: str | None = None
    keyboard_layout: str | None = None  # e.g. "de", "us", "ch", "fr"


# --- VM Schemas ---


class VMCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    project_id: uuid.UUID

    # Hardware
    vcpus: int = Field(default=1, ge=1, le=128)
    ram_mb: int = Field(default=1024, ge=256, le=524288)
    disk_size_gb: int = Field(default=20, ge=5, le=10000)
    disk_format: DiskFormat = DiskFormat.QCOW2

    # Template
    template_id: uuid.UUID
    os_profile: Literal["linux", "windows", "other"] = "linux"
    virtio_iso_id: uuid.UUID | None = None

    # Network - IP pool is required (NAT or External)
    ip_pool_id: uuid.UUID
    # Optional: pin a specific reserved public IP (EXTERNAL); otherwise next free is used
    ip_allocation_id: uuid.UUID | None = None
    secondary_pool_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)

    # Storage
    storage_pool_id: uuid.UUID | None = None

    # Cloud-Init
    cloud_init_identity: CloudInitIdentity
    cloud_init_network: CloudInitNetworkV2 | None = None
    cloud_init_userdata: CloudInitUserData | None = None
    custom_userdata_yaml: str | None = Field(default=None, max_length=65535)


class VMUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    vcpus: int | None = Field(None, ge=1, le=128)
    ram_mb: int | None = Field(None, ge=256, le=524288)
    disk_size_gb: int | None = Field(None, ge=5, le=10000)


class VMPasswordResetRequest(BaseModel):
    default_user: str | None = Field(default=None, min_length=1, max_length=50)
    new_password: str = Field(..., min_length=8, max_length=128)
    disable_password_login: bool = False

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return _validate_vm_password(v)


class VMResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    assigned_user_id: uuid.UUID | None = None
    name: str
    hostname: str
    description: str | None
    status: VMStatus
    vcpus: int
    ram_mb: int
    disk_size_gb: int
    disk_format: DiskFormat
    mac_address: str | None
    bridge: str
    vnc_port: int | None
    vnc_websocket_port: int | None
    template_id: uuid.UUID | None
    storage_pool_id: uuid.UUID | None
    cloud_init_network: dict | None
    secondary_interfaces: list | None = None
    os_profile: Literal["linux", "windows", "other"] = "linux"
    install_source: Literal["cloud", "iso"] = "cloud"
    # From linked cloud image (template_id → cloud_images)
    image_name: str | None = None
    os_type: str | None = None
    os_version: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def _extract_install_meta(cls, data: Any) -> Any:
        """Derive os_profile / install_source from cloud_init_metadata install options."""
        if isinstance(data, dict):
            meta = data.get("cloud_init_metadata") or {}
            obj = data
        else:
            meta = getattr(data, "cloud_init_metadata", None) or {}
            obj = data

        opts = meta.get("_install_options") if isinstance(meta, dict) else None
        profile = "linux"
        source = "cloud"
        if isinstance(opts, dict):
            raw_profile = str(opts.get("os_profile") or "linux").lower()
            if raw_profile in ("linux", "windows", "other"):
                profile = raw_profile
            # ISO installs always stash _install_options
            source = "iso"

        if isinstance(obj, dict):
            obj = {**obj, "os_profile": profile, "install_source": source}
            return obj

        # Attach ephemeral attrs for from_attributes pickup
        try:
            object.__setattr__(obj, "os_profile", profile)
            object.__setattr__(obj, "install_source", source)
        except Exception:
            pass
        return obj


class VMListResponse(BaseModel):
    items: list[VMResponse]
    total: int
    page: int
    page_size: int


class VMActionRequest(BaseModel):
    action: str = Field(..., pattern=r"^(start|stop|reboot|force_stop)$")


class VMCloneRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    project_id: uuid.UUID
    cloud_init_identity: CloudInitIdentity
    ip_pool_id: uuid.UUID | None = None  # Optional: if None, uses same pool as source VM


class AddSecondaryNICRequest(BaseModel):
    pool_id: uuid.UUID  # Must be an INTERNAL pool


class ConsoleTokenResponse(BaseModel):
    token: str
    websocket_url: str
    expires_in: int


# --- Disk Schemas ---


class DiskCreate(BaseModel):
    name: str = Field(default="data", min_length=1, max_length=255)
    size_gb: int = Field(default=10, ge=1, le=10000)
    format: DiskFormat = DiskFormat.QCOW2
    storage_pool_id: uuid.UUID | None = None


class DiskResize(BaseModel):
    size_gb: int = Field(ge=1, le=10000)


class DiskResponse(BaseModel):
    id: uuid.UUID
    vm_id: uuid.UUID
    name: str
    disk_path: str
    size_gb: int
    format: DiskFormat
    bus: str
    target_dev: str
    status: DiskStatus
    storage_pool_id: uuid.UUID | None
    is_readonly: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
