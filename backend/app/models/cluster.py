"""
Cluster Node model – registry of remote QEMU Interface instances.
The local installation acts as the 'master' that aggregates data
from all registered nodes via their public APIs.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class NodeStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class ClusterNode(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "cluster_nodes"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus, values_callable=lambda x: [e.value for e in x]),
        default=NodeStatus.UNKNOWN, nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cached resource info (updated by health check)
    total_vms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    running_vms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_vcpus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_ram_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_vcpus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_ram_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_storage_gb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_storage_gb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Full cached data from last health check
    cached_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
