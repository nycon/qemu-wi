"""
IP Address Management (IPAM) and Network models.
"""

import enum
import uuid

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class IPVersion(str, enum.Enum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"


class PoolType(str, enum.Enum):
    NAT = "nat"           # Internal network with SNAT (VMs share host IP)
    EXTERNAL = "external"  # Public/routed IPs assigned directly to VMs
    INTERNAL = "internal"  # Isolated VM-to-VM network (no internet, DHCP)


class IPPool(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ip_pools"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pool_type: Mapped[PoolType] = mapped_column(
        Enum(PoolType, values_callable=lambda x: [e.value for e in x]),
        default=PoolType.NAT, nullable=False,
        server_default="nat",  # Hardcoded .value – Python version independent
    )
    ip_version: Mapped[IPVersion] = mapped_column(
        Enum(IPVersion, values_callable=lambda x: [e.value for e in x]),
        default=IPVersion.IPV4, nullable=False,
    )

    # Network definition
    network: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. 10.10.1.0/24
    gateway: Mapped[str] = mapped_column(String(50), nullable=False)
    dns_primary: Mapped[str] = mapped_column(String(50), default="8.8.8.8", nullable=False)
    dns_secondary: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bridge: Mapped[str] = mapped_column(String(50), default="br0", nullable=False)
    # DHCP for NAT + INTERNAL libvirt networks (ignored for EXTERNAL)
    internal_dhcp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default="true",
    )
    dhcp_start: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dhcp_end: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Libvirt network name (for NAT / INTERNAL pools)
    libvirt_network_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # None = shared/global (visible to all tenants). Set = exclusive to that tenant.
    # External/public pools should remain global (tenant_id NULL).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Relationships
    allocations: Mapped[list["IPAllocation"]] = relationship(
        "IPAllocation", back_populates="pool"
    )
    port_forwards: Mapped[list["PortForward"]] = relationship(
        "PortForward", back_populates="pool"
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )


class AllocationStatus(str, enum.Enum):
    AVAILABLE = "available"
    ALLOCATED = "allocated"
    RESERVED = "reserved"


class IPAllocation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ip_allocations"

    pool_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ip_address: Mapped[str] = mapped_column(INET, nullable=False)
    status: Mapped[AllocationStatus] = mapped_column(
        Enum(AllocationStatus, values_callable=lambda x: [e.value for e in x]),
        default=AllocationStatus.AVAILABLE, nullable=False,
    )

    # Optional assignment
    vm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    pool: Mapped["IPPool"] = relationship("IPPool", back_populates="allocations")
    virtual_machine: Mapped["VirtualMachine"] = relationship(
        "VirtualMachine", back_populates="ip_allocations"
    )

    __table_args__ = (
        UniqueConstraint("pool_id", "ip_address", name="uq_pool_ip"),
        ForeignKeyConstraint(["pool_id"], ["ip_pools.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )


class PortForward(Base, UUIDMixin, TimestampMixin):
    """Port forwarding rule: maps host port -> VM private IP:port (DNAT)."""
    __tablename__ = "port_forwards"

    pool_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    protocol: Mapped[str] = mapped_column(
        String(10), default="tcp", nullable=False
    )  # tcp or udp
    external_port: Mapped[int] = mapped_column(Integer, nullable=False)
    internal_ip: Mapped[str] = mapped_column(String(50), nullable=False)
    internal_port: Mapped[int] = mapped_column(Integer, nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    pool: Mapped["IPPool"] = relationship("IPPool", back_populates="port_forwards")
    virtual_machine: Mapped["VirtualMachine"] = relationship(
        "VirtualMachine", back_populates="port_forwards"
    )

    __table_args__ = (
        UniqueConstraint("protocol", "external_port", name="uq_ext_port_proto"),
        ForeignKeyConstraint(["pool_id"], ["ip_pools.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="CASCADE"),
    )
