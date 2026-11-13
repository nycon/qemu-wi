"""
Storage pool and Cloud Image management endpoints.
"""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_superadmin
from app.models.storage import CloudImage, StoragePool
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.storage import (
    CloudImageCreate,
    CloudImageResponse,
    StoragePoolCreate,
    StoragePoolResponse,
    StoragePoolUpdate,
)
from app.services.audit_service import create_audit_log
from app.services.image_validation import (
    inspect_cloud_image_async,
    reject_arm_filename,
)

router = APIRouter(tags=["Storage"])

# --- Storage Pools ---

pool_router = APIRouter(prefix="/storage-pools", tags=["Storage Pools"])


def _get_dir_size_gb(path: str) -> int:
    """Calculate actual disk usage of a directory in GB."""
    import os
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total // (1024 ** 3)


async def _validate_storage_pool_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
) -> None:
    """Ensure tenant_id references an existing tenant when set."""
    if tenant_id is None:
        return
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=400, detail="Tenant not found for storage pool assignment")


@pool_router.get("", response_model=list[StoragePoolResponse])
async def list_storage_pools(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[Optional[uuid.UUID], Query()] = None,
):
    """
    List storage pools.

    - Superadmin: all pools (optional filter via tenant_id)
    - Other users: shared pools (tenant_id NULL) + pools assigned to their tenants
    - Optional tenant_id: further restrict to shared + that tenant (for VM create UI)
    """
    import shutil

    query = select(StoragePool).order_by(StoragePool.name)

    if current_user.is_superadmin:
        if tenant_id is not None:
            query = query.where(
                or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id == tenant_id)
            )
    else:
        membership_ids = [
            m.tenant_id for m in (current_user.tenant_memberships or []) if m.is_active
        ]
        if tenant_id is not None:
            if tenant_id not in membership_ids and not current_user.is_superadmin:
                raise HTTPException(status_code=403, detail="No access to this tenant")
            query = query.where(
                or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id == tenant_id)
            )
        elif membership_ids:
            query = query.where(
                or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id.in_(membership_ids))
            )
        else:
            query = query.where(StoragePool.tenant_id.is_(None))

    result = await db.execute(query)
    pools = result.scalars().all()

    enriched = []
    for pool in pools:
        try:
            fs = shutil.disk_usage(pool.path)
            pool.total_size_gb = fs.total // (1024 ** 3)
            pool.used_size_gb = _get_dir_size_gb(pool.path)
            pool.free_size_gb = fs.free // (1024 ** 3)
        except (FileNotFoundError, OSError):
            pool.free_size_gb = 0
        enriched.append(pool)

    return enriched


@pool_router.post("", response_model=StoragePoolResponse, status_code=status.HTTP_201_CREATED)
async def create_storage_pool(
    body: StoragePoolCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new storage pool (optionally assign to one tenant)."""
    existing = await db.execute(
        select(StoragePool).where(StoragePool.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Storage pool name already taken")

    await _validate_storage_pool_tenant(db, body.tenant_id)

    pool = StoragePool(**body.model_dump())
    db.add(pool)
    await db.flush()
    await db.refresh(pool)

    scope = f"tenant={body.tenant_id}" if body.tenant_id else "shared/global"
    await create_audit_log(
        db,
        action="storage_pool_created",
        resource_type="storage_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=body.tenant_id,
        description=f"Storage pool created: {pool.name} ({scope})",
    )

    return pool


@pool_router.patch("/{pool_id}", response_model=StoragePoolResponse)
async def update_storage_pool(
    pool_id: uuid.UUID,
    body: StoragePoolUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a storage pool (including tenant assignment)."""
    result = await db.execute(select(StoragePool).where(StoragePool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Storage pool not found")

    update_data = body.model_dump(exclude_unset=True)
    if "tenant_id" in update_data:
        await _validate_storage_pool_tenant(db, update_data["tenant_id"])

    for field, value in update_data.items():
        setattr(pool, field, value)

    db.add(pool)
    await db.flush()
    await db.refresh(pool)

    await create_audit_log(
        db,
        action="storage_pool_updated",
        resource_type="storage_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=pool.tenant_id,
        description=f"Storage pool updated: {pool.name}",
        metadata={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in update_data.items()},
    )

    return pool


@pool_router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_storage_pool(
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a storage pool (only if not in use by any cloud images or VMs)."""
    result = await db.execute(select(StoragePool).where(StoragePool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Storage pool not found")

    # Check if pool is in use by cloud images
    from sqlalchemy import func
    img_count = (await db.execute(
        select(func.count(CloudImage.id)).where(
            CloudImage.storage_pool_id == pool_id,
            CloudImage.is_active.is_(True),
        )
    )).scalar() or 0
    if img_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Pool is used by {img_count} Cloud Image(s)",
        )

    # Check if pool is in use by VMs
    from app.models.vm import VirtualMachine
    vm_count = (await db.execute(
        select(func.count(VirtualMachine.id)).where(
            VirtualMachine.storage_pool_id == pool_id,
            VirtualMachine.is_deleted.is_(False),
        )
    )).scalar() or 0
    if vm_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Pool is used by {vm_count} VM(s). Delete or migrate VMs first.",
        )

    await db.delete(pool)

    await create_audit_log(
        db,
        action="storage_pool_deleted",
        resource_type="storage_pool",
        resource_id=pool_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Storage pool deleted: {pool.name}",
    )


# --- Cloud Images ---

image_router = APIRouter(prefix="/cloud-images", tags=["Cloud Images"])


@image_router.get("", response_model=list[CloudImageResponse])
async def list_cloud_images(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """List cloud images shared across all tenants.

    Public active images are available to every authenticated user.
    Superadmins additionally see non-public active images.
    """
    query = select(CloudImage).where(CloudImage.is_active.is_(True))
    if not current_user.is_superadmin:
        query = query.where(CloudImage.is_public.is_(True))
    result = await db.execute(query.order_by(CloudImage.os_type, CloudImage.os_version))
    return result.scalars().all()


@image_router.post("", response_model=CloudImageResponse, status_code=status.HTTP_201_CREATED)
async def create_cloud_image(
    body: CloudImageCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register a new cloud image. If download_url is provided, starts async download."""
    from app.models.storage import ImageDownloadStatus

    image = CloudImage(
        **body.model_dump(),
        file_path="",
        file_size_bytes=0,
        download_status=ImageDownloadStatus.PENDING if body.download_url else ImageDownloadStatus.READY,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image)

    await create_audit_log(
        db,
        action="cloud_image_created",
        resource_type="cloud_image",
        resource_id=image.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Cloud-Image registriert: {image.name} ({image.os_type} {image.os_version})",
    )

    # Commit BEFORE dispatching Celery task so the worker can read the data
    await db.commit()

    # Trigger async download if URL provided
    if body.download_url:
        from app.workers.storage_tasks import download_cloud_image
        download_cloud_image.delay(str(image.id))

    return image


@image_router.post("/upload", response_model=CloudImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_cloud_image(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    name: str = Form(default=""),
    os_type: str = Form(default="linux"),
    os_version: str = Form(default=""),
    image_format: str = Form(default="qcow2"),
    min_disk_gb: int = Form(default=10),
    min_ram_mb: int = Form(default=512),
    default_userdata_yaml: str | None = Form(default=None),
):
    """Upload a cloud image file directly."""
    import os
    from pathlib import Path
    from app.models.storage import ImageDownloadStatus, ImageFormat

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file specified")

    # Determine image name
    img_name = name or file.filename.rsplit(".", 1)[0]

    # Save file to images directory
    images_dir = Path("/var/lib/qemu-interface/images")
    images_dir.mkdir(parents=True, exist_ok=True)

    # Use a safe filename
    safe_filename = f"{uuid.uuid4().hex[:8]}_{file.filename.replace(' ', '_')}"
    file_path = images_dir / safe_filename

    total_size = 0
    with open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break
            f.write(chunk)
            total_size += len(chunk)

    # Determine format
    fmt = ImageFormat.QCOW2
    if image_format == "raw":
        fmt = ImageFormat.RAW
    elif image_format == "iso":
        fmt = ImageFormat.ISO
    elif file.filename and file.filename.endswith(".img"):
        # Ubuntu cloud images use .img but are qcow2 (not raw block devices).
        fmt = ImageFormat.QCOW2
    elif file.filename and file.filename.endswith(".iso"):
        fmt = ImageFormat.ISO

    detected_architecture = "x86_64"
    if fmt != ImageFormat.ISO:
        try:
            reject_arm_filename(file.filename or "")
            detected_architecture = await inspect_cloud_image_async(str(file_path))
        except ValueError as e:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(e)) from e

    # Create DB record
    image = CloudImage(
        name=img_name,
        os_type=os_type,
        os_version=os_version or "unknown",
        architecture=detected_architecture,
        format=fmt,
        min_disk_gb=min_disk_gb,
        min_ram_mb=min_ram_mb,
        min_vcpus=1,
        file_path=str(file_path),
        file_size_bytes=total_size,
        default_userdata_yaml=default_userdata_yaml,
        download_status=ImageDownloadStatus.READY,
    )
    db.add(image)
    await db.flush()
    await db.refresh(image)

    await create_audit_log(
        db,
        action="cloud_image_uploaded",
        resource_type="cloud_image",
        resource_id=image.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Cloud-Image hochgeladen: {img_name} ({os.path.getsize(file_path) / 1048576:.1f} MB)",
    )

    await db.commit()
    return image


@image_router.get("/{image_id}", response_model=CloudImageResponse)
async def get_cloud_image(
    image_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get a specific cloud image (shared across tenants when public)."""
    result = await db.execute(select(CloudImage).where(CloudImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image or not image.is_active:
        raise HTTPException(status_code=404, detail="Cloud image not found")
    if not image.is_public and not current_user.is_superadmin:
        raise HTTPException(status_code=404, detail="Cloud image not found")
    return image


@image_router.post("/{image_id}/retry-download", response_model=CloudImageResponse)
async def retry_cloud_image_download(
    image_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Retry downloading a cloud image that is stuck or failed."""
    from app.models.storage import ImageDownloadStatus

    result = await db.execute(select(CloudImage).where(CloudImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Cloud image not found")

    if not image.download_url:
        raise HTTPException(status_code=400, detail="No download URL available")

    if image.download_status == ImageDownloadStatus.DOWNLOADING:
        raise HTTPException(status_code=409, detail="Download already in progress")

    # Full reset: status, progress, error, and file info (re-download from scratch)
    image.download_status = ImageDownloadStatus.PENDING
    image.download_progress = 0
    image.download_error = None
    image.file_path = ""  # Will be set by download task
    image.file_size_bytes = 0
    image.checksum_sha256 = None
    db.add(image)
    await db.commit()

    # Dispatch download task
    from app.workers.storage_tasks import download_cloud_image
    download_cloud_image.delay(str(image.id))

    await create_audit_log(
        db,
        action="cloud_image_retry_download",
        resource_type="cloud_image",
        resource_id=image.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Cloud-Image Download wiederholt: {image.name}",
    )

    await db.refresh(image)
    return image


@image_router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cloud_image(
    image_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Deactivate a cloud image."""
    result = await db.execute(select(CloudImage).where(CloudImage.id == image_id))
    image = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="Cloud image not found")

    image.is_active = False
    db.add(image)

    await create_audit_log(
        db,
        action="cloud_image_deleted",
        resource_type="cloud_image",
        resource_id=image.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Cloud image deactivated: {image.name}",
    )
