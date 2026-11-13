"""
Load Balancer model – TCP/HTTP load balancing across multiple VMs.
Uses Nginx stream/http on the host for the actual proxying.
"""

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class LBProtocol(str, enum.Enum):
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"


class LBAlgorithm(str, enum.Enum):
    ROUND_ROBIN = "round_robin"
    LEAST_CONN = "least_conn"
    IP_HASH = "ip_hash"


class LoadBalancer(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "load_balancers"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    protocol: Mapped[LBProtocol] = mapped_column(
        Enum(LBProtocol, values_callable=lambda x: [e.value for e in x]),
        default=LBProtocol.HTTP, nullable=False,
    )
    algorithm: Mapped[LBAlgorithm] = mapped_column(
        Enum(LBAlgorithm, values_callable=lambda x: [e.value for e in x]),
        default=LBAlgorithm.ROUND_ROBIN, nullable=False,
    )

    listen_port: Mapped[int] = mapped_column(Integer, nullable=False)
    # Single public VIP for client access; backends stay on NAT/internal IPs
    public_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    health_check_path: Mapped[str | None] = mapped_column(String(255), nullable=True, default="/")
    health_check_interval: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )


class LBTarget(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "lb_targets"

    load_balancer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("load_balancers.id", ondelete="CASCADE"), nullable=False
    )
    vm_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_machines.id", ondelete="CASCADE"), nullable=False
    )
    # Which VM address class to use as nginx upstream: nat (RFC1918) or public
    ip_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="nat", server_default="nat"
    )
    # Optional explicit public IP from an external pool (when VM has only NAT)
    preferred_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    target_port: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
