"""
Tenant management endpoints.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import (
    PaginationParams,
    RequireTenantRole,
    enforce_api_key_tenant_scope,
    get_api_key_tenant_scope,
    get_current_user,
    require_superadmin,
)
from app.models.tenant import Project, Tenant
from app.models.user import TenantUser, User, UserRole
from app.schemas.tenant import (
    TenantCreate,
    TenantMemberResponse,
    TenantNotificationEmailUpdate,
    TenantResponse,
    TenantUpdate,
)
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/tenants", tags=["Tenants"])


async def _assign_tenant_admin(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    admin_user_id: uuid.UUID,
    skip_if_user_id: uuid.UUID | None = None,
) -> User:
    """Ensure an active user is a TENANT_ADMIN member of the tenant.

    Creates membership or upgrades an existing non-super_admin membership.
    Platform super_admin memberships are left untouched.
    """
    admin_user = (
        await db.execute(select(User).where(User.id == admin_user_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if not admin_user:
        raise HTTPException(status_code=404, detail="Tenant admin user not found or inactive")

    if skip_if_user_id and admin_user.id == skip_if_user_id:
        return admin_user

    existing = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == admin_user.id,
            )
        )
    ).scalar_one_or_none()

    if existing:
        if existing.role != UserRole.SUPER_ADMIN:
            existing.role = UserRole.TENANT_ADMIN
            existing.is_active = True
            db.add(existing)
    else:
        db.add(
            TenantUser(
                tenant_id=tenant_id,
                user_id=admin_user.id,
                role=UserRole.TENANT_ADMIN,
            )
        )
    return admin_user


@router.get("", response_model=list[TenantResponse])
async def list_tenants(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
):
    """List all tenants (superadmin: all, user: only their own)."""
    query = select(Tenant)

    scoped = get_api_key_tenant_scope(current_user)
    if scoped is not None:
        query = query.where(Tenant.id == scoped)
    elif not current_user.is_superadmin:
        tenant_ids = [m.tenant_id for m in current_user.tenant_memberships if m.is_active]
        query = query.where(Tenant.id.in_(tenant_ids))

    query = query.where(Tenant.is_active.is_(True)).offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new tenant (superadmin only)."""
    # Check uniqueness
    existing = await db.execute(
        select(Tenant).where((Tenant.name == body.name) | (Tenant.slug == body.slug))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant name or slug already taken",
        )

    data = body.model_dump()
    admin_user_id = data.pop("admin_user_id", None)
    public_ip_allocation_ids = data.pop("public_ip_allocation_ids", None)
    tenant = Tenant(**data)
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    # Every tenant needs at least one project so VM create works immediately
    db.add(
        Project(
            tenant_id=tenant.id,
            name="Default",
            description="Default project",
        )
    )
    # Keep creator attached so tenant shows up in membership-scoped views
    db.add(
        TenantUser(
            tenant_id=tenant.id,
            user_id=current_user.id,
            role=UserRole.SUPER_ADMIN,
        )
    )

    admin_user: User | None = None
    if admin_user_id:
        admin_user = await _assign_tenant_admin(
            db,
            tenant_id=tenant.id,
            admin_user_id=admin_user_id,
            skip_if_user_id=current_user.id,
        )

    await db.flush()

    # Public IPs are reserved explicitly; NAT/Internal are created by the tenant later.
    from app.services.network_service import sync_tenant_public_ips

    if public_ip_allocation_ids is not None:
        await sync_tenant_public_ips(db, tenant, public_ip_allocation_ids)

    desc = f"Tenant created: {tenant.name}"
    if admin_user and admin_user.id != current_user.id:
        desc += f" (tenant admin: {admin_user.username})"
    if public_ip_allocation_ids:
        desc += f" (public IPs: {len(public_ip_allocation_ids)})"

    await create_audit_log(
        db,
        action="tenant_created",
        resource_type="tenant",
        resource_id=tenant.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant.id,
        description=desc,
    )

    return tenant


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    enforce_api_key_tenant_scope(current_user, tenant_id)

    # Check access
    if not current_user.is_superadmin:
        has_access = any(
            m.tenant_id == tenant_id and m.is_active
            for m in current_user.tenant_memberships
        )
        if not has_access:
            raise HTTPException(status_code=403, detail="No access to this tenant")

    return tenant


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update tenant settings (superadmin only)."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = body.model_dump(exclude_unset=True)
    admin_user_id = update_data.pop("admin_user_id", None)
    # Must detect presence via dump keys — model_fields_set is unreliable across pydantic versions
    sync_public_ips = "public_ip_allocation_ids" in update_data
    public_ip_allocation_ids = update_data.pop("public_ip_allocation_ids", None)

    for field, value in update_data.items():
        setattr(tenant, field, value)

    db.add(tenant)

    admin_user: User | None = None
    # Only assign when explicitly sent (frontend omits when empty on edit).
    if "admin_user_id" in body.model_dump(exclude_unset=True) and admin_user_id is not None:
        admin_user = await _assign_tenant_admin(
            db,
            tenant_id=tenant.id,
            admin_user_id=admin_user_id,
            skip_if_user_id=current_user.id,
        )

    if sync_public_ips:
        from app.services.network_service import sync_tenant_public_ips

        await sync_tenant_public_ips(db, tenant, public_ip_allocation_ids or [])

    await db.flush()
    await db.refresh(tenant)

    meta = {**update_data}
    desc = f"Tenant updated: {tenant.name}"
    if admin_user:
        meta["admin_user_id"] = str(admin_user.id)
        desc += f" (tenant admin: {admin_user.username})"
    if sync_public_ips:
        meta["public_ip_count"] = len(public_ip_allocation_ids or [])

    await create_audit_log(
        db,
        action="tenant_updated",
        resource_type="tenant",
        resource_id=tenant.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant.id,
        description=desc,
        metadata=meta,
    )

    return tenant


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Soft-delete a tenant (superadmin only)."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.is_active = False
    db.add(tenant)

    # Soft-deleted tenants must not keep assignable "Default" projects visible
    projects = (
        await db.execute(select(Project).where(Project.tenant_id == tenant_id, Project.is_active.is_(True)))
    ).scalars().all()
    for project in projects:
        project.is_active = False
        db.add(project)

    await create_audit_log(
        db,
        action="tenant_deleted",
        resource_type="tenant",
        resource_id=tenant.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant.id,
        description=f"Tenant deactivated: {tenant.name}",
    )


# --- Notification email (tenant admin + superadmin) ---


@router.patch("/{tenant_id}/notification-email", response_model=TenantResponse)
async def update_tenant_notification_email(
    tenant_id: uuid.UUID,
    body: TenantNotificationEmailUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Set the tenant notification inbox (tenant admin or superadmin)."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.notification_email = body.notification_email
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    await create_audit_log(
        db,
        action="tenant_notification_email_updated",
        resource_type="tenant",
        resource_id=tenant.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant.id,
        description=f"Tenant notification email updated: {tenant.name}",
        metadata={"notification_email": tenant.notification_email},
    )
    return tenant


# --- Public IP assignment (per tenant) ---


@router.get("/{tenant_id}/public-ips")
async def list_tenant_public_ips_endpoint(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List public IPs assigned to this tenant (reserved or on VMs)."""
    enforce_api_key_tenant_scope(current_user, tenant_id)
    if not current_user.is_superadmin:
        has_access = any(
            m.tenant_id == tenant_id and m.is_active
            for m in (current_user.tenant_memberships or [])
        )
        if not has_access:
            raise HTTPException(status_code=403, detail="No access to this tenant")

    from app.services.network_service import list_tenant_public_ips

    allocs = await list_tenant_public_ips(db, tenant_id)
    return [
        {
            "id": str(a.id),
            "pool_id": str(a.pool_id),
            "ip_address": str(a.ip_address.ip) if hasattr(a.ip_address, "ip") else str(a.ip_address),
            "status": a.status.value if hasattr(a.status, "value") else str(a.status),
            "vm_id": str(a.vm_id) if a.vm_id else None,
        }
        for a in allocs
    ]


# --- Tenant User Management ---


@router.get("/{tenant_id}/users", response_model=list[TenantMemberResponse])
async def list_tenant_users(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List tenant memberships (superadmin only)."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    rows = (
        await db.execute(
            select(TenantUser, User)
            .join(User, User.id == TenantUser.user_id)
            .where(TenantUser.tenant_id == tenant_id)
            .order_by(TenantUser.created_at.asc())
        )
    ).all()

    return [
        TenantMemberResponse(
            user_id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=membership.role.value if hasattr(membership.role, "value") else str(membership.role),
            is_active=membership.is_active,
        )
        for membership, user in rows
    ]


@router.post("/{tenant_id}/users", status_code=status.HTTP_201_CREATED)
async def add_user_to_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    role: UserRole = UserRole.USER,
):
    """Add a user to a tenant with a specific role."""
    # Verify tenant and user exist
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already member
    existing = await db.execute(
        select(TenantUser).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member of this tenant")

    membership = TenantUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
    )
    db.add(membership)

    await create_audit_log(
        db,
        action="tenant_user_added",
        resource_type="tenant_user",
        resource_id=user_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"User {user.username} added to tenant {tenant.name} (role: {role.value})",
    )

    return {"status": "ok", "message": f"User added with role {role.value}"}
