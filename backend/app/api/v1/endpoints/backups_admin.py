"""
Superadmin: database backups + VM backup storage configuration.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_superadmin
from app.models.backup import VMBackup
from app.models.platform import DatabaseBackup, DatabaseBackupStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.models.vm import VirtualMachine
from app.services.audit_service import create_audit_log
from app.services import backup_storage as storage_svc
from app.services import db_backup_service as db_svc
from app.services.vm_backup_ops import backup_is_creating, purge_vm_backup_storage

router = APIRouter(prefix="/system", tags=["Backups"])


# ─── Schemas ────────────────────────────────────────────────────────────────


class DatabaseBackupCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    notes: str | None = Field(None, max_length=2000)


class DatabaseBackupResponse(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    file_path: str | None
    file_size_bytes: int | None
    created_by: uuid.UUID | None
    notes: str | None
    error_message: str | None
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class BackupStorageUpdate(BaseModel):
    backend: str = Field(..., pattern=r"^(internal|ftp|s3)$")
    keep_local_copy: bool = True
    ftp: dict[str, Any] | None = None
    s3: dict[str, Any] | None = None


# ─── DB Backups ─────────────────────────────────────────────────────────────


@router.get("/db-backups", response_model=list[DatabaseBackupResponse])
async def list_db_backups(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(DatabaseBackup)
        .where(DatabaseBackup.status != DatabaseBackupStatus.DELETED)
        .order_by(DatabaseBackup.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/db-backups", response_model=DatabaseBackupResponse, status_code=status.HTTP_201_CREATED)
async def create_db_backup(
    body: DatabaseBackupCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    backup = DatabaseBackup(
        name=body.name,
        notes=body.notes,
        status=DatabaseBackupStatus.CREATING,
        created_by=current_user.id,
    )
    db.add(backup)
    await db.flush()
    await db.refresh(backup)

    try:
        path, size = db_svc.create_database_dump(str(backup.id), body.name)
        backup.file_path = path
        backup.file_size_bytes = size
        backup.status = DatabaseBackupStatus.READY
    except Exception as e:
        backup.status = DatabaseBackupStatus.FAILED
        backup.error_message = str(e)[:2000]
        db.add(backup)
        await db.flush()
        raise HTTPException(status_code=500, detail=f"Database dump failed: {e}")

    db.add(backup)
    await db.flush()
    await db.refresh(backup)

    await create_audit_log(
        db,
        action="db_backup_created",
        resource_type="database_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"DB backup created: {backup.name}",
        metadata={"size": size, "path": path},
    )
    return backup


@router.get("/db-backups/{backup_id}/download")
async def download_db_backup(
    backup_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    backup = (
        await db.execute(select(DatabaseBackup).where(DatabaseBackup.id == backup_id))
    ).scalar_one_or_none()
    if not backup or backup.status != DatabaseBackupStatus.READY or not backup.file_path:
        raise HTTPException(status_code=404, detail="Backup not found or not ready")
    if not os.path.isfile(backup.file_path):
        raise HTTPException(status_code=404, detail="Backup file missing on disk")

    filename = Path(backup.file_path).name
    await create_audit_log(
        db,
        action="db_backup_exported",
        resource_type="database_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"DB backup exported: {backup.name}",
    )
    return FileResponse(
        backup.file_path,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.post("/db-backups/import", response_model=dict)
async def import_db_backup(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    confirm: bool = Query(False, description="Must be true to apply restore"),
    validate_only: bool = Query(False, description="Only validate dump TOC"),
):
    if not confirm and not validate_only:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to restore, or validate_only=true to inspect the dump",
        )

    db_svc.ensure_backup_dir()
    suffix = Path(file.filename or "import.dump").suffix or ".dump"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(db_svc.DB_BACKUP_DIR))
    tmp_path = tmp.name
    tmp.close()

    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

        toc = db_svc.list_dump_toc(tmp_path)
        if validate_only:
            os.unlink(tmp_path)
            return {"ok": True, "validated": True, "toc_lines": len(toc.splitlines())}

        # Persist as a tracked backup then restore
        backup = DatabaseBackup(
            name=f"import-{Path(file.filename or 'dump').stem}"[:255],
            notes="Imported dump",
            status=DatabaseBackupStatus.RESTORING,
            created_by=current_user.id,
            file_path=tmp_path,
            file_size_bytes=os.path.getsize(tmp_path),
        )
        db.add(backup)
        await db.flush()

        try:
            db_svc.restore_database_dump(tmp_path)
            backup.status = DatabaseBackupStatus.READY
            backup.error_message = None
        except Exception as e:
            backup.status = DatabaseBackupStatus.FAILED
            backup.error_message = str(e)[:2000]
            db.add(backup)
            await db.flush()
            raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

        db.add(backup)
        await create_audit_log(
            db,
            action="db_backup_imported",
            resource_type="database_backup",
            resource_id=backup.id,
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"DB backup imported/restored: {backup.name}",
        )
        return {
            "ok": True,
            "restored": True,
            "backup_id": str(backup.id),
            "toc_lines": len(toc.splitlines()),
        }
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/db-backups/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_db_backup(
    backup_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    backup = (
        await db.execute(select(DatabaseBackup).where(DatabaseBackup.id == backup_id))
    ).scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    db_svc.delete_dump_file(backup.file_path)
    backup.status = DatabaseBackupStatus.DELETED
    backup.file_path = None
    db.add(backup)
    await create_audit_log(
        db,
        action="db_backup_deleted",
        resource_type="database_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"DB backup deleted: {backup.name}",
    )
    return None


# ─── Global VM Backup listing ───────────────────────────────────────────────


class VMBackupAdminItem(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    vm_id: uuid.UUID
    vm_name: str | None
    tenant_id: uuid.UUID
    tenant_name: str | None = None
    storage_backend: str
    file_path: str | None
    remote_path: str | None
    has_local: bool
    has_remote: bool
    locations: list[str]
    file_size_bytes: int | None
    error_message: str | None
    schedule_id: uuid.UUID | None = None
    created_at: Any
    updated_at: Any


@router.get("/vm-backups", response_model=list[VMBackupAdminItem])
async def list_all_vm_backups(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    backend: Annotated[str | None, Query(description="Filter: internal|ftp|s3|all")] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
):
    """List all VM backups across tenants, with local/FTP/S3 location flags."""
    query = select(VMBackup).order_by(VMBackup.created_at.desc())
    if backend and backend != "all":
        be = backend.lower()
        if be == "local":
            be = "internal"
        if be not in ("internal", "ftp", "s3"):
            raise HTTPException(status_code=400, detail="backend must be internal|local|ftp|s3|all")
        query = query.where(VMBackup.storage_backend == be)
    if status_filter:
        query = query.where(VMBackup.status == status_filter)

    rows = list((await db.execute(query)).scalars().all())
    if not rows:
        return []

    vm_ids = {r.vm_id for r in rows}
    tenant_ids = {r.tenant_id for r in rows}
    vms = {
        v.id: v
        for v in (
            await db.execute(select(VirtualMachine).where(VirtualMachine.id.in_(vm_ids)))
        ).scalars().all()
    }
    tenants = {
        t.id: t
        for t in (
            await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
        ).scalars().all()
    }

    items: list[VMBackupAdminItem] = []
    for b in rows:
        backend_name = (b.storage_backend or "internal").lower()
        has_local = bool(b.file_path)
        has_remote = bool(b.remote_path) and backend_name in ("ftp", "s3")
        locations: list[str] = []
        if has_local or backend_name == "internal":
            if has_local:
                locations.append("local")
            elif backend_name == "internal":
                locations.append("local")
        if has_remote:
            locations.append(backend_name)
        if not locations:
            locations.append(backend_name)

        vm = vms.get(b.vm_id)
        tenant = tenants.get(b.tenant_id)
        items.append(
            VMBackupAdminItem(
                id=b.id,
                name=b.name,
                status=b.status.value if hasattr(b.status, "value") else str(b.status),
                vm_id=b.vm_id,
                vm_name=b.vm_name or (vm.name if vm else None),
                tenant_id=b.tenant_id,
                tenant_name=tenant.name if tenant else None,
                storage_backend=backend_name,
                file_path=b.file_path,
                remote_path=b.remote_path,
                has_local=has_local,
                has_remote=has_remote,
                locations=locations,
                file_size_bytes=b.file_size_bytes,
                error_message=b.error_message,
                schedule_id=b.schedule_id,
                created_at=b.created_at,
                updated_at=b.updated_at,
            )
        )
    return items


@router.delete("/vm-backups/{backup_id}", status_code=status.HTTP_200_OK)
async def delete_vm_backup_admin(
    backup_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Superadmin: delete a VM backup (same DB row + storage as the VM UI delete)."""
    backup = (
        await db.execute(select(VMBackup).where(VMBackup.id == backup_id))
    ).scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if backup_is_creating(backup):
        raise HTTPException(status_code=409, detail="Backup is currently being created")

    await purge_vm_backup_storage(db, backup)

    name = backup.name
    tenant_id = backup.tenant_id
    await create_audit_log(
        db,
        action="backup_deleted",
        resource_type="vm_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM backup '{name}' deleted (admin inventory)",
    )
    # Hard-delete the vm_backups row so it disappears from the VM detail UI as well.
    await db.delete(backup)
    return {"status": "success", "message": f"Backup '{name}' deleted"}


@router.get("/vm-backups/summary")
async def vm_backups_summary(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Counts of VM backups by storage backend / location."""
    rows = list((await db.execute(select(VMBackup))).scalars().all())
    summary = {
        "total": len(rows),
        "by_backend": {"internal": 0, "ftp": 0, "s3": 0, "other": 0},
        "with_local": 0,
        "with_remote": 0,
        "ready": 0,
        "failed": 0,
        "creating": 0,
    }
    for b in rows:
        be = (b.storage_backend or "internal").lower()
        if be in summary["by_backend"]:
            summary["by_backend"][be] += 1
        else:
            summary["by_backend"]["other"] += 1
        if b.file_path:
            summary["with_local"] += 1
        if b.remote_path and be in ("ftp", "s3"):
            summary["with_remote"] += 1
        st = b.status.value if hasattr(b.status, "value") else str(b.status)
        if st in summary:
            summary[st] += 1
    return summary


# ─── VM Backup Storage Config ───────────────────────────────────────────────


@router.get("/backup-storage")
async def get_backup_storage(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    cfg = await storage_svc.get_backup_storage_config(db)
    return storage_svc.mask_config(cfg)


@router.put("/backup-storage")
async def update_backup_storage(
    body: BackupStorageUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = body.model_dump()
    saved = await storage_svc.save_backup_storage_config(db, payload)
    await create_audit_log(
        db,
        action="backup_storage_updated",
        resource_type="platform_setting",
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"VM backup storage set to {saved.get('backend')}",
        metadata={"backend": saved.get("backend"), "keep_local_copy": saved.get("keep_local_copy")},
    )
    return storage_svc.mask_config(saved)


@router.post("/backup-storage/test")
async def test_backup_storage(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    body: BackupStorageUpdate | None = None,
):
    if body:
        # Test provided config without saving (merge secrets from stored)
        stored = await storage_svc.get_backup_storage_config(db)
        cfg = storage_svc._merge_defaults(body.model_dump())
        ftp_pw = (body.ftp or {}).get("password") if body.ftp else None
        if not ftp_pw or ftp_pw == "********":
            cfg["ftp"]["password"] = stored["ftp"].get("password", "")
        s3_secret = (body.s3 or {}).get("secret_key") if body.s3 else None
        if not s3_secret or s3_secret == "********":
            cfg["s3"]["secret_key"] = stored["s3"].get("secret_key", "")
    else:
        cfg = await storage_svc.get_backup_storage_config(db)

    try:
        return storage_svc.test_backup_storage(cfg)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
