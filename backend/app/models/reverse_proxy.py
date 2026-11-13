"""
Reverse Proxy rules – domain-based routing to VMs.
"""

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class SSLMode(str, enum.Enum):
    NONE = "none"
    AUTO = "auto"          # Let's Encrypt via certbot
    CUSTOM = "custom"      # User-provided certs (future)


class ReverseProxyRule(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "reverse_proxy_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    target_ip: Mapped[str] = mapped_column(String(50), nullable=False)
    target_port: Mapped[int] = mapped_column(Integer, default=80, nullable=False)

    ssl_mode: Mapped[SSLMode] = mapped_column(
        Enum(SSLMode, values_callable=lambda x: [e.value for e in x]),
        default=SSLMode.NONE, nullable=False,
    )
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Advanced options
    websocket_support: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    custom_nginx_config: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Relationships
    virtual_machine: Mapped["VirtualMachine"] = relationship(
        "VirtualMachine", foreign_keys=[vm_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["vm_id"], ["virtual_machines.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
