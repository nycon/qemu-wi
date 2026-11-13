"""
Virtual Machine management endpoints.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import PaginationParams, RequireTenantRole, get_current_user
from app.core.security import create_console_token
from app.models.tenant import Project, ProjectUser
from app.models.task import Task, TaskStatus, TaskType
from app.models.user import User, UserRole
from app.models.network import IPAllocation, AllocationStatus, IPPool, PoolType
from app.models.storage import CloudImage
from app.models.vm import VMStatus, VirtualMachine, VMDisk, DiskStatus
from app.schemas.vm import (
    AddSecondaryNICRequest,
    ConsoleTokenResponse,
    DiskCreate,
    DiskResize,
    DiskResponse,
    VMPasswordResetRequest,
    VMActionRequest,
    VMCloneRequest,
    VMCreate,
    VMListResponse,
    VMResponse,
    VMUpdate,
)
from app.services.audit_service import create_audit_log
from app.services.vm_service import create_vm, clone_vm

router = APIRouter(prefix="/tenants/{tenant_id}/vms", tags=["Virtual Machines"])


async def _cloud_image_map(
    db: AsyncSession, template_ids: list[uuid.UUID | None]
) -> dict[uuid.UUID, CloudImage]:
    ids = [tid for tid in set(template_ids) if tid]
    if not ids:
        return {}
    rows = (
        await db.execute(select(CloudImage).where(CloudImage.id.in_(ids)))
    ).scalars().all()
    return {img.id: img for img in rows}


def _vm_response(vm: VirtualMachine, images: dict[uuid.UUID, CloudImage] | None = None) -> VMResponse:
    """Serialize VM and attach distro fields from the linked cloud image."""
    base = VMResponse.model_validate(vm)
    img = (images or {}).get(vm.template_id) if vm.template_id else None
    if not img:
        return base
    return base.model_copy(
        update={
            "image_name": img.name,
            "os_type": img.os_type,
            "os_version": img.os_version,
        }
    )


async def _vm_response_one(db: AsyncSession, vm: VirtualMachine) -> VMResponse:
    images = await _cloud_image_map(db, [vm.template_id])
    return _vm_response(vm, images)


def _is_tenant_admin(user: User, tenant_id: uuid.UUID) -> bool:
    return any(
        membership.tenant_id == tenant_id
        and membership.is_active
        and membership.role in (UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN)
        for membership in user.tenant_memberships
    )


async def _assigned_project_ids_for_user(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[uuid.UUID]:
    return list(
        (
            await db.execute(
                select(ProjectUser.project_id)
                .join(Project, Project.id == ProjectUser.project_id)
                .where(
                    ProjectUser.user_id == user_id,
                    Project.tenant_id == tenant_id,
                    Project.is_active.is_(True),
                )
            )
        ).scalars().all()
    )


@router.get("", response_model=VMListResponse)
async def list_vms(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    project_id: uuid.UUID | None = None,
    vm_status: VMStatus | None = Query(None, alias="status"),
):
    """List all VMs in a tenant, optionally filtered by project or status."""
    query = select(VirtualMachine).where(
        VirtualMachine.tenant_id == tenant_id,
        VirtualMachine.is_deleted.is_(False),
    )

    # Non-admin users only see VMs they created
    if not current_user.is_superadmin:
        is_admin = _is_tenant_admin(current_user, tenant_id)
        if not is_admin:
            assigned_project_ids = await _assigned_project_ids_for_user(db, tenant_id, current_user.id)
            if assigned_project_ids:
                query = query.where(
                    (VirtualMachine.project_id.in_(assigned_project_ids))
                    | (VirtualMachine.assigned_user_id == current_user.id)
                )
            else:
                query = query.where(
                    (VirtualMachine.created_by == current_user.id)
                    | (VirtualMachine.assigned_user_id == current_user.id)
                )

    if project_id:
        query = query.where(VirtualMachine.project_id == project_id)
    if vm_status:
        query = query.where(VirtualMachine.status == vm_status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()

    # Fetch page
    query = query.order_by(VirtualMachine.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    vms = list(result.scalars().all())
    images = await _cloud_image_map(db, [vm.template_id for vm in vms])

    return VMListResponse(
        items=[_vm_response(vm, images) for vm in vms],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("", response_model=VMResponse, status_code=status.HTTP_201_CREATED)
async def create_virtual_machine(
    tenant_id: uuid.UUID,
    body: VMCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new VM from a cloud image template."""
    try:
        vm, task = await create_vm(db, body, current_user, tenant_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    await create_audit_log(
        db,
        action="vm_created",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM created: {vm.name} ({vm.vcpus} vCPU, {vm.ram_mb} MB RAM)",
        metadata={"task_id": str(task.id)},
    )

    # Commit before dispatching Celery task so the worker can read the data
    await db.commit()

    # Dispatch provisioning task to Celery worker
    from app.workers.vm_tasks import task_vm_create
    task_vm_create.delay(str(vm.id), str(task.id))

    return await _vm_response_one(db, vm)


@router.get("/{vm_id}", response_model=VMResponse)
async def get_vm(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    # Keep DB status in sync with host/libvirt state to avoid stale "running" UI.
    # This prevents follow-up errors in stats/console/firewall panels when a domain
    # is actually shut off on the host.
    if vm.libvirt_domain_name:
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=5) as client:
                resp = await client.get(
                    f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/status",
                    headers=AGENT_HEADERS,
                )
                if resp.status_code == 200:
                    host_state = (resp.json() or {}).get("state")
                    state_map = {
                        "running": VMStatus.RUNNING,
                        "stopped": VMStatus.STOPPED,
                        "paused": VMStatus.STOPPING,
                        "shutting_down": VMStatus.STOPPING,
                        "crashed": VMStatus.ERROR,
                        "unknown": vm.status,
                        "nostate": vm.status,
                    }
                    mapped = state_map.get(host_state, vm.status)
                    if mapped != vm.status:
                        vm.status = mapped
                        db.add(vm)
                        await db.flush()
        except Exception:
            # Non-fatal: return VM as-is if host-agent status is unavailable.
            pass

    return await _vm_response_one(db, vm)


@router.patch("/{vm_id}", response_model=VMResponse)
async def update_vm(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: VMUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update VM metadata (name, description). Hardware changes require stop."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    update_data = body.model_dump(exclude_unset=True)

    # Hardware changes only when stopped
    hw_fields = {"vcpus", "ram_mb", "disk_size_gb"}
    hw_changes = {k: v for k, v in update_data.items() if k in hw_fields}
    if hw_changes and vm.status != VMStatus.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VM must be stopped for hardware changes",
        )

    # Disk can only grow, never shrink
    if "disk_size_gb" in hw_changes and hw_changes["disk_size_gb"] < vm.disk_size_gb:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disk can only be increased (currently {vm.disk_size_gb} GB)",
        )

    if hw_changes:
        from app.services.vm_service import check_tenant_quota_for_resize

        ok, msg = await check_tenant_quota_for_resize(
            db,
            tenant_id,
            current_vcpus=vm.vcpus,
            new_vcpus=int(hw_changes.get("vcpus", vm.vcpus)),
            current_ram_mb=vm.ram_mb,
            new_ram_mb=int(hw_changes.get("ram_mb", vm.ram_mb)),
            current_disk_gb=vm.disk_size_gb,
            new_disk_gb=int(hw_changes.get("disk_size_gb", vm.disk_size_gb)),
        )
        if not ok:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)

    # Apply hardware changes on host agent if VM is stopped
    if hw_changes and vm.libvirt_domain_name:
        resize_payload = {}
        if "vcpus" in hw_changes:
            resize_payload["vcpus"] = hw_changes["vcpus"]
        if "ram_mb" in hw_changes:
            resize_payload["ram_mb"] = hw_changes["ram_mb"]
        if "disk_size_gb" in hw_changes:
            resize_payload["disk_size_gb"] = hw_changes["disk_size_gb"]

        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=120) as client:
                resp = await client.post(
                    f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/resize",
                    headers=AGENT_HEADERS,
                    json=resize_payload,
                )
                resp.raise_for_status()
        except httpx.ConnectError:
            raise HTTPException(status_code=502, detail="Host-Agent unreachable")
        except httpx.HTTPStatusError as e:
            detail = "Resize failed"
            try:
                detail = e.response.json().get("detail", detail)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=detail)

    for field, value in update_data.items():
        setattr(vm, field, value)

    db.add(vm)
    await db.flush()
    await db.refresh(vm)

    await create_audit_log(
        db,
        action="vm_updated",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM updated: {vm.name}" + (f" (Hardware: {', '.join(f'{k}={v}' for k, v in hw_changes.items())})" if hw_changes else ""),
        metadata=update_data,
    )

    return await _vm_response_one(db, vm)


@router.post("/{vm_id}/action", status_code=status.HTTP_202_ACCEPTED)
async def vm_action(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: VMActionRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Perform a lifecycle action on a VM (start, stop, reboot, force_stop)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    # Validate state transitions
    valid_transitions = {
        "start": [VMStatus.STOPPED],
        "stop": [VMStatus.RUNNING],
        "reboot": [VMStatus.RUNNING],
        "force_stop": [VMStatus.RUNNING, VMStatus.STOPPING, VMStatus.ERROR],
    }

    allowed_states = valid_transitions.get(body.action, [])
    if vm.status not in allowed_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action '{body.action}' not possible in status '{vm.status.value}'",
        )

    # Map action to task type
    action_task_map = {
        "start": (TaskType.VM_START, VMStatus.STARTING),
        "stop": (TaskType.VM_STOP, VMStatus.STOPPING),
        "reboot": (TaskType.VM_REBOOT, VMStatus.REBOOTING),
        "force_stop": (TaskType.VM_FORCE_STOP, VMStatus.STOPPING),
    }

    task_type, transition_status = action_task_map[body.action]

    # Update VM status
    vm.status = transition_status
    db.add(vm)

    # Create task
    task = Task(
        task_type=task_type,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
    )
    db.add(task)
    await db.flush()

    await create_audit_log(
        db,
        action=f"vm_{body.action}",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM action: {body.action} on {vm.name}",
        metadata={"task_id": str(task.id)},
    )

    # Commit before dispatching Celery task
    await db.commit()

    # Dispatch lifecycle task to Celery worker
    from app.workers.vm_tasks import (
        task_vm_start, task_vm_stop, task_vm_reboot, task_vm_force_stop,
    )
    celery_task_map = {
        "start": task_vm_start,
        "stop": task_vm_stop,
        "reboot": task_vm_reboot,
        "force_stop": task_vm_force_stop,
    }
    celery_task = celery_task_map[body.action]
    celery_task.delay(str(vm.id), str(task.id))

    return {"task_id": str(task.id), "status": "queued", "action": body.action}


@router.post("/{vm_id}/eject-installer")
async def eject_vm_installer_media(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Eject attached installer ISOs and switch boot order to disk."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if vm.status != VMStatus.STOPPED:
        raise HTTPException(status_code=409, detail="VM must be stopped before ejecting installer media")
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no libvirt domain")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/eject-installer",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = "Failed to eject installer media"
        try:
            detail = e.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)
    except Exception:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")

    await create_audit_log(
        db,
        action="vm_installer_media_ejected",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Installer media ejected for VM: {vm.name}",
    )

    return {"status": "success", "message": "Installer media ejected. VM will boot from disk."}


@router.post("/{vm_id}/attach-virtio")
async def attach_vm_virtio_media(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Attach VirtIO ISO to a stopped VM (auto-detected by host-agent)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if vm.status != VMStatus.STOPPED:
        raise HTTPException(status_code=409, detail="VM must be stopped before attaching VirtIO media")
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no libvirt domain")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/attach-virtio",
                headers=AGENT_HEADERS,
                json={},
            )
            response.raise_for_status()
            agent_resp = response.json()
    except httpx.HTTPStatusError as e:
        detail = "Failed to attach VirtIO media"
        try:
            detail = e.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)
    except Exception:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")

    await create_audit_log(
        db,
        action="vm_virtio_media_attached",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VirtIO media attached for VM: {vm.name}",
        metadata={"agent_response": agent_resp.get("data") if isinstance(agent_resp, dict) else None},
    )

    return {
        "status": "success",
        "message": (agent_resp.get("message") if isinstance(agent_resp, dict) else None) or "VirtIO media attached",
        "data": (agent_resp.get("data") if isinstance(agent_resp, dict) else None),
    }


@router.delete("/{vm_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_vm(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = Query(False, description="Force delete regardless of VM state"),
):
    """Delete a VM (soft delete + cleanup task). Use force=true for stuck VMs."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if not force and vm.status not in (VMStatus.STOPPED, VMStatus.ERROR):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VM must be stopped or in error status to delete. Use force=true for stuck VMs.",
        )

    vm.status = VMStatus.DELETING
    db.add(vm)

    task = Task(
        task_type=TaskType.VM_DELETE,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
    )
    db.add(task)
    await db.flush()

    await create_audit_log(
        db,
        action="vm_force_deleted" if force else "vm_deleted",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM {'(force) ' if force else ''}marked for deletion: {vm.name}",
        metadata={"task_id": str(task.id), "force": force, "prev_status": vm.status.value if hasattr(vm.status, 'value') else str(vm.status)},
    )

    # Commit before dispatching Celery task
    await db.commit()

    # Dispatch delete task to Celery worker
    from app.workers.vm_tasks import task_vm_delete
    task_vm_delete.delay(str(vm.id), str(task.id))

    return {"task_id": str(task.id), "status": "queued", "force": force}


@router.post("/{vm_id}/console", response_model=ConsoleTokenResponse)
async def get_console_token(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a short-lived console access token for VNC/noVNC."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no host domain reference")

    # Source of truth check against host/libvirt state to avoid stale DB status.
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            status_resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/status",
                headers=AGENT_HEADERS,
            )
            status_resp.raise_for_status()
            host_state = (status_resp.json() or {}).get("state")
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not verify VM host state: {e}")

    if host_state != "running":
        if vm.status != VMStatus.STOPPED:
            vm.status = VMStatus.STOPPED
            db.add(vm)
            await db.flush()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VM is not running on host. Start the VM first, then open console.",
        )

    if vm.status != VMStatus.RUNNING:
        vm.status = VMStatus.RUNNING
        db.add(vm)
        await db.flush()

    # Ensure host-agent has a current vm_id -> vnc_port mapping.
    # This prevents noVNC "Disconnected" issues after agent/container restarts.
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/refresh-vnc-mapping",
                headers=AGENT_HEADERS,
                json={"vm_id": str(vm.id)},
            )
            # Backward compatibility: older host-agent versions don't expose this endpoint yet.
            # In that case we continue with legacy mapping behavior instead of failing console access.
            if resp.status_code not in (200, 404):
                resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to prepare console mapping: {e}")

    token = create_console_token(str(vm.id), str(current_user.id))

    from urllib.parse import quote

    websocket_url = (
        f"/websockify?token={quote(token, safe='')}"
        f"&domain={quote(vm.libvirt_domain_name, safe='')}"
    )

    await create_audit_log(
        db,
        action="console_accessed",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Console access to VM: {vm.name}",
    )

    return ConsoleTokenResponse(
        token=token,
        websocket_url=websocket_url,
        expires_in=settings.CONSOLE_TOKEN_EXPIRE_SECONDS,
    )


async def _get_vm_or_404(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: User,
) -> VirtualMachine:
    """Helper to fetch a VM with access control checks."""
    result = await db.execute(
        select(VirtualMachine).where(
            VirtualMachine.id == vm_id,
            VirtualMachine.tenant_id == tenant_id,
            VirtualMachine.is_deleted.is_(False),
        )
    )
    vm = result.scalar_one_or_none()

    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    # Check ownership for non-admins
    if not current_user.is_superadmin:
        is_admin = _is_tenant_admin(current_user, tenant_id)
        if not is_admin:
            assigned_project_ids = await _assigned_project_ids_for_user(db, tenant_id, current_user.id)
            if assigned_project_ids:
                if vm.project_id not in assigned_project_ids and vm.assigned_user_id != current_user.id:
                    raise HTTPException(status_code=403, detail="No access to this VM")
            elif vm.created_by != current_user.id and vm.assigned_user_id != current_user.id:
                raise HTTPException(status_code=403, detail="No access to this VM")

    return vm


# --- VM Clone ---


@router.post("/{vm_id}/clone", response_model=VMResponse, status_code=status.HTTP_201_CREATED)
async def clone_vm_endpoint(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: VMCloneRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Clone an existing VM."""
    source_vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if source_vm.status not in (VMStatus.RUNNING, VMStatus.STOPPED):
        raise HTTPException(status_code=400, detail="VM must be running or stopped to clone")

    try:
        new_vm, task = await clone_vm(
            db=db,
            tenant_id=tenant_id,
            user=current_user,
            source_vm=source_vm,
            body=body,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await create_audit_log(
        db,
        action="vm_cloned",
        resource_type="virtual_machine",
        resource_id=new_vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"VM '{source_vm.name}' cloned as '{body.name}'",
    )

    await db.commit()

    from app.workers.vm_tasks import task_vm_clone
    task_vm_clone.delay(str(new_vm.id), str(task.id))

    return await _vm_response_one(db, new_vm)


# --- Helper for host agent calls ---

AGENT_BASE_URL = settings.HOST_AGENT_URL
AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}


# --- VM Stats (per-VM resource usage) ---


@router.get("/{vm_id}/stats")
async def get_vm_stats(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get real-time CPU/RAM/Disk/Network stats for a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/stats",
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VM stats could not be retrieved: {e}")


# --- VM Snapshots ---


class SnapshotCreateRequest(BaseModel):
    name: str
    description: str = ""


class VMFirewallPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class VMFirewallDirection(BaseModel):
    enabled: bool = True
    allowed_ports: list[VMFirewallPortRule] = []


class VMFirewallProfileUpdate(BaseModel):
    inbound: VMFirewallDirection | None = None
    outbound: VMFirewallDirection | None = None
    bridge: str | None = None


async def _get_vm_primary_ip(db: AsyncSession, vm_id: uuid.UUID) -> str | None:
    """Resolve the primary allocated IP for a VM."""
    alloc = (
        await db.execute(
            select(IPAllocation)
            .where(
                IPAllocation.vm_id == vm_id,
                IPAllocation.status == AllocationStatus.ALLOCATED,
            )
            .order_by(IPAllocation.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not alloc:
        return None
    return str(alloc.ip_address) if not hasattr(alloc.ip_address, "ip") else str(alloc.ip_address.ip)


@router.get("/{vm_id}/firewall-profile")
async def get_vm_firewall_profile(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get per-VM firewall profile."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    vm_ip = await _get_vm_primary_ip(db, vm.id)
    if not vm_ip:
        raise HTTPException(status_code=400, detail="VM has no allocated IP")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/vm-firewall/{vm.id}",
                headers=AGENT_HEADERS,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            data = resp.json()
            data["vm_ip"] = vm_ip
            data["bridge"] = vm.bridge
            return data
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{vm_id}/firewall-profile")
async def update_vm_firewall_profile(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: VMFirewallProfileUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update per-VM firewall profile and apply immediately."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    vm_ip = await _get_vm_primary_ip(db, vm.id)
    if not vm_ip:
        raise HTTPException(status_code=400, detail="VM has no allocated IP")

    payload: dict = {
        "vm_ip": vm_ip,
        "bridge": body.bridge or vm.bridge or "br0",
    }
    if body.inbound is not None:
        payload["inbound"] = body.inbound.model_dump()
    if body.outbound is not None:
        payload["outbound"] = body.outbound.model_dump()

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.put(
                f"{AGENT_BASE_URL}/api/v1/security/vm-firewall/{vm.id}",
                headers=AGENT_HEADERS,
                json=payload,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            result = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await create_audit_log(
        db,
        action="vm_firewall_updated",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Per-VM firewall updated for '{vm.name}'",
    )
    await db.commit()
    return result


@router.get("/{vm_id}/firewall-log")
async def get_vm_firewall_log(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
    lines: int = 50,
):
    """Recent blocked traffic log entries for this VM's per-VM firewall."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=20) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/vm-firewall/{vm.id}/log",
                headers=AGENT_HEADERS,
                params={"lines": lines},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{vm_id}/firewall-profile")
async def delete_vm_firewall_profile(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete per-VM firewall profile and remove iptables hooks/chains."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.delete(
                f"{AGENT_BASE_URL}/api/v1/security/vm-firewall/{vm.id}",
                headers=AGENT_HEADERS,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            result = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await create_audit_log(
        db,
        action="vm_firewall_deleted",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Per-VM firewall reset for '{vm.name}'",
    )
    await db.commit()
    return result


@router.get("/{vm_id}/snapshots")
async def list_snapshots(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all snapshots for a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if not vm.libvirt_domain_name:
        return []

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/snapshots",
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{vm_id}/snapshots", status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: SnapshotCreateRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a snapshot of a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/snapshots",
                headers=AGENT_HEADERS,
                json={"name": body.name, "description": body.description},
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await create_audit_log(
        db,
        action="snapshot_created",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Snapshot '{body.name}' created for VM '{vm.name}'",
    )

    return result


@router.post("/{vm_id}/snapshots/{snapshot_name}/revert")
async def revert_snapshot(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    snapshot_name: str,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Revert a VM to a specific snapshot."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/snapshots/{snapshot_name}/revert",
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await create_audit_log(
        db,
        action="snapshot_reverted",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"VM '{vm.name}' reverted to snapshot '{snapshot_name}'",
    )

    return result


@router.delete("/{vm_id}/snapshots/{snapshot_name}")
async def delete_snapshot(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    snapshot_name: str,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a VM snapshot."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            resp = await client.delete(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/snapshots/{snapshot_name}",
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await create_audit_log(
        db,
        action="snapshot_deleted",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Snapshot '{snapshot_name}' deleted from VM '{vm.name}'",
    )

    return result


# --- Secondary NICs ---


def _generate_mac():
    """Generate a random MAC address with QEMU prefix."""
    import random
    mac = [0x52, 0x54, 0x00,
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff)]
    return ':'.join(f'{b:02x}' for b in mac)


@router.post("/{vm_id}/secondary-nics", status_code=status.HTTP_201_CREATED)
async def add_secondary_nic(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: AddSecondaryNICRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add a secondary NIC to a stopped VM (for internal VM-to-VM networks)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.STOPPED:
        raise HTTPException(status_code=409, detail="VM must be stopped")

    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    # Verify the pool exists and is INTERNAL
    from app.services.network_service import pool_visible_to_tenant

    pool = (await db.execute(
        select(IPPool).where(IPPool.id == body.pool_id, IPPool.is_active.is_(True))
    )).scalar_one_or_none()

    if not pool:
        raise HTTPException(status_code=404, detail="Network pool not found")

    if pool.pool_type != PoolType.INTERNAL:
        raise HTTPException(status_code=400, detail="Secondary NICs are only available for internal networks")

    if not await pool_visible_to_tenant(db, pool, tenant_id):
        raise HTTPException(status_code=403, detail="Network is not available for this tenant")

    if not pool.libvirt_network_name:
        raise HTTPException(status_code=400, detail="Pool has no libvirt network name")

    # Check limit (max 4 secondary NICs)
    current_nics = vm.secondary_interfaces or []
    if len(current_nics) >= 4:
        raise HTTPException(status_code=400, detail="Maximum 4 secondary NICs allowed")

    # Check if already connected to this network
    for nic in current_nics:
        if nic.get("pool_id") == str(body.pool_id):
            raise HTTPException(status_code=409, detail="VM is already connected to this network")

    mac_address = _generate_mac()

    # Call Host Agent to add NIC
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/add-nic",
                headers=AGENT_HEADERS,
                json={
                    "mac_address": mac_address,
                    "network_name": pool.libvirt_network_name,
                },
            )
            resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except httpx.HTTPStatusError as e:
        detail = "Adding NIC failed"
        try:
            detail = e.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=detail)

    # Update DB
    new_nic = {
        "mac_address": mac_address,
        "network_name": pool.libvirt_network_name,
        "pool_id": str(body.pool_id),
        "pool_name": pool.name,
        "subnet": pool.network,
        "dhcp_enabled": bool(pool.internal_dhcp_enabled),
    }
    updated_nics = list(current_nics) + [new_nic]
    vm.secondary_interfaces = updated_nics
    db.add(vm)

    await create_audit_log(
        db,
        action="vm_nic_added",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Secondary NIC added: {pool.name} ({pool.network})",
    )

    await db.flush()
    await db.refresh(vm)
    return {"status": "success", "nic": new_nic, "total_secondary_nics": len(updated_nics)}


@router.delete("/{vm_id}/secondary-nics/{mac_address}")
async def remove_secondary_nic(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    mac_address: str,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Remove a secondary NIC from a stopped VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.STOPPED:
        raise HTTPException(status_code=409, detail="VM must be stopped")

    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no domain name")

    current_nics = vm.secondary_interfaces or []
    nic_to_remove = None
    for nic in current_nics:
        if nic.get("mac_address") == mac_address:
            nic_to_remove = nic
            break

    if not nic_to_remove:
        raise HTTPException(status_code=404, detail="Secondary NIC not found")

    # Call Host Agent to remove NIC
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/remove-nic",
                headers=AGENT_HEADERS,
                json={
                    "mac_address": mac_address,
                    "network_name": nic_to_remove.get("network_name", ""),
                },
            )
            resp.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception:
        pass  # Best effort - remove from DB anyway

    # Update DB
    updated_nics = [n for n in current_nics if n.get("mac_address") != mac_address]
    vm.secondary_interfaces = updated_nics
    db.add(vm)

    await create_audit_log(
        db,
        action="vm_nic_removed",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Secondary NIC removed: {nic_to_remove.get('pool_name', mac_address)}",
    )

    await db.flush()
    return {"status": "success", "message": "NIC removed"}


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGE IP
# ══════════════════════════════════════════════════════════════════════════════


class ChangeIPRequest(BaseModel):
    ip_address: str  # Specific IP to assign (must be available in same pool)


class ChangeIPResponse(BaseModel):
    old_ip: str
    new_ip: str
    gateway: str
    dns: list[str]


class VMPasswordResetResponse(BaseModel):
    status: str
    message: str
    reboot_required: bool


@router.post("/{vm_id}/reset-password", response_model=VMPasswordResetResponse)
async def reset_vm_password(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: VMPasswordResetRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Reset VM guest password via Cloud-Init seed regeneration."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status in (VMStatus.CREATING, VMStatus.DELETING, VMStatus.DELETED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Password reset is not available while VM is in '{vm.status.value}' status",
        )
    if not vm.libvirt_domain_name:
        raise HTTPException(status_code=400, detail="VM has no host domain reference")

    cloud_init_userdata = dict(vm.cloud_init_userdata or {})
    cloud_init_metadata = dict(vm.cloud_init_metadata or {})
    users = cloud_init_userdata.get("users")
    if not isinstance(users, list):
        users = []

    # Dynamic behavior: use selected user; fallback to "cloud".
    requested_username = (body.default_user or "").strip()
    username = requested_username or "cloud"
    live_reset_error: str | None = None

    # Try immediate reset via qemu-guest-agent when VM is running.
    if vm.status == VMStatus.RUNNING:
        try:
            async with httpx.AsyncClient(
                base_url=settings.HOST_AGENT_URL,
                headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
                verify=AGENT_SSL_VERIFY,
                timeout=40.0,
            ) as client:
                live_resp = await client.post(
                    f"/api/v1/vms/{vm.libvirt_domain_name}/guest-password",
                    json={
                        "username": username,
                        "new_password": body.new_password,
                        "enable_password_ssh": not body.disable_password_login,
                    },
                )
                if live_resp.status_code >= 400:
                    live_reset_error = live_resp.text
        except Exception as e:
            live_reset_error = str(e)

    user_updated = False
    for entry in users:
        if isinstance(entry, dict) and entry.get("name") == username:
            entry["lock_passwd"] = False
            user_updated = True
            break
    if not user_updated:
        users.append(
            {
                "name": username,
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "shell": "/bin/bash",
                "lock_passwd": False,
            }
        )

    cloud_init_userdata["users"] = users
    cloud_init_userdata["ssh_pwauth"] = not body.disable_password_login
    # Password only for host-agent seed regeneration — never persist plaintext in DB
    seed_userdata = dict(cloud_init_userdata)
    seed_userdata["chpasswd"] = {
        "expire": False,
        "list": [f"{username}:{body.new_password}"],
    }
    cloud_init_userdata["chpasswd"] = {"expire": False}
    if not body.disable_password_login:
        # Hardened fallback for existing VMs:
        # ensure SSH password auth is enabled and SSH service is restarted.
        forced_ssh_cmds = [
            "sed -ri 's/^\\s*#?\\s*PasswordAuthentication\\s+.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || true",
            "grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config",
            "systemctl restart ssh || systemctl restart sshd || service ssh restart || service sshd restart || true",
        ]
        existing_runcmd = cloud_init_userdata.get("runcmd", [])
        if not isinstance(existing_runcmd, list):
            existing_runcmd = []
        for cmd in forced_ssh_cmds:
            if cmd not in existing_runcmd:
                existing_runcmd.append(cmd)
        cloud_init_userdata["runcmd"] = existing_runcmd
        seed_userdata["runcmd"] = list(existing_runcmd)

    # Force a fresh Cloud-Init run on next boot for changed credentials.
    cloud_init_metadata["instance-id"] = str(uuid.uuid4())

    from app.services.cloud_init_sanitize import sanitize_cloud_init_userdata

    vm.cloud_init_userdata = sanitize_cloud_init_userdata(cloud_init_userdata)
    vm.cloud_init_metadata = cloud_init_metadata

    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(vm, "cloud_init_userdata")
    flag_modified(vm, "cloud_init_metadata")
    db.add(vm)
    await db.flush()

    try:
        async with httpx.AsyncClient(
            base_url=settings.HOST_AGENT_URL,
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            verify=AGENT_SSL_VERIFY,
            timeout=30.0,
        ) as client:
            resp = await client.post(
                "/api/v1/vms/regenerate-seed",
                json={
                    "vm_id": str(vm.id),
                    "cloud_init_userdata": seed_userdata,
                    "cloud_init_metadata": cloud_init_metadata,
                    "cloud_init_network": vm.cloud_init_network or {},
                },
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to regenerate Cloud-Init seed: {resp.text}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Host agent unreachable: {e}",
        )

    await create_audit_log(
        db,
        action="vm_password_reset",        resource_type="vm",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Guest password reset for VM '{vm.name}' (user '{username}')",
        metadata={"username": username},
    )

    reboot_required = vm.status == VMStatus.RUNNING
    if vm.status == VMStatus.RUNNING and not live_reset_error:
        message = f"Password for user '{username}' was reset immediately (running VM) and persisted for next boot."
        reboot_required = False
    elif reboot_required:
        message = f"Password reset for user '{username}' prepared. Reboot VM to apply changes."
    else:
        message = f"Password reset for user '{username}' prepared. Start VM to apply changes."
    if live_reset_error:
        message += " Live reset via guest-agent failed; cloud-init fallback was saved."
    return VMPasswordResetResponse(
        status="success",
        message=message,
        reboot_required=reboot_required,
    )


@router.post("/{vm_id}/change-ip", response_model=ChangeIPResponse)
async def change_vm_ip(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: ChangeIPRequest,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Change the IP address of a VM (VM must be stopped)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VM must be stopped to change IP address",
        )

    # Find the current IP allocation for this VM
    current_alloc = (
        await db.execute(
            select(IPAllocation).where(
                IPAllocation.vm_id == vm.id,
                IPAllocation.status == AllocationStatus.ALLOCATED,
            )
        )
    ).scalar_one_or_none()

    if not current_alloc:
        raise HTTPException(status_code=404, detail="No IP allocation found for this VM")

    pool_id = current_alloc.pool_id
    old_ip = str(current_alloc.ip_address)

    # Normalize: strip possible /xx CIDR suffix from the request
    requested_ip = body.ip_address.split("/")[0].strip()

    if requested_ip == old_ip:
        raise HTTPException(status_code=400, detail="New IP is the same as current IP")

    # Find the requested IP in the same pool (INET column needs explicit cast)
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import INET

    # Get pool info for network config
    pool = (await db.execute(select(IPPool).where(IPPool.id == pool_id))).scalar_one()

    if pool.pool_type == PoolType.EXTERNAL:
        new_alloc = (
            await db.execute(
                select(IPAllocation).where(
                    IPAllocation.pool_id == pool_id,
                    IPAllocation.ip_address == cast(requested_ip, INET),
                    IPAllocation.tenant_id == tenant_id,
                    IPAllocation.status == AllocationStatus.RESERVED,
                )
            )
        ).scalar_one_or_none()
    else:
        new_alloc = (
            await db.execute(
                select(IPAllocation).where(
                    IPAllocation.pool_id == pool_id,
                    IPAllocation.ip_address == cast(requested_ip, INET),
                    IPAllocation.status == AllocationStatus.AVAILABLE,
                )
            )
        ).scalar_one_or_none()

    if not new_alloc:
        raise HTTPException(
            status_code=400,
            detail=f"IP {requested_ip} is not available in this pool",
        )

    # Release old IP (public stays reserved for tenant)
    current_alloc.vm_id = None
    if pool.pool_type == PoolType.EXTERNAL and current_alloc.tenant_id == tenant_id:
        current_alloc.status = AllocationStatus.RESERVED
    else:
        current_alloc.status = AllocationStatus.AVAILABLE
        if pool.tenant_id is None:
            current_alloc.tenant_id = None
    db.add(current_alloc)

    # Allocate new IP
    new_alloc.status = AllocationStatus.ALLOCATED
    new_alloc.vm_id = vm.id
    new_alloc.tenant_id = tenant_id
    db.add(new_alloc)

    # Update Cloud-Init network config
    cidr_prefix = "/" + pool.network.split("/")[1] if "/" in pool.network else "/24"
    new_ip_cidr = requested_ip + cidr_prefix

    dns_servers = [pool.dns_primary]
    if pool.dns_secondary:
        dns_servers.append(pool.dns_secondary)

    cloud_init_network = vm.cloud_init_network or {}
    ethernets = cloud_init_network.get("ethernets", {})

    # Update the first ethernet interface
    for _iface_name, iface_cfg in ethernets.items():
        if isinstance(iface_cfg, dict) and "addresses" in iface_cfg:
            iface_cfg["addresses"] = [new_ip_cidr]
            iface_cfg["gateway4"] = pool.gateway
            iface_cfg["nameservers"] = {"addresses": dns_servers}
            break

    cloud_init_network["ethernets"] = ethernets
    vm.cloud_init_network = cloud_init_network

    # Force cloud-init to re-apply network on next boot (same pattern as password reset)
    cloud_init_metadata = dict(vm.cloud_init_metadata or {})
    cloud_init_metadata["instance-id"] = str(uuid.uuid4())
    vm.cloud_init_metadata = cloud_init_metadata

    # Force SQLAlchemy to detect JSONB change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(vm, "cloud_init_network")
    flag_modified(vm, "cloud_init_metadata")

    await db.flush()

    # Regenerate Cloud-Init seed on the host agent
    try:
        async with httpx.AsyncClient(
            base_url=settings.HOST_AGENT_URL,
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            verify=AGENT_SSL_VERIFY,
            timeout=30.0,
        ) as client:
            resp = await client.post(
                "/api/v1/vms/regenerate-seed",
                json={
                    # Seed path is keyed by DB VM UUID (same as provision), not domain name
                    "vm_id": str(vm.id),
                    "cloud_init_userdata": vm.cloud_init_userdata or {},
                    "cloud_init_metadata": cloud_init_metadata,
                    "cloud_init_network": cloud_init_network,
                },
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to regenerate Cloud-Init seed: {resp.text}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Host agent unreachable: {e}",
        )

    await create_audit_log(
        db,
        action="vm_ip_changed",
        resource_type="vm",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"IP changed from {old_ip} to {requested_ip} for VM '{vm.name}'",
    )

    return ChangeIPResponse(
        old_ip=old_ip,
        new_ip=requested_ip,
        gateway=pool.gateway,
        dns=dns_servers,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  ADDITIONAL DISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════


def _next_target_dev(existing_devs: list[str]) -> str:
    """Compute the next available virtio device name (vdb, vdc, ...)."""
    used = set(existing_devs)
    for letter in "bcdefghijklmnop":
        dev = f"vd{letter}"
        if dev not in used:
            return dev
    raise HTTPException(status_code=400, detail="No more disk slots available (max 15 data disks)")


@router.get("/{vm_id}/disks", response_model=list[DiskResponse])
async def list_vm_disks(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all additional disks attached to a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    result = await db.execute(
        select(VMDisk).where(VMDisk.vm_id == vm.id).order_by(VMDisk.target_dev)
    )
    return result.scalars().all()


@router.post("/{vm_id}/disks", response_model=DiskResponse, status_code=status.HTTP_201_CREATED)
async def create_vm_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: DiskCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create and attach a new data disk to a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    # Determine storage pool path (shared or assigned to this tenant)
    from app.models.storage import StoragePool
    storage_pool_id = body.storage_pool_id or vm.storage_pool_id
    storage_path = None
    if storage_pool_id:
        pool = await db.get(StoragePool, storage_pool_id)
        if not pool or not pool.is_active:
            raise HTTPException(status_code=400, detail="Storage pool not found or inactive")
        if pool.tenant_id is not None and pool.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Storage pool is not available for this tenant")
        storage_path = pool.path

    # Find next available device name
    existing = await db.execute(select(VMDisk.target_dev).where(VMDisk.vm_id == vm.id))
    used_devs = [row[0] for row in existing.all()]
    target_dev = _next_target_dev(used_devs)

    # Sanitize disk name for filesystem
    safe_name = body.name.replace(" ", "_").replace("/", "_")[:50].lower()
    disk_name = f"{safe_name}_{target_dev}"

    # Create disk on host agent
    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=120) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/disks/create",
                json={
                    "vm_id": str(vm.id),
                    "disk_name": disk_name,
                    "size_gb": body.size_gb,
                    "disk_format": body.format.value,
                    "storage_base_path": storage_path,
                },
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create disk on host: {e}")

    disk_path = result["disk_path"]

    # Attach to VM if running or stopped
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/disks/attach",
                json={
                    "domain_name": vm.libvirt_domain_name,
                    "disk_path": disk_path,
                    "target_dev": target_dev,
                    "disk_format": body.format.value,
                    "bus": "virtio",
                    "readonly": False,
                },
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            attach_result = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Disk created but attach failed: {e}")

    # Save to database
    disk = VMDisk(
        vm_id=vm.id,
        name=body.name,
        storage_pool_id=storage_pool_id,
        disk_path=disk_path,
        size_gb=body.size_gb,
        format=body.format,
        bus="virtio",
        target_dev=target_dev,
        status=DiskStatus.ATTACHED,
    )
    db.add(disk)

    await create_audit_log(
        db,
        action="disk_created",
        resource_type="vm_disk",
        resource_id=disk.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Disk '{body.name}' ({body.size_gb} GB) attached to VM '{vm.name}' as {target_dev}"
            + (f" [hot-attach]" if attach_result.get("hot_attach") else ""),
    )

    await db.flush()
    await db.refresh(disk)
    return disk


@router.post("/{vm_id}/disks/{disk_id}/detach", response_model=DiskResponse)
async def detach_vm_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    disk_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Detach a disk from a VM (hot-detach if running). Keeps the disk file."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    disk = await db.get(VMDisk, disk_id)
    if not disk or disk.vm_id != vm.id:
        raise HTTPException(status_code=404, detail="Disk not found")
    if disk.status != DiskStatus.ATTACHED:
        raise HTTPException(status_code=409, detail=f"Disk is not attached (status: {disk.status.value})")

    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/disks/detach",
                json={
                    "domain_name": vm.libvirt_domain_name,
                    "disk_path": disk.disk_path,
                    "target_dev": disk.target_dev,
                },
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Detach failed: {e}")

    disk.status = DiskStatus.AVAILABLE

    await create_audit_log(
        db,
        action="disk_detached",
        resource_type="vm_disk",
        resource_id=disk.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Disk '{disk.name}' detached from VM '{vm.name}'"
            + (f" [hot-detach]" if result.get("hot_detach") else ""),
    )

    await db.flush()
    await db.refresh(disk)
    return disk


@router.post("/{vm_id}/disks/{disk_id}/attach", response_model=DiskResponse)
async def reattach_vm_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    disk_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-attach a previously detached disk (hot-attach if running)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    disk = await db.get(VMDisk, disk_id)
    if not disk or disk.vm_id != vm.id:
        raise HTTPException(status_code=404, detail="Disk not found")
    if disk.status != DiskStatus.AVAILABLE:
        raise HTTPException(status_code=409, detail=f"Disk is not available (status: {disk.status.value})")

    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/disks/attach",
                json={
                    "domain_name": vm.libvirt_domain_name,
                    "disk_path": disk.disk_path,
                    "target_dev": disk.target_dev,
                    "disk_format": disk.format.value,
                    "bus": disk.bus,
                    "readonly": disk.is_readonly,
                },
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Attach failed: {e}")

    disk.status = DiskStatus.ATTACHED

    await create_audit_log(
        db,
        action="disk_reattached",
        resource_type="vm_disk",
        resource_id=disk.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Disk '{disk.name}' re-attached to VM '{vm.name}'"
            + (f" [hot-attach]" if result.get("hot_attach") else ""),
    )

    await db.flush()
    await db.refresh(disk)
    return disk


@router.patch("/{vm_id}/disks/{disk_id}/resize", response_model=DiskResponse)
async def resize_vm_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    disk_id: uuid.UUID,
    body: DiskResize,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Resize a disk (grow only). For safety, detach first or stop the VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    disk = await db.get(VMDisk, disk_id)
    if not disk or disk.vm_id != vm.id:
        raise HTTPException(status_code=404, detail="Disk not found")

    if body.size_gb <= disk.size_gb:
        raise HTTPException(status_code=400, detail=f"New size ({body.size_gb} GB) must be larger than current ({disk.size_gb} GB)")

    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=120) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/disks/resize",
                json={
                    "disk_path": disk.disk_path,
                    "new_size_gb": body.size_gb,
                },
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Resize failed: {e}")

    old_size = disk.size_gb
    disk.size_gb = body.size_gb

    await create_audit_log(
        db,
        action="disk_resized",
        resource_type="vm_disk",
        resource_id=disk.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Disk '{disk.name}' resized: {old_size} GB → {body.size_gb} GB",
    )

    await db.flush()
    await db.refresh(disk)
    return disk


@router.delete("/{vm_id}/disks/{disk_id}")
async def delete_vm_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    disk_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a disk (detaches first if needed, deletes file from host)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    disk = await db.get(VMDisk, disk_id)
    if not disk or disk.vm_id != vm.id:
        raise HTTPException(status_code=404, detail="Disk not found")

    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    # Detach if still attached
    if disk.status == DiskStatus.ATTACHED:
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=60) as client:
                resp = await client.post(
                    f"{AGENT_BASE_URL}/api/v1/disks/detach",
                    json={
                        "domain_name": vm.libvirt_domain_name,
                        "disk_path": disk.disk_path,
                        "target_dev": disk.target_dev,
                    },
                    headers=AGENT_HEADERS,
                )
                resp.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Detach before delete failed: {e}")

    # Delete file on host
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            resp = await client.delete(
                f"{AGENT_BASE_URL}/api/v1/disks/file",
                params={"disk_path": disk.disk_path},
                headers=AGENT_HEADERS,
            )
            resp.raise_for_status()
    except Exception:
        pass  # File may already be gone

    disk_name = disk.name
    await db.delete(disk)

    await create_audit_log(
        db,
        action="disk_deleted",
        resource_type="vm_disk",
        resource_id=disk_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Disk '{disk_name}' deleted from VM '{vm.name}'",
    )

    return {"status": "success", "message": f"Disk '{disk_name}' deleted"}


# ══════════════════════════════════════════════════════════════════════════════
#  VM EXPORT / IMPORT
# ══════════════════════════════════════════════════════════════════════════════


@router.post("/{vm_id}/export")
async def export_vm(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Export a VM as a downloadable qcow2 file (VM must be stopped)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.STOPPED:
        raise HTTPException(status_code=409, detail="VM must be stopped for export")

    task = Task(
        task_type=TaskType.VM_EXPORT,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    from app.workers.vm_tasks import task_vm_export
    task_vm_export.delay(str(vm.id), str(task.id))

    await create_audit_log(
        db,
        action="vm_export_started",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Export started for VM '{vm.name}'",
    )

    return {"task_id": str(task.id), "status": "export_started", "message": f"Export for VM '{vm.name}' started"}


@router.get("/{vm_id}/export/download")
async def download_export(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Download an exported VM disk image (proxied from Host Agent)."""
    from fastapi.responses import StreamingResponse

    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    # Find the most recent successful export task
    result = await db.execute(
        select(Task).where(
            Task.resource_id == vm.id,
            Task.task_type == TaskType.VM_EXPORT,
            Task.status == TaskStatus.SUCCESS,
        ).order_by(Task.completed_at.desc()).limit(1)
    )
    export_task = result.scalar_one_or_none()
    if not export_task or not export_task.result:
        raise HTTPException(status_code=404, detail="No export available. Start an export first.")

    file_name = export_task.result.get("file_name")
    if not file_name:
        raise HTTPException(status_code=404, detail="Export file not found")

    AGENT_BASE_URL = settings.HOST_AGENT_URL
    AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}

    async def stream_from_agent():
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=7200) as client:
            async with client.stream(
                "GET",
                f"{AGENT_BASE_URL}/api/v1/vms/export/{file_name}",
                headers=AGENT_HEADERS,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                    yield chunk

    return StreamingResponse(
        stream_from_agent(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/{vm_id}/export/status")
async def export_status(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Check the status of the latest export task for a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    result = await db.execute(
        select(Task).where(
            Task.resource_id == vm.id,
            Task.task_type == TaskType.VM_EXPORT,
        ).order_by(Task.created_at.desc()).limit(1)
    )
    export_task = result.scalar_one_or_none()
    if not export_task:
        return {"status": "none", "message": "No export has been started"}

    resp = {
        "task_id": str(export_task.id),
        "status": export_task.status.value,
        "created_at": export_task.created_at.isoformat() if export_task.created_at else None,
        "completed_at": export_task.completed_at.isoformat() if export_task.completed_at else None,
    }
    if export_task.status == TaskStatus.SUCCESS and export_task.result:
        resp["file_name"] = export_task.result.get("file_name")
        resp["file_size_bytes"] = export_task.result.get("file_size_bytes")
    if export_task.status == TaskStatus.FAILED:
        resp["error"] = export_task.error_message

    return resp


from pydantic import Field as PydanticField


class VMImportCreate(BaseModel):
    name: str
    vcpus: int = 1
    ram_mb: int = 1024
    disk_size_gb: int = 20
    project_id: uuid.UUID | None = None
    storage_pool_id: uuid.UUID | None = None
    bridge: str = "virbr0"


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_vm(
    tenant_id: uuid.UUID,
    body: VMImportCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Create a VM record for importing an existing disk image.
    Returns the VM ID and upload URL for the disk file.
    """
    import random as rnd

    mac = "52:54:00:{:02x}:{:02x}:{:02x}".format(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
    domain_name = f"qi-{body.name[:20].lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"

    # Resolve storage pool (shared/global or assigned to this tenant)
    from app.models.storage import StoragePool
    storage_pool_id = body.storage_pool_id
    if storage_pool_id:
        chosen = await db.get(StoragePool, storage_pool_id)
        if not chosen or not chosen.is_active:
            raise HTTPException(status_code=400, detail="Storage pool not found or inactive")
        if chosen.tenant_id is not None and chosen.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Storage pool is not available for this tenant")
    else:
        result = await db.execute(
            select(StoragePool).where(
                StoragePool.is_default.is_(True),
                StoragePool.is_active.is_(True),
                or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id == tenant_id),
            )
        )
        default_pool = result.scalar_one_or_none()
        if default_pool:
            storage_pool_id = default_pool.id

    vm = VirtualMachine(
        name=body.name,
        tenant_id=tenant_id,
        project_id=body.project_id,
        created_by=current_user.id,
        vcpus=body.vcpus,
        ram_mb=body.ram_mb,
        disk_size_gb=body.disk_size_gb,
        mac_address=mac,
        bridge=body.bridge,
        libvirt_domain_name=domain_name,
        status=VMStatus.STOPPED,
        storage_pool_id=storage_pool_id,
    )
    # Mark as "importing" (creating state until import is done)
    vm.status = VMStatus.CREATING
    db.add(vm)
    await db.flush()
    await db.refresh(vm)

    # Prepare storage on host agent
    storage_path = None
    if storage_pool_id:
        pool = await db.get(StoragePool, storage_pool_id)
        if pool and pool.is_active:
            storage_path = pool.path

    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            resp = await client.post(
                f"{settings.HOST_AGENT_URL}/api/v1/vms/import/prepare",
                json={
                    "vm_id": str(vm.id),
                    "domain_name": domain_name,
                    "disk_format": "qcow2",
                    "storage_base_path": storage_path,
                },
                headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            )
            resp.raise_for_status()
    except Exception as e:
        vm.status = VMStatus.ERROR
        db.add(vm)
        raise HTTPException(status_code=502, detail=f"Failed to prepare import on host: {e}")

    await create_audit_log(
        db,
        action="vm_import_started",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM import started: '{body.name}'",
    )

    return {
        "vm_id": str(vm.id),
        "domain_name": domain_name,
        "status": "ready_for_upload",
        "upload_url": f"/api/v1/tenants/{tenant_id}/vms/{vm.id}/import/upload",
    }


@router.put("/{vm_id}/import/upload")
async def upload_import_disk(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Upload a qcow2 disk file for import (streamed through to Host Agent).
    After upload, call /import/finalize to register the VM in libvirt.
    """
    from fastapi import Request
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.CREATING:
        raise HTTPException(status_code=409, detail="VM is not in import state")

    # For now return the info about how to upload
    return {
        "message": "Upload the qcow2 file via PUT to the host agent directly, or use the finalize endpoint after placing the disk.",
        "vm_id": str(vm.id),
    }


@router.post("/{vm_id}/import/finalize")
async def finalize_import(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register the imported VM in libvirt and mark it as ready."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.CREATING:
        raise HTTPException(status_code=409, detail="VM is not in import state")

    task = Task(
        task_type=TaskType.VM_IMPORT,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    from app.workers.vm_tasks import task_vm_import_register
    task_vm_import_register.delay(str(vm.id), str(task.id))

    return {"task_id": str(task.id), "status": "registering", "message": "VM is being registered in libvirt"}


# ══════════════════════════════════════════════════════════════════════════════
#  BACKUPS
# ══════════════════════════════════════════════════════════════════════════════

from app.models.backup import VMBackup, BackupStatus, BackupType, BackupSchedule, ScheduleFrequency
from app.schemas.backup import (
    BackupCreate, BackupResponse, BackupListResponse,
    BackupScheduleCreate, BackupScheduleUpdate, BackupScheduleResponse,
)


@router.get("/{vm_id}/backups", response_model=BackupListResponse)
async def list_backups(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all backups for a VM."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    result = await db.execute(
        select(VMBackup)
        .where(VMBackup.vm_id == vm.id)
        .order_by(VMBackup.created_at.desc())
    )
    backups = list(result.scalars().all())

    return BackupListResponse(items=backups, total=len(backups))


@router.post("/{vm_id}/backups", response_model=BackupResponse, status_code=status.HTTP_201_CREATED)
async def create_backup(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: BackupCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a backup of a VM (offline or live via temporary disk snapshot)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status not in (VMStatus.STOPPED, VMStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"VM must be stopped or running for backup (current: {vm.status.value})",
        )

    # Create backup record
    backup = VMBackup(
        vm_id=vm.id,
        tenant_id=tenant_id,
        created_by=current_user.id,
        name=body.name,
        description=body.description,
        backup_type=BackupType.FULL,
        status=BackupStatus.CREATING,
        vm_name=vm.name,
        vm_vcpus=vm.vcpus,
        vm_ram_mb=vm.ram_mb,
        vm_disk_size_gb=vm.disk_size_gb,
    )
    db.add(backup)
    await db.flush()
    await db.refresh(backup)

    # Create task
    task = Task(
        task_type=TaskType.BACKUP_CREATE,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="vm_backup",
        resource_id=backup.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    # Launch Celery task
    from app.workers.vm_tasks import task_vm_backup
    task_vm_backup.delay(str(vm.id), str(backup.id), str(task.id))

    await create_audit_log(
        db,
        action="backup_created",
        resource_type="vm_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Backup '{body.name}' started for VM '{vm.name}'",
    )

    return backup


@router.post("/{vm_id}/backups/{backup_id}/restore")
async def restore_backup(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    backup_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Restore a VM from a backup (VM must be stopped)."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    if vm.status != VMStatus.STOPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VM must be stopped for restore",
        )

    result = await db.execute(
        select(VMBackup).where(VMBackup.id == backup_id, VMBackup.vm_id == vm.id)
    )
    backup = result.scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    if backup.status != BackupStatus.READY:
        raise HTTPException(status_code=409, detail="Backup is not ready")

    backup.status = BackupStatus.RESTORING
    db.add(backup)

    # Create task
    task = Task(
        task_type=TaskType.BACKUP_RESTORE,
        status=TaskStatus.QUEUED,
        user_id=current_user.id,
        tenant_id=tenant_id,
        resource_type="vm_backup",
        resource_id=backup.id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)

    from app.workers.vm_tasks import task_vm_restore
    task_vm_restore.delay(str(vm.id), str(backup.id), str(task.id))

    await create_audit_log(
        db,
        action="backup_restore_started",
        resource_type="vm_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Restore of backup '{backup.name}' for VM '{vm.name}' started",
    )

    return {"status": "restoring", "message": "Restore started"}


@router.delete("/{vm_id}/backups/{backup_id}", status_code=status.HTTP_200_OK)
async def delete_backup(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    backup_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a backup."""
    vm = await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    result = await db.execute(
        select(VMBackup).where(VMBackup.id == backup_id, VMBackup.vm_id == vm.id)
    )
    backup = result.scalar_one_or_none()
    if not backup:
        raise HTTPException(status_code=404, detail="Backup not found")

    from app.services.vm_backup_ops import backup_is_creating, purge_vm_backup_storage

    if backup_is_creating(backup):
        raise HTTPException(status_code=409, detail="Backup is currently being created")

    await purge_vm_backup_storage(db, backup)

    await create_audit_log(
        db,
        action="backup_deleted",
        resource_type="vm_backup",
        resource_id=backup.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Backup '{backup.name}' deleted",
    )

    # Same row the VM UI lists — admin inventory delete uses the identical helper.
    await db.delete(backup)
    return {"status": "success", "message": "Backup deleted"}


# ══════════════════════════════════════════════════════════════════════════════
#  BACKUP SCHEDULES
# ══════════════════════════════════════════════════════════════════════════════


def _compute_next_run(frequency: str, time_utc: str, day_of_week: int | None = None, day_of_month: int | None = None):
    """Compute the next run datetime from now."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    hour, minute = int(time_utc.split(":")[0]), int(time_utc.split(":")[1])
    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if frequency == "daily":
        if target_today > now:
            return target_today
        return target_today + timedelta(days=1)

    elif frequency == "weekly":
        dow = day_of_week if day_of_week is not None else 0  # default Monday
        days_ahead = dow - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and target_today <= now):
            days_ahead += 7
        return target_today + timedelta(days=days_ahead)

    elif frequency == "monthly":
        dom = day_of_month if day_of_month is not None else 1
        try:
            target = now.replace(day=dom, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            target = now.replace(day=28, hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            month = target.month + 1
            year = target.year
            if month > 12:
                month = 1
                year += 1
            try:
                target = target.replace(year=year, month=month, day=dom)
            except ValueError:
                target = target.replace(year=year, month=month, day=28)
        return target

    return target_today + timedelta(days=1)


@router.get("/{vm_id}/backup-schedules", response_model=list[BackupScheduleResponse])
async def list_backup_schedules(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all backup schedules for a VM."""
    await _get_vm_or_404(db, tenant_id, vm_id, current_user)
    result = await db.execute(
        select(BackupSchedule).where(BackupSchedule.vm_id == vm_id).order_by(BackupSchedule.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/{vm_id}/backup-schedules", response_model=BackupScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_backup_schedule(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    body: BackupScheduleCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a scheduled backup for a VM."""
    await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    # Validate: weekly needs day_of_week, monthly needs day_of_month
    if body.frequency == "weekly" and body.day_of_week is None:
        raise HTTPException(status_code=422, detail="day_of_week is required for weekly schedules (0=Mon..6=Sun)")
    if body.frequency == "monthly" and body.day_of_month is None:
        raise HTTPException(status_code=422, detail="day_of_month is required for monthly schedules (1-28)")

    next_run = _compute_next_run(body.frequency, body.time_utc, body.day_of_week, body.day_of_month)

    schedule = BackupSchedule(
        vm_id=vm_id,
        tenant_id=tenant_id,
        created_by=current_user.id,
        frequency=ScheduleFrequency(body.frequency),
        time_utc=body.time_utc,
        day_of_week=body.day_of_week,
        day_of_month=body.day_of_month,
        retention_count=body.retention_count,
        is_active=True,
        next_run_at=next_run,
    )
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)

    await create_audit_log(
        db,
        action="backup_schedule_created",
        resource_type="backup_schedule",
        resource_id=schedule.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Backup schedule created: {body.frequency} at {body.time_utc} UTC, keep {body.retention_count}",
    )

    return schedule


@router.patch("/{vm_id}/backup-schedules/{schedule_id}", response_model=BackupScheduleResponse)
async def update_backup_schedule(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    schedule_id: uuid.UUID,
    body: BackupScheduleUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a backup schedule."""
    await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    result = await db.execute(
        select(BackupSchedule).where(BackupSchedule.id == schedule_id, BackupSchedule.vm_id == vm_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "frequency":
            setattr(schedule, field, ScheduleFrequency(value))
        else:
            setattr(schedule, field, value)

    # Recompute next_run
    freq = update_data.get("frequency", schedule.frequency)
    if isinstance(freq, ScheduleFrequency):
        freq = freq.value
    schedule.next_run_at = _compute_next_run(
        freq,
        update_data.get("time_utc", schedule.time_utc),
        update_data.get("day_of_week", schedule.day_of_week),
        update_data.get("day_of_month", schedule.day_of_month),
    )

    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.delete("/{vm_id}/backup-schedules/{schedule_id}")
async def delete_backup_schedule(
    tenant_id: uuid.UUID,
    vm_id: uuid.UUID,
    schedule_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.USER))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a backup schedule."""
    await _get_vm_or_404(db, tenant_id, vm_id, current_user)

    result = await db.execute(
        select(BackupSchedule).where(BackupSchedule.id == schedule_id, BackupSchedule.vm_id == vm_id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    await create_audit_log(
        db,
        action="backup_schedule_deleted",
        resource_type="backup_schedule",
        resource_id=schedule.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Backup schedule deleted: {schedule.frequency.value} at {schedule.time_utc} UTC",
    )

    await db.delete(schedule)
    return {"status": "success", "message": "Backup schedule deleted"}
