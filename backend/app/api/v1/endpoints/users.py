"""
User management endpoints (Super Admin only).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.dependencies import require_superadmin
from app.core.security import hash_password
from app.models.user import User, TenantUser, UserRole
from app.models.tenant import Project, ProjectUser, Tenant
from app.models.vm import VirtualMachine, VMStatus
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/users", tags=["Users"])


class AssignableVMResponse(BaseModel):
    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    assigned_user_id: uuid.UUID | None = None


class UserVMAssignmentResponse(BaseModel):
    user_id: uuid.UUID
    vm_id: uuid.UUID
    vm_name: str
    tenant_id: uuid.UUID


class UserAssignVMRequest(BaseModel):
    vm_id: uuid.UUID | None = None


class AssignableProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    tenant_name: str


class UserProjectAssignmentResponse(BaseModel):
    user_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    tenant_id: uuid.UUID
    tenant_name: str


class UserAssignProjectsRequest(BaseModel):
    project_ids: list[uuid.UUID] = Field(default_factory=list)


@router.get("", response_model=list[UserResponse])
async def list_users(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all users (super admin only)."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


@router.get("/assignable-vms", response_model=list[AssignableVMResponse])
async def list_assignable_vms(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List VMs that can be assigned to users."""
    result = await db.execute(
        select(VirtualMachine)
        .where(VirtualMachine.is_deleted.is_(False))
        .order_by(VirtualMachine.created_at.desc())
    )
    vms = result.scalars().all()
    return [
        AssignableVMResponse(
            id=vm.id,
            name=vm.name,
            tenant_id=vm.tenant_id,
            assigned_user_id=vm.assigned_user_id,
        )
        for vm in vms
    ]


@router.get("/vm-assignments", response_model=list[UserVMAssignmentResponse])
async def list_vm_assignments(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List explicit VM assignments to users."""
    result = await db.execute(
        select(VirtualMachine)
        .where(
            VirtualMachine.is_deleted.is_(False),
            VirtualMachine.assigned_user_id.is_not(None),
        )
        .order_by(VirtualMachine.created_at.desc())
    )
    vms = result.scalars().all()
    return [
        UserVMAssignmentResponse(
            user_id=vm.assigned_user_id,  # type: ignore[arg-type]
            vm_id=vm.id,
            vm_name=vm.name,
            tenant_id=vm.tenant_id,
        )
        for vm in vms
    ]


@router.get("/assignable-projects", response_model=list[AssignableProjectResponse])
async def list_assignable_projects(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List active projects on active tenants that can be assigned to users."""
    result = await db.execute(
        select(Project, Tenant)
        .join(Tenant, Tenant.id == Project.tenant_id)
        .where(
            Project.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .order_by(Tenant.name.asc(), Project.name.asc())
    )
    rows = result.all()
    return [
        AssignableProjectResponse(
            id=project.id,
            name=project.name,
            tenant_id=project.tenant_id,
            tenant_name=tenant.name,
        )
        for project, tenant in rows
    ]


@router.get("/project-assignments", response_model=list[UserProjectAssignmentResponse])
async def list_project_assignments(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List explicit project assignments to users."""
    result = await db.execute(
        select(ProjectUser, Project, Tenant)
        .join(Project, Project.id == ProjectUser.project_id)
        .join(Tenant, Tenant.id == Project.tenant_id)
        .where(
            Project.is_active.is_(True),
            Tenant.is_active.is_(True),
        )
        .order_by(Tenant.name.asc(), Project.name.asc())
    )
    rows = result.all()
    return [
        UserProjectAssignmentResponse(
            user_id=project_user.user_id,
            project_id=project.id,
            project_name=project.name,
            tenant_id=project.tenant_id,
            tenant_name=tenant.name,
        )
        for project_user, project, tenant in rows
    ]


@router.put("/{user_id}/assigned-vm")
async def assign_single_vm_to_user(
    user_id: uuid.UUID,
    body: UserAssignVMRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Assign one additional VM to a user (or clear all assignments with vm_id=null).
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.vm_id is None:
        previous = (
            await db.execute(
                select(VirtualMachine).where(
                    VirtualMachine.assigned_user_id == user_id,
                    VirtualMachine.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        for vm in previous:
            vm.assigned_user_id = None
            db.add(vm)
        await db.flush()
        await create_audit_log(
            db,
            action="vm_assignment_cleared",
            resource_type="user",
            resource_id=user_id,
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"VM assignment cleared for user {user.username}",
        )
        return {"status": "success", "message": f"Assignment cleared for user '{user.username}'"}

    vm = (
        await db.execute(
            select(VirtualMachine).where(
                VirtualMachine.id == body.vm_id,
                VirtualMachine.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")
    if vm.assigned_user_id and vm.assigned_user_id != user_id:
        raise HTTPException(status_code=409, detail="VM is already assigned to another user")

    # Optional tenant safety: user must be active member of VM tenant.
    membership = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.user_id == user_id,
                TenantUser.tenant_id == vm.tenant_id,
                TenantUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not membership and not user.is_superadmin:
        raise HTTPException(
            status_code=400,
            detail="User is not an active member of the VM tenant",
        )

    if vm.assigned_user_id == user_id:
        return {
            "status": "success",
            "message": f"VM '{vm.name}' is already assigned to user '{user.username}'",
            "user_id": str(user_id),
            "vm_id": str(vm.id),
        }

    vm.assigned_user_id = user_id
    db.add(vm)
    await db.flush()

    await create_audit_log(
        db,
        action="vm_assigned_to_user",
        resource_type="virtual_machine",
        resource_id=vm.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"VM '{vm.name}' assigned to user '{user.username}'",
        metadata={"user_id": str(user_id), "vm_id": str(vm.id)},
    )

    return {
        "status": "success",
        "message": f"User '{user.username}' assigned to VM '{vm.name}'",
        "user_id": str(user_id),
        "vm_id": str(vm.id),
    }


@router.put("/{user_id}/assigned-projects")
async def assign_projects_to_user(
    user_id: uuid.UUID,
    body: UserAssignProjectsRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Replace all project assignments for a user."""
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    selected_project_ids = list(dict.fromkeys(body.project_ids))

    existing_assignments = (
        await db.execute(select(ProjectUser).where(ProjectUser.user_id == user_id))
    ).scalars().all()
    for assignment in existing_assignments:
        await db.delete(assignment)

    if not selected_project_ids:
        await db.flush()
        await create_audit_log(
            db,
            action="project_assignment_cleared",
            resource_type="user",
            resource_id=user_id,
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Project assignments cleared for user {user.username}",
        )
        return {"status": "success", "message": f"Project assignments cleared for '{user.username}'"}

    selected_projects = (
        await db.execute(
            select(Project).where(
                Project.id.in_(selected_project_ids),
                Project.is_active.is_(True),
            )
        )
    ).scalars().all()
    selected_project_map = {project.id: project for project in selected_projects}
    missing_ids = [str(project_id) for project_id in selected_project_ids if project_id not in selected_project_map]
    if missing_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Project(s) not found or inactive: {', '.join(missing_ids)}",
        )

    if not user.is_superadmin:
        tenant_ids = {project.tenant_id for project in selected_projects}
        memberships = (
            await db.execute(
                select(TenantUser).where(
                    TenantUser.user_id == user_id,
                    TenantUser.tenant_id.in_(tenant_ids),
                    TenantUser.is_active.is_(True),
                )
            )
        ).scalars().all()
        membership_tenant_ids = {membership.tenant_id for membership in memberships}
        invalid_tenants = [str(tenant_id) for tenant_id in tenant_ids if tenant_id not in membership_tenant_ids]
        if invalid_tenants:
            raise HTTPException(
                status_code=400,
                detail=f"User is not an active member of tenant(s): {', '.join(invalid_tenants)}",
            )

    for project in selected_projects:
        db.add(ProjectUser(project_id=project.id, user_id=user_id))

    await db.flush()
    await create_audit_log(
        db,
        action="project_assignment_updated",
        resource_type="user",
        resource_id=user_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Assigned {len(selected_projects)} project(s) to user {user.username}",
        metadata={"project_ids": [str(project_id) for project_id in selected_project_ids]},
    )

    return {
        "status": "success",
        "message": f"Assigned {len(selected_projects)} project(s) to user '{user.username}'",
        "user_id": str(user_id),
        "project_ids": [str(project_id) for project_id in selected_project_ids],
    }


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific user (super admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new user, optionally assigning to a tenant (super admin only)."""
    existing = await db.execute(
        select(User).where((User.email == body.email) | (User.username == body.username))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username already taken",
        )

    # Validate tenant if provided
    tenant = None
    if body.tenant_id:
        tenant = (await db.execute(
            select(Tenant).where(Tenant.id == uuid.UUID(body.tenant_id))
        )).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        is_superadmin=getattr(body, 'is_superadmin', False),
    )
    db.add(user)
    await db.flush()

    # Assign to tenant if provided
    if tenant:
        role_map = {
            "super_admin": UserRole.SUPER_ADMIN,
            "tenant_admin": UserRole.TENANT_ADMIN,
            "user": UserRole.USER,
            "readonly": UserRole.READONLY,
        }
        role = role_map.get(body.tenant_role, UserRole.USER)
        membership = TenantUser(
            tenant_id=tenant.id,
            user_id=user.id,
            role=role,
            max_vms=body.max_vms,
        )
        db.add(membership)
        await db.flush()

    # Optionally assign existing VMs from this tenant directly on user creation.
    if body.assigned_vm_ids:
        if not tenant:
            raise HTTPException(
                status_code=400,
                detail="VM assignment requires tenant assignment",
            )

        selected_vm_ids = list(dict.fromkeys(body.assigned_vm_ids))
        selected_vms = (
            await db.execute(
                select(VirtualMachine).where(
                    VirtualMachine.id.in_(selected_vm_ids),
                    VirtualMachine.tenant_id == tenant.id,
                    VirtualMachine.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        selected_vm_map = {vm.id: vm for vm in selected_vms}
        missing_ids = [str(vm_id) for vm_id in selected_vm_ids if vm_id not in selected_vm_map]
        if missing_ids:
            raise HTTPException(
                status_code=404,
                detail=f"VM(s) not found in selected tenant: {', '.join(missing_ids)}",
            )

        for vm in selected_vms:
            if vm.assigned_user_id and vm.assigned_user_id != user.id:
                raise HTTPException(
                    status_code=409,
                    detail=f"VM '{vm.name}' is already assigned to another user",
                )

        for vm in selected_vms:
            vm.assigned_user_id = user.id
            db.add(vm)
        await db.flush()

    await db.refresh(user)

    desc = f"User created: {user.username}"
    if tenant:
        desc += f" (Tenant: {tenant.name}, Role: {body.tenant_role})"
    if body.assigned_vm_ids:
        desc += f", assigned_vms={len(body.assigned_vm_ids)}"

    await create_audit_log(
        db,
        action="user_created",
        resource_type="user",
        resource_id=user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=desc,
    )

    return user


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a user (super admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    db.add(user)
    await db.flush()
    await db.refresh(user)

    await create_audit_log(
        db,
        action="user_updated",
        resource_type="user",
        resource_id=user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"User updated: {user.username}",
    )

    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    permanent: bool = Query(False, description="Permanently delete user instead of deactivating"),
):
    """Delete or deactivate a user (super admin only)."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete own account",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    username = user.username

    if permanent:
        # Block only while the user still owns active (non-deleted) VMs.
        active_vms = (await db.execute(
            select(VirtualMachine).where(
                VirtualMachine.created_by == user_id,
                VirtualMachine.is_deleted.is_(False),
                VirtualMachine.status != VMStatus.DELETED,
            )
        )).scalars().all()
        if active_vms:
            raise HTTPException(
                status_code=409,
                detail=f"User still has {len(active_vms)} active VM(s). Please delete them first.",
            )

        # Historical rows keep created_by WITHOUT ON DELETE → reassign to the actor
        # so soft-deleted VMs / templates / backups do not block permanent delete.
        from app.models.backup import BackupSchedule, VMBackup
        from app.models.load_balancer import LoadBalancer
        from app.models.reverse_proxy import ReverseProxyRule
        from app.models.vm_template import VMTemplate

        reassign_to = current_user.id
        for model in (
            VirtualMachine,
            VMBackup,
            BackupSchedule,
            ReverseProxyRule,
            LoadBalancer,
            VMTemplate,
        ):
            await db.execute(
                update(model)
                .where(model.created_by == user_id)
                .values(created_by=reassign_to)
            )

        memberships = (await db.execute(
            select(TenantUser).where(TenantUser.user_id == user_id)
        )).scalars().all()
        for m in memberships:
            await db.delete(m)

        project_memberships = (await db.execute(
            select(ProjectUser).where(ProjectUser.user_id == user_id)
        )).scalars().all()
        for m in project_memberships:
            await db.delete(m)

        await db.delete(user)
        action = "user_deleted"
        desc = f"User permanently deleted: {username}"
    else:
        user.is_active = False
        db.add(user)
        action = "user_deactivated"
        desc = f"User deactivated: {username}"

    await create_audit_log(
        db,
        action=action,
        resource_type="user",
        resource_id=user_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=desc,
    )
