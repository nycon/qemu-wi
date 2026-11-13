"""
Storage pool and Cloud Image (template) models.
"""

import enum
import uuid

from sqlalchemy import BigInteger, Boolean, Enum, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin


class StoragePoolType(str, enum.Enum):
    DIRECTORY = "directory"
    LVM_THIN = "lvm_thin"
    ZFS = "zfs"


class StoragePool(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "storage_pools"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    pool_type: Mapped[StoragePoolType] = mapped_column(
        Enum(StoragePoolType, values_callable=lambda x: [e.value for e in x]),
        default=StoragePoolType.DIRECTORY, nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    total_size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    used_size_gb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # None = shared/global (all tenants). Set = exclusive to that tenant.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
    )


class ImageFormat(str, enum.Enum):
    QCOW2 = "qcow2"
    RAW = "raw"
    ISO = "iso"
    TAR_XZ = "tar.xz"


class ImageDownloadStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"


class CloudImage(Base, UUIDMixin, TimestampMixin):
    """Cloud images used as VM templates (base images)."""

    __tablename__ = "cloud_images"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    os_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. ubuntu, debian, rocky
    os_version: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. 22.04, 12, 9
    architecture: Mapped[str] = mapped_column(String(20), default="x86_64", nullable=False)

    # Download
    download_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    download_status: Mapped[ImageDownloadStatus] = mapped_column(
        Enum(ImageDownloadStatus, values_callable=lambda x: [e.value for e in x]),
        default=ImageDownloadStatus.PENDING, nullable=False,
    )
    download_progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    download_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # File info
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    format: Mapped[ImageFormat] = mapped_column(
        Enum(ImageFormat, values_callable=lambda x: [e.value for e in x]),
        default=ImageFormat.QCOW2, nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Storage
    storage_pool_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_userdata_yaml: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Minimum requirements
    min_vcpus: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    min_ram_mb: Mapped[int] = mapped_column(Integer, default=512, nullable=False)
    min_disk_gb: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["storage_pool_id"], ["storage_pools.id"], ondelete="SET NULL"
        ),
    )
