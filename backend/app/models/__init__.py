"""
SQLAlchemy models – import all models here so Alembic can discover them.
"""

from app.models.api_key import APIKey
from app.models.audit import AuditLog
from app.models.backup import BackupSchedule, BackupStatus, BackupType, ScheduleFrequency, VMBackup
from app.models.base import TimestampMixin, UUIDMixin
from app.models.platform import DatabaseBackup, DatabaseBackupStatus, PlatformSetting
from app.models.network import AllocationStatus, IPAllocation, IPPool, IPVersion, PoolType, PortForward
from app.models.storage import CloudImage, ImageDownloadStatus, ImageFormat, StoragePool, StoragePoolType
from app.models.task import Task, TaskStatus, TaskType
from app.models.tenant import Project, ProjectUser, Tenant
from app.models.user import TenantUser, User, UserRole
from app.models.notification import NotificationPreference
from app.models.reverse_proxy import ReverseProxyRule, SSLMode
from app.models.vm import DiskFormat, DiskStatus, VMDisk, VMStatus, VirtualMachine
from app.models.cluster import ClusterNode, NodeStatus
from app.models.load_balancer import LBAlgorithm, LBProtocol, LBTarget, LoadBalancer
from app.models.vm_template import VMTemplate

__all__ = [
    "APIKey",
    "AuditLog",
    "BackupSchedule",
    "BackupStatus",
    "BackupType",
    "ScheduleFrequency",
    "VMBackup",
    "DatabaseBackup",
    "DatabaseBackupStatus",
    "PlatformSetting",
    "TimestampMixin",
    "UUIDMixin",
    "AllocationStatus",
    "IPAllocation",
    "IPPool",
    "IPVersion",
    "PoolType",
    "PortForward",
    "CloudImage",
    "ImageDownloadStatus",
    "ImageFormat",
    "StoragePool",
    "StoragePoolType",
    "Task",
    "TaskStatus",
    "TaskType",
    "Project",
    "ProjectUser",
    "Tenant",
    "TenantUser",
    "User",
    "UserRole",
    "NotificationPreference",
    "ReverseProxyRule",
    "SSLMode",
    "DiskFormat",
    "DiskStatus",
    "VMDisk",
    "VMStatus",
    "VirtualMachine",
    "VMTemplate",
    "LoadBalancer",
    "LBTarget",
    "LBProtocol",
    "LBAlgorithm",
    "ClusterNode",
    "NodeStatus",
]
