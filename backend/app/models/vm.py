"""
Virtual Machine model with full lifecycle and hardware profile support.
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class VMStatus(str, enum.Enum):
    CREATING = "creating"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    REBOOTING = "rebooting"
    ERROR = "error"
    DELETING = "deleting"
    DELETED = "deleted"


class DiskFormat(str, enum.Enum):
    QCOW2 = "qcow2"
    RAW = "raw"


class VirtualMachine(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "virtual_machines"

    # Ownership
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Identity
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[VMStatus] = mapped_column(
        Enum(VMStatus, values_callable=lambda x: [e.value for e in x]),
        default=VMStatus.CREATING, nullable=False,
    )

    # Hardware Profile
    vcpus: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    disk_size_gb: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    disk_format: Mapped[DiskFormat] = mapped_column(
        Enum(DiskFormat, values_callable=lambda x: [e.value for e in x]),
        default=DiskFormat.QCOW2, nullable=False,
    )

    # Storage
    storage_pool_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    disk_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Template
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Network
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    bridge: Mapped[str] = mapped_column(String(50), default="br0", nullable=False)

    # Cloud-Init config (stored as JSON)
    cloud_init_userdata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cloud_init_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cloud_init_network: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Secondary network interfaces (JSON array)
    # Each entry: {"mac_address": "...", "network_name": "...", "pool_id": "..."}
    secondary_interfaces: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)

    # Libvirt reference
    libvirt_domain_name: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )

    # VNC
    vnc_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vnc_websocket_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="virtual_machines")
    ip_allocations: Mapped[list["IPAllocation"]] = relationship(
        "IPAllocation", back_populates="virtual_machine"
    )
    port_forwards: Mapped[list["PortForward"]] = relationship(
        "PortForward", back_populates="virtual_machine", cascade="all, delete-orphan"
    )
    disks: Mapped[list["VMDisk"]] = relationship(
        "VMDisk", back_populates="virtual_machine", cascade="all, delete-orphan"
    )
    backups: Mapped[list["VMBackup"]] = relationship(
        "VMBackup", back_populates="virtual_machine", cascade="all, delete-orphan"
    )
    backup_schedules: Mapped[list["BackupSchedule"]] = relationship(
        "BackupSchedule", back_populates="virtual_machine", cascade="all, delete-orphan"
    )

    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
        ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["template_id"], ["cloud_images.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["storage_pool_id"], ["storage_pools.id"], ondelete="SET NULL"),
    )


class DiskStatus(str, enum.Enum):
    CREATING = "creating"
    AVAILABLE = "available"
    ATTACHED = "attached"
    DETACHING = "detaching"
    ERROR = "error"
    DELETING = "deleting"


class VMDisk(Base, UUIDMixin, TimestampMixin):
    """Additional disks attached to a VM."""

    __tablename__ = "vm_disks"

    vm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="data")
    storage_pool_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    disk_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[DiskFormat] = mapped_column(
        Enum(DiskFormat, values_callable=lambda x: [e.value for e in x]),
        default=DiskFormat.QCOW2, nullable=False,
    )
    bus: Mapped[str] = mapped_column(String(20), default="virtio", nullable=False)
    target_dev: Mapped[str] = mapped_column(String(10), default="vdb", nullable=False)
    status: Mapped[DiskStatus] = mapped_column(
        Enum(DiskStatus, values_callable=lambda x: [e.value for e in x]),
        default=DiskStatus.CREATING, nullable=False,
    )
    boot_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_readonly: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_cloudinit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    virtual_machine: Mapped["VirtualMachine"] = relationship(
        "VirtualMachine", back_populates="disks"
    )

    __table_args__ = (
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["storage_pool_id"], ["storage_pools.id"], ondelete="SET NULL"),
    )
