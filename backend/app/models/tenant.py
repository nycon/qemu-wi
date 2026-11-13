"""
Tenant / Organisation model for multi-tenancy support.
"""

import uuid

from sqlalchemy import Boolean, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class Tenant(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Quota limits
    max_vms: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_vcpus: Mapped[int] = mapped_column(Integer, default=32, nullable=False)
    max_ram_mb: Mapped[int] = mapped_column(Integer, default=65536, nullable=False)  # 64 GB
    max_storage_gb: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    max_ips: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    # Tenant-wide inbox for VM/backup/quota notifications (logs etc.)
    notification_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="tenant", cascade="all, delete-orphan"
    )
    users: Mapped[list["TenantUser"]] = relationship(
        "TenantUser", back_populates="tenant", cascade="all, delete-orphan"
    )


class Project(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "projects"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="projects")
    virtual_machines: Mapped[list["VirtualMachine"]] = relationship(
        "VirtualMachine", back_populates="project", cascade="all, delete-orphan"
    )
    user_assignments: Mapped[list["ProjectUser"]] = relationship(
        "ProjectUser", back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_project_tenant_name"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )


class ProjectUser(Base, UUIDMixin, TimestampMixin):
    """Maps users to projects for scoped visibility within a tenant."""

    __tablename__ = "project_users"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="user_assignments")
    user: Mapped["User"] = relationship("User", back_populates="project_assignments")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_user"),
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
