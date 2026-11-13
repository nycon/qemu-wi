"""
Audit logging service – creates immutable audit trail entries.
"""

import uuid
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def create_audit_log(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[uuid.UUID] = None,
    actor_id: Optional[uuid.UUID] = None,
    actor_email: Optional[str] = None,
    actor_ip: Optional[str] = None,
    tenant_id: Optional[uuid.UUID] = None,
    description: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    status: str = "success",
) -> AuditLog:
    """Create an audit log entry (append-only)."""
    entry = AuditLog(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_ip=actor_ip,
        tenant_id=tenant_id,
        description=description,
        metadata_json=metadata,
        status=status,
    )
    db.add(entry)
    # Don't commit here – let the endpoint/caller handle the transaction
    return entry
