"""
VM Template / Preset model for quick VM creation.
"""

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class VMTemplate(Base, UUIDMixin, TimestampMixin):
    """Reusable VM configuration presets."""

    __tablename__ = "vm_templates"

    # Ownership (null = global/system template)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # Identity
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str] = mapped_column(String(50), default="server", nullable=False)

    # Hardware profile
    vcpus: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    disk_size_gb: Mapped[int] = mapped_column(Integer, default=20, nullable=False)

    # Cloud image (optional – if set, pre-selects image)
    cloud_image_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Cloud-Init defaults (stored as JSON)
    default_user: Mapped[str] = mapped_column(String(50), default="cloud", nullable=False)
    packages: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    runcmd: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Berlin", nullable=False)
    keyboard_layout: Mapped[str] = mapped_column(String(10), default="de", nullable=False)
    locale: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # SSH keys (pre-filled)
    ssh_keys: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=list)

    # Visibility
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
        ForeignKeyConstraint(["cloud_image_id"], ["cloud_images.id"], ondelete="SET NULL"),
    )
