"""
Pydantic schemas for IP Address Management (IPAM) and Port Forwarding.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.network import AllocationStatus, IPVersion, PoolType


# --- IP Pool ---


class IPPoolCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    description: str | None = None
    pool_type: PoolType = PoolType.NAT
    ip_version: IPVersion = IPVersion.IPV4
    network: str = Field(..., max_length=50)  # CIDR notation
    gateway: str = Field(..., max_length=50)
    dns_primary: str = Field(default="8.8.8.8", max_length=50)
    dns_secondary: str | None = None
    vlan_id: int | None = None
    bridge: str = Field(default="", max_length=50)  # Only for external pools
    # DHCP for NAT + INTERNAL (field name kept for API compatibility)
    internal_dhcp_enabled: bool = True
    dhcp_start: str | None = Field(None, max_length=50)
    dhcp_end: str | None = Field(None, max_length=50)
    # Optional tenant scope (NAT/INTERNAL only). NULL = shared/global.
    tenant_id: uuid.UUID | None = None


class TenantNetworkCreate(BaseModel):
    """Tenant NAT/Internal create — same options as admin IPAM (CIDR optional = auto)."""

    name: str = Field(..., min_length=2, max_length=64)
    pool_type: PoolType = PoolType.NAT
    description: str | None = None
    # Optional: if omitted, CIDR/gateway are auto-assigned
    network: str | None = Field(None, max_length=50)
    gateway: str | None = Field(None, max_length=50)
    dns_primary: str = Field(default="8.8.8.8", max_length=50)
    dns_secondary: str | None = Field(default="1.1.1.1", max_length=50)
    internal_dhcp_enabled: bool = True
    dhcp_start: str | None = Field(None, max_length=50)
    dhcp_end: str | None = Field(None, max_length=50)

    @field_validator("pool_type")
    @classmethod
    def only_private_types(cls, v: PoolType) -> PoolType:
        if v not in (PoolType.NAT, PoolType.INTERNAL):
            raise ValueError("Tenant networks must be nat or internal")
        return v


class TenantPublicIpAssign(BaseModel):
    """Replace the tenant's assigned public IP set (allocation IDs from external pools)."""

    allocation_ids: list[uuid.UUID] = Field(default_factory=list)


class TenantNetworkUpdate(BaseModel):
    """Editable tenant NAT/Internal fields (CIDR / type are fixed after create)."""

    name: str | None = Field(None, min_length=2, max_length=64)
    description: str | None = None
    dns_primary: str | None = Field(None, max_length=50)
    dns_secondary: str | None = Field(None, max_length=50)
    internal_dhcp_enabled: bool | None = None
    dhcp_start: str | None = Field(None, max_length=50)
    dhcp_end: str | None = Field(None, max_length=50)


class IPPoolUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=255)
    description: str | None = None
    is_active: bool | None = None
    dns_primary: str | None = None
    dns_secondary: str | None = None
    vlan_id: int | None = None
    bridge: str | None = Field(None, max_length=50)
    internal_dhcp_enabled: bool | None = None
    dhcp_start: str | None = Field(None, max_length=50)
    dhcp_end: str | None = Field(None, max_length=50)


class IPPoolResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    pool_type: PoolType
    ip_version: IPVersion
    network: str
    gateway: str
    dns_primary: str
    dns_secondary: str | None
    vlan_id: int | None
    bridge: str
    internal_dhcp_enabled: bool
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    libvirt_network_name: str | None
    tenant_id: uuid.UUID | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IPPoolWithStats(IPPoolResponse):
    total_ips: int = 0
    allocated_ips: int = 0
    available_ips: int = 0


# --- IP Allocation ---


class IPAllocationCreate(BaseModel):
    pool_id: uuid.UUID
    ip_address: str
    vm_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    description: str | None = None


class IPAllocationResponse(BaseModel):
    id: uuid.UUID
    pool_id: uuid.UUID
    ip_address: str
    status: AllocationStatus
    vm_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("ip_address", mode="before")
    @classmethod
    def coerce_ip_to_str(cls, v: object) -> str:
        """Handle PostgreSQL INET type that asyncpg returns as IPv4Interface/IPv6Interface."""
        if hasattr(v, "ip"):
            return str(v.ip)
        return str(v)


# --- Port Forwarding ---


class PortForwardCreate(BaseModel):
    vm_id: uuid.UUID
    protocol: str = Field(default="tcp", pattern=r"^(tcp|udp)$")
    external_port: int = Field(..., ge=1, le=65535)
    internal_port: int = Field(..., ge=1, le=65535)
    description: str | None = None


class PortForwardResponse(BaseModel):
    id: uuid.UUID
    pool_id: uuid.UUID
    vm_id: uuid.UUID
    protocol: str
    external_port: int
    internal_ip: str
    internal_port: int
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
