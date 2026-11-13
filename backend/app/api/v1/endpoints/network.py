"""
IP Address Management (IPAM) and Port Forwarding endpoints.
"""

import uuid
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_superadmin, get_current_user
from app.models.network import AllocationStatus, IPAllocation, IPPool, PoolType, PortForward
from app.models.tenant import Project, ProjectUser, Tenant
from app.models.vm import VirtualMachine
from app.models.user import User, UserRole
from app.schemas.network import (
    IPAllocationCreate,
    IPAllocationResponse,
    IPPoolCreate,
    IPPoolResponse,
    IPPoolUpdate,
    IPPoolWithStats,
    PortForwardCreate,
    PortForwardResponse,
)
from app.services.audit_service import create_audit_log
from app.services.network_service import create_libvirt_and_pool, resolve_dhcp_settings

router = APIRouter(prefix="/ip-pools", tags=["IPAM"])

# Host Agent connection helper
HOST_AGENT_URL = settings.HOST_AGENT_URL
HOST_AGENT_TOKEN = settings.HOST_AGENT_TOKEN


def _agent_headers() -> dict:
    return {"Authorization": f"Bearer {HOST_AGENT_TOKEN}"}


# --- IP Pools ---


@router.get("/public/available")
async def list_available_public_ips(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Globally free public IPs that can be assigned to a tenant."""
    from app.services.network_service import list_unassigned_public_ips

    allocs = await list_unassigned_public_ips(db)
    return [
        {
            "id": str(a.id),
            "pool_id": str(a.pool_id),
            "ip_address": str(a.ip_address.ip) if hasattr(a.ip_address, "ip") else str(a.ip_address),
            "status": a.status.value if hasattr(a.status, "value") else str(a.status),
        }
        for a in allocs
    ]


@router.get("", response_model=list[IPPoolWithStats])
async def list_ip_pools(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[Optional[uuid.UUID], Query()] = None,
):
    """
    List IP pools with allocation statistics.

    Tenant-scoped view (tenant_id set, or non-superadmin):
    - Own NAT/Internal pools
    - EXTERNAL pools only if the tenant has assigned public IPs
    - Shared NAT/Internal are hidden from tenants

    Superadmin without tenant_id: all pools (global admin IPAM).
    """
    from app.services.network_service import pool_visible_to_tenant

    query = select(IPPool).order_by(IPPool.name)
    membership_ids = [
        m.tenant_id for m in (current_user.tenant_memberships or []) if m.is_active
    ]

    scope_tenant_ids: list[uuid.UUID] = []
    if tenant_id is not None:
        if not current_user.is_superadmin and tenant_id not in membership_ids:
            raise HTTPException(status_code=403, detail="No access to this tenant")
        scope_tenant_ids = [tenant_id]
    elif current_user.is_superadmin:
        scope_tenant_ids = []  # global admin view
    else:
        scope_tenant_ids = membership_ids
        query = query.where(IPPool.is_active.is_(True))

    result = await db.execute(query)
    pools = list(result.scalars().all())

    pool_stats = []
    for pool in pools:
        # Tenant-scoped visibility
        if scope_tenant_ids:
            visible = False
            for tid in scope_tenant_ids:
                if await pool_visible_to_tenant(db, pool, tid):
                    visible = True
                    break
            if not visible:
                continue

            # Stats relative to first matching tenant (VM create uses one tenant)
            tid = scope_tenant_ids[0]
            if pool.pool_type == PoolType.EXTERNAL:
                assigned = await db.execute(
                    select(
                        func.count(IPAllocation.id).label("total"),
                        func.count(IPAllocation.id)
                        .filter(IPAllocation.status == AllocationStatus.ALLOCATED)
                        .label("allocated"),
                        func.count(IPAllocation.id)
                        .filter(IPAllocation.status == AllocationStatus.RESERVED)
                        .label("reserved"),
                    ).where(
                        IPAllocation.pool_id == pool.id,
                        IPAllocation.tenant_id == tid,
                    )
                )
                row = assigned.one()
                pool_data = IPPoolWithStats.model_validate(pool)
                pool_data.total_ips = int(row.total or 0)
                pool_data.allocated_ips = int(row.allocated or 0)
                pool_data.available_ips = int(row.reserved or 0)
                # Do not leak public CIDR / gateway / bridge to tenants
                pool_data.network = ""
                pool_data.gateway = ""
                pool_data.bridge = ""
                pool_data.vlan_id = None
                pool_stats.append(pool_data)
                continue

        alloc_result = await db.execute(
            select(
                func.count(IPAllocation.id).label("total"),
                func.count(IPAllocation.id).filter(
                    IPAllocation.status == AllocationStatus.ALLOCATED
                ).label("allocated"),
            ).where(IPAllocation.pool_id == pool.id)
        )
        row = alloc_result.one()
        pool_data = IPPoolWithStats.model_validate(pool)
        pool_data.total_ips = row.total
        pool_data.allocated_ips = row.allocated
        pool_data.available_ips = row.total - row.allocated
        pool_stats.append(pool_data)

    return pool_stats


@router.post("", response_model=IPPoolResponse, status_code=status.HTTP_201_CREATED)
async def create_ip_pool(
    body: IPPoolCreate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a platform public IP pool (external only). NAT/Internal: tenant Networks API."""
    if body.pool_type != PoolType.EXTERNAL:
        raise HTTPException(
            status_code=400,
            detail="Admin IP pools are public/external only. Tenants create NAT/Internal under Networks.",
        )
    if body.tenant_id is not None:
        raise HTTPException(
            status_code=400,
            detail="External/public pools must remain global (no tenant_id)",
        )

    pool = await create_libvirt_and_pool(
        db,
        name=body.name,
        description=body.description,
        pool_type=body.pool_type,
        network=body.network,
        gateway=body.gateway,
        dns_primary=body.dns_primary,
        dns_secondary=body.dns_secondary,
        vlan_id=body.vlan_id,
        bridge=body.bridge or "",
        internal_dhcp_enabled=body.internal_dhcp_enabled,
        dhcp_start=body.dhcp_start,
        dhcp_end=body.dhcp_end,
        tenant_id=body.tenant_id,
        ip_version=body.ip_version,
    )

    await create_audit_log(
        db,
        action="ip_pool_created",
        resource_type="ip_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=body.tenant_id,
        description=f"Network created: {pool.name} ({body.network}, Type: {body.pool_type.value})",
        metadata={"pool_type": body.pool_type.value, "tenant_id": str(body.tenant_id) if body.tenant_id else None},
    )

    return pool


@router.get("/{pool_id}", response_model=IPPoolResponse)
async def get_ip_pool(
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific IP pool."""
    result = await db.execute(select(IPPool).where(IPPool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="IP pool not found")
    return pool


@router.patch("/{pool_id}", response_model=IPPoolResponse)
async def update_ip_pool(
    pool_id: uuid.UUID,
    body: IPPoolUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update an IP pool (metadata + DHCP settings for NAT/INTERNAL)."""
    result = await db.execute(select(IPPool).where(IPPool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="IP pool not found")

    update_data = body.model_dump(exclude_unset=True)

    # EXTERNAL: bridge may be updated; DHCP fields are ignored
    if pool.pool_type == PoolType.EXTERNAL:
        update_data.pop("internal_dhcp_enabled", None)
        update_data.pop("dhcp_start", None)
        update_data.pop("dhcp_end", None)
    else:
        update_data.pop("bridge", None)

    dhcp_fields_touched = any(
        key in update_data for key in ("internal_dhcp_enabled", "dhcp_start", "dhcp_end")
    )
    if pool.pool_type in (PoolType.NAT, PoolType.INTERNAL) and dhcp_fields_touched:
        next_enabled = update_data.get("internal_dhcp_enabled", pool.internal_dhcp_enabled)
        next_start = update_data.get("dhcp_start", pool.dhcp_start)
        next_end = update_data.get("dhcp_end", pool.dhcp_end)
        # Explicit null clears custom range → auto-default
        if "dhcp_start" in update_data and update_data["dhcp_start"] is None:
            next_start = None
        if "dhcp_end" in update_data and update_data["dhcp_end"] is None:
            next_end = None

        enabled, start, end = resolve_dhcp_settings(
            network_cidr=pool.network,
            gateway=pool.gateway,
            dhcp_enabled=bool(next_enabled),
            dhcp_start=next_start,
            dhcp_end=next_end,
        )
        update_data["internal_dhcp_enabled"] = enabled
        update_data["dhcp_start"] = start
        update_data["dhcp_end"] = end

        if pool.libvirt_network_name:
            mode = "nat" if pool.pool_type == PoolType.NAT else "isolated"
            try:
                async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=45) as client:
                    resp = await client.put(
                        f"{HOST_AGENT_URL}/api/v1/networks/{pool.libvirt_network_name}/dhcp",
                        headers=_agent_headers(),
                        json={
                            "network": pool.network,
                            "gateway": pool.gateway,
                            "mode": mode,
                            "enable_dhcp": enabled,
                            "dhcp_start": start,
                            "dhcp_end": end,
                        },
                    )
                    if resp.status_code != 200:
                        detail = resp.json().get("detail", resp.text) if resp.content else resp.text
                        raise HTTPException(
                            status_code=502,
                            detail=f"Host-Agent DHCP update failed: {detail}",
                        )
            except httpx.RequestError as e:
                raise HTTPException(status_code=502, detail=f"Host-Agent unreachable: {e}")

    for field, value in update_data.items():
        setattr(pool, field, value)

    db.add(pool)
    await db.flush()
    await db.refresh(pool)

    await create_audit_log(
        db,
        action="ip_pool_updated",
        resource_type="ip_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Network updated: {pool.name}",
        metadata={k: (str(v) if v is not None else None) for k, v in update_data.items()},
    )
    return pool


@router.delete("/{pool_id}")
async def delete_ip_pool(
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete an IP pool. For NAT pools, also removes the libvirt network."""
    result = await db.execute(select(IPPool).where(IPPool.id == pool_id))
    pool = result.scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="IP pool not found")

    # Check if any VMs are using IPs from this pool
    allocated = (await db.execute(
        select(func.count(IPAllocation.id)).where(
            IPAllocation.pool_id == pool_id,
            IPAllocation.status == AllocationStatus.ALLOCATED,
        )
    )).scalar()
    if allocated and allocated > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Pool still has {allocated} allocated IPs. Please delete VMs first.",
        )

    # For NAT/INTERNAL pools: delete the libvirt network on the host
    if pool.pool_type in (PoolType.NAT, PoolType.INTERNAL) and pool.libvirt_network_name:
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                await client.delete(
                    f"{HOST_AGENT_URL}/api/v1/networks/{pool.libvirt_network_name}",
                    headers=_agent_headers(),
                )
        except Exception:
            pass  # Best effort

    # Clean up DB dependencies explicitly before deleting the pool.
    # Even with FK ondelete=CASCADE, explicit deletes avoid ORM flush issues.
    pfs = (
        await db.execute(select(PortForward).where(PortForward.pool_id == pool_id))
    ).scalars().all()

    for pf in pfs:
        # Best effort cleanup on host side (iptables DNAT rule)
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
                await client.post(
                    f"{HOST_AGENT_URL}/api/v1/networks/port-forward/remove",
                    headers=_agent_headers(),
                    json={
                        "rule_id": str(pf.id),
                        "protocol": pf.protocol,
                        "external_port": pf.external_port,
                        "internal_ip": pf.internal_ip,
                        "internal_port": pf.internal_port,
                    },
                )
        except Exception:
            pass

    await db.execute(delete(PortForward).where(PortForward.pool_id == pool_id))
    await db.execute(delete(IPAllocation).where(IPAllocation.pool_id == pool_id))

    await db.delete(pool)
    await db.flush()

    await create_audit_log(
        db,
        action="ip_pool_deleted",
        resource_type="ip_pool",
        resource_id=pool_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Network deleted: {pool.name}",
    )

    return {"status": "success", "message": f"Network '{pool.name}' deleted"}


# --- IP Allocations ---


@router.get("/{pool_id}/allocations", response_model=list[IPAllocationResponse])
async def list_allocations(
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    allocation_status: AllocationStatus | None = None,
):
    """List IP allocations in a pool."""
    query = select(IPAllocation).where(IPAllocation.pool_id == pool_id)
    if allocation_status:
        query = query.where(IPAllocation.status == allocation_status)
    query = query.order_by(IPAllocation.ip_address)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/{pool_id}/allocate", response_model=IPAllocationResponse)
async def allocate_ip(
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vm_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
):
    """Allocate the next available IP from a pool."""
    result = await db.execute(
        select(IPAllocation)
        .where(
            IPAllocation.pool_id == pool_id,
            IPAllocation.status == AllocationStatus.AVAILABLE,
        )
        .order_by(IPAllocation.ip_address)
        .limit(1)
    )
    allocation = result.scalar_one_or_none()

    if not allocation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No free IPs in this pool",
        )

    allocation.status = AllocationStatus.ALLOCATED
    allocation.vm_id = vm_id
    allocation.tenant_id = tenant_id
    db.add(allocation)
    await db.flush()
    await db.refresh(allocation)

    return allocation


@router.post("/{pool_id}/release/{allocation_id}", response_model=IPAllocationResponse)
async def release_ip(
    pool_id: uuid.UUID,
    allocation_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Release an allocated IP back to the pool."""
    result = await db.execute(
        select(IPAllocation).where(
            IPAllocation.id == allocation_id,
            IPAllocation.pool_id == pool_id,
        )
    )
    allocation = result.scalar_one_or_none()

    if not allocation:
        raise HTTPException(status_code=404, detail="IP allocation not found")

    allocation.status = AllocationStatus.AVAILABLE
    allocation.vm_id = None
    allocation.tenant_id = None
    db.add(allocation)
    await db.flush()
    await db.refresh(allocation)

    return allocation


# --- Port Forwarding ---


port_forward_router = APIRouter(prefix="/port-forwards", tags=["Port Forwarding"])


def _is_active_tenant_member(user: User, tenant_id: uuid.UUID) -> bool:
    return any(
        m.tenant_id == tenant_id and m.is_active
        for m in (user.tenant_memberships or [])
    )


def _is_tenant_admin(user: User, tenant_id: uuid.UUID) -> bool:
    return any(
        m.tenant_id == tenant_id
        and m.is_active
        and m.role in (UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN)
        for m in (user.tenant_memberships or [])
    )


async def _assert_port_forward_vm_access(
    db: AsyncSession,
    current_user: User,
    vm: VirtualMachine,
) -> None:
    """Enforce tenant membership + VM ownership for port-forward operations."""
    if current_user.is_superadmin:
        return
    if not _is_active_tenant_member(current_user, vm.tenant_id):
        raise HTTPException(status_code=403, detail="No access to this VM")
    if _is_tenant_admin(current_user, vm.tenant_id):
        return

    assigned_project_ids = list(
        (
            await db.execute(
                select(ProjectUser.project_id)
                .join(Project, Project.id == ProjectUser.project_id)
                .where(
                    ProjectUser.user_id == current_user.id,
                    Project.tenant_id == vm.tenant_id,
                    Project.is_active.is_(True),
                )
            )
        ).scalars().all()
    )
    if assigned_project_ids:
        if vm.project_id not in assigned_project_ids and vm.assigned_user_id != current_user.id:
            raise HTTPException(status_code=403, detail="No access to this VM")
    elif vm.created_by != current_user.id and vm.assigned_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="No access to this VM")


@port_forward_router.get("", response_model=list[PortForwardResponse])
async def list_port_forwards(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vm_id: uuid.UUID | None = None,
    pool_id: uuid.UUID | None = None,
):
    """List port forwarding rules visible to the current user."""
    query = (
        select(PortForward)
        .join(VirtualMachine, VirtualMachine.id == PortForward.vm_id)
        .where(VirtualMachine.is_deleted.is_(False))
    )
    if not current_user.is_superadmin:
        tenant_ids = [
            m.tenant_id for m in (current_user.tenant_memberships or []) if m.is_active
        ]
        if not tenant_ids:
            return []
        query = query.where(VirtualMachine.tenant_id.in_(tenant_ids))
    if vm_id:
        query = query.where(PortForward.vm_id == vm_id)
    if pool_id:
        query = query.where(PortForward.pool_id == pool_id)
    query = query.order_by(PortForward.external_port)

    result = await db.execute(query)
    rows = list(result.scalars().all())
    if current_user.is_superadmin:
        return rows

    # Non-admins: drop rules for VMs they cannot access.
    visible: list[PortForward] = []
    for pf in rows:
        vm = (
            await db.execute(
                select(VirtualMachine).where(VirtualMachine.id == pf.vm_id)
            )
        ).scalar_one_or_none()
        if not vm:
            continue
        try:
            await _assert_port_forward_vm_access(db, current_user, vm)
        except HTTPException:
            continue
        visible.append(pf)
    return visible


@port_forward_router.post("", response_model=PortForwardResponse, status_code=status.HTTP_201_CREATED)
async def create_port_forward(
    body: PortForwardCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a port forwarding rule (DNAT). Only works for NAT pools."""
    # Verify VM exists and get its IP
    vm = (await db.execute(
        select(VirtualMachine).where(
            VirtualMachine.id == body.vm_id,
            VirtualMachine.is_deleted.is_(False),
        )
    )).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")
    await _assert_port_forward_vm_access(db, current_user, vm)

    # Get the VM's allocated IP
    alloc = (await db.execute(
        select(IPAllocation).where(
            IPAllocation.vm_id == body.vm_id,
            IPAllocation.status == AllocationStatus.ALLOCATED,
        )
    )).scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=400, detail="VM has no allocated IP")

    ip_str = str(alloc.ip_address) if not hasattr(alloc.ip_address, 'ip') else str(alloc.ip_address.ip)

    # Verify pool is NAT
    pool = (await db.execute(
        select(IPPool).where(IPPool.id == alloc.pool_id)
    )).scalar_one_or_none()
    if not pool or pool.pool_type != PoolType.NAT:
        raise HTTPException(status_code=400, detail="Port forwarding only available for NAT networks")

    # Check for duplicate external port
    existing = (await db.execute(
        select(PortForward).where(
            PortForward.protocol == body.protocol,
            PortForward.external_port == body.external_port,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Port {body.external_port}/{body.protocol} is already in use",
        )

    # Create DB record
    pf = PortForward(
        pool_id=alloc.pool_id,
        vm_id=body.vm_id,
        protocol=body.protocol,
        external_port=body.external_port,
        internal_ip=ip_str,
        internal_port=body.internal_port,
        description=body.description,
    )
    db.add(pf)
    await db.flush()

    # Call host agent to add iptables rule
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{HOST_AGENT_URL}/api/v1/networks/port-forward/add",
                headers=_agent_headers(),
                json={
                    "rule_id": str(pf.id),
                    "protocol": body.protocol,
                    "external_port": body.external_port,
                    "internal_ip": ip_str,
                    "internal_port": body.internal_port,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(resp.text)
    except Exception as e:
        # Rollback DB record if iptables fails
        await db.delete(pf)
        await db.flush()
        raise HTTPException(
            status_code=502,
            detail=f"Port forward on host failed: {e}",
        )

    await db.refresh(pf)

    await create_audit_log(
        db,
        action="port_forward_created",
        resource_type="port_forward",
        resource_id=pf.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Port forward created: {body.protocol.upper()}:{body.external_port} -> {ip_str}:{body.internal_port}",
    )

    return pf


@port_forward_router.delete("/{forward_id}")
async def delete_port_forward(
    forward_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a port forwarding rule."""
    pf = (await db.execute(
        select(PortForward).where(PortForward.id == forward_id)
    )).scalar_one_or_none()
    if not pf:
        raise HTTPException(status_code=404, detail="Port forward not found")

    vm = (await db.execute(
        select(VirtualMachine).where(VirtualMachine.id == pf.vm_id)
    )).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")
    await _assert_port_forward_vm_access(db, current_user, vm)

    # Remove iptables rule on host
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            await client.post(
                f"{HOST_AGENT_URL}/api/v1/networks/port-forward/remove",
                headers=_agent_headers(),
                json={
                    "rule_id": str(pf.id),
                    "protocol": pf.protocol,
                    "external_port": pf.external_port,
                    "internal_ip": pf.internal_ip,
                    "internal_port": pf.internal_port,
                },
            )
    except Exception:
        pass  # Best effort

    await db.delete(pf)
    await db.flush()

    await create_audit_log(
        db,
        action="port_forward_deleted",
        resource_type="port_forward",
        resource_id=forward_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"Port forward deleted: {pf.protocol.upper()}:{pf.external_port}",
    )

    return {"status": "success", "message": "Port forward deleted"}
