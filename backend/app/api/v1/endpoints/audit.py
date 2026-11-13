"""
Audit log endpoints (read-only).
"""

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import PaginationParams, require_superadmin
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["Audit"])


def _build_audit_query(
    action: str | None = None,
    resource_type: str | None = None,
    tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
):
    """Build a filtered audit log query."""
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if tenant_id:
        query = query.where(AuditLog.tenant_id == tenant_id)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    return query


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    action: str | None = None,
    resource_type: str | None = None,
    tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
):
    """List audit logs (superadmin only). Supports filtering."""
    query = _build_audit_query(action, resource_type, tenant_id, actor_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()

    # Fetch page
    query = query.order_by(AuditLog.timestamp.desc())
    query = query.offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    logs = result.scalars().all()

    return AuditLogListResponse(
        items=logs,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/export")
async def export_audit_logs(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    format: str = Query("csv", regex="^(csv|json)$"),
    action: str | None = None,
    resource_type: str | None = None,
    tenant_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    limit: int = Query(10000, ge=1, le=100000),
):
    """Export audit logs as CSV or JSON (superadmin only). Max 100k rows."""
    query = _build_audit_query(action, resource_type, tenant_id, actor_id)
    query = query.order_by(AuditLog.timestamp.desc()).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if format == "json":
        rows = [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "actor_id": str(log.actor_id) if log.actor_id else None,
                "actor_email": log.actor_email,
                "actor_ip": log.actor_ip,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": str(log.resource_id) if log.resource_id else None,
                "tenant_id": str(log.tenant_id) if log.tenant_id else None,
                "description": log.description,
                "status": log.status,
            }
            for log in logs
        ]
        content = json.dumps(rows, indent=2, default=str)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="audit_logs_{timestamp}.json"'},
        )

    # CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp", "Action", "Actor Email", "Actor IP",
        "Resource Type", "Resource ID", "Tenant ID", "Description", "Status",
    ])
    for log in logs:
        writer.writerow([
            log.timestamp.isoformat() if log.timestamp else "",
            log.action,
            log.actor_email or "",
            log.actor_ip or "",
            log.resource_type,
            str(log.resource_id) if log.resource_id else "",
            str(log.tenant_id) if log.tenant_id else "",
            log.description or "",
            log.status,
        ])

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_logs_{timestamp}.csv"'},
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific audit log entry."""
    result = await db.execute(select(AuditLog).where(AuditLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return log
