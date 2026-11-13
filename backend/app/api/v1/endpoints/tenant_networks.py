"""
Tenant self-service networks (NAT / Internal) + public IP visibility.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import RequireTenantRole
from app.models.network import AllocationStatus, IPAllocation, IPPool, PoolType, PortForward
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.schemas.network import IPPoolWithStats, TenantNetworkCreate, TenantNetworkUpdate
from app.services.audit_service import create_audit_log
from app.services.network_service import (
    create_libvirt_and_pool,
    next_private_cidr,
    resolve_dhcp_settings,
    sanitize_network_slug,
)

router = APIRouter(prefix="/tenants/{tenant_id}/networks", tags=["Tenant Networks"])


async def _pool_stats(
    db: AsyncSession,
    pool: IPPool,
    *,
    tenant_id: uuid.UUID | None = None,
) -> IPPoolWithStats:
    if pool.pool_type == PoolType.EXTERNAL and tenant_id is not None:
        alloc_result = await db.execute(
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
                IPAllocation.tenant_id == tenant_id,
            )
        )
        row = alloc_result.one()
        data = IPPoolWithStats.model_validate(pool)
        data.total_ips = int(row.total or 0)
        data.allocated_ips = int(row.allocated or 0)
        data.available_ips = int(row.reserved or 0)
        return data

    alloc_result = await db.execute(
        select(
            func.count(IPAllocation.id).label("total"),
            func.count(IPAllocation.id)
            .filter(IPAllocation.status == AllocationStatus.ALLOCATED)
            .label("allocated"),
        ).where(IPAllocation.pool_id == pool.id)
    )
    row = alloc_result.one()
    data = IPPoolWithStats.model_validate(pool)
    data.total_ips = row.total
    data.allocated_ips = row.allocated
    data.available_ips = row.total - row.allocated
    return data


@router.get("", response_model=list[IPPoolWithStats])
async def list_tenant_networks(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List tenant NAT/Internal networks only (public IPs are under /public-ips)."""
    result = await db.execute(
        select(IPPool)
        .where(
            IPPool.is_active.is_(True),
            IPPool.tenant_id == tenant_id,
            IPPool.pool_type.in_([PoolType.NAT, PoolType.INTERNAL]),
        )
        .order_by(IPPool.name)
    )
    pools = result.scalars().all()
    return [await _pool_stats(db, p, tenant_id=tenant_id) for p in pools]


@router.post("", response_model=IPPoolWithStats, status_code=status.HTTP_201_CREATED)
async def create_tenant_network(
    tenant_id: uuid.UUID,
    body: TenantNetworkCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a NAT or Internal network (full options; CIDR optional → auto)."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
    ).scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if body.network and body.gateway:
        network, gateway = body.network, body.gateway
    elif body.network or body.gateway:
        raise HTTPException(
            status_code=400,
            detail="Provide both network (CIDR) and gateway, or leave both empty for auto",
        )
    else:
        network, gateway = await next_private_cidr(db)

    safe = sanitize_network_slug(body.name)
    pool_name = f"{tenant.slug}-{safe}"

    pool = await create_libvirt_and_pool(
        db,
        name=pool_name,
        description=body.description or f"{body.pool_type.value} network for {tenant.name}",
        pool_type=body.pool_type,
        network=network,
        gateway=gateway,
        dns_primary=body.dns_primary,
        dns_secondary=body.dns_secondary,
        internal_dhcp_enabled=body.internal_dhcp_enabled,
        dhcp_start=body.dhcp_start,
        dhcp_end=body.dhcp_end,
        tenant_id=tenant.id,
    )

    await create_audit_log(
        db,
        action="tenant_network_created",
        resource_type="ip_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant.id,
        description=f"Tenant network created: {pool.name} ({pool.network}, {pool.pool_type.value})",
        metadata={
            "pool_type": pool.pool_type.value,
            "auto_cidr": not bool(body.network),
        },
    )

    return await _pool_stats(db, pool, tenant_id=tenant_id)


@router.patch("/{pool_id}", response_model=IPPoolWithStats)
async def update_tenant_network(
    tenant_id: uuid.UUID,
    pool_id: uuid.UUID,
    body: TenantNetworkUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a tenant-owned NAT/Internal network (name, DNS, DHCP)."""
    pool = (
        await db.execute(select(IPPool).where(IPPool.id == pool_id))
    ).scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Network not found")
    if pool.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Only tenant-owned networks can be edited here")
    if pool.pool_type not in (PoolType.NAT, PoolType.INTERNAL):
        raise HTTPException(status_code=400, detail="Only NAT/Internal networks can be edited")

    update_data = body.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"]:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        safe = sanitize_network_slug(update_data["name"])
        update_data["name"] = f"{tenant.slug}-{safe}"

    dhcp_fields_touched = any(
        key in update_data for key in ("internal_dhcp_enabled", "dhcp_start", "dhcp_end")
    )
    if dhcp_fields_touched:
        next_enabled = update_data.get("internal_dhcp_enabled", pool.internal_dhcp_enabled)
        next_start = update_data.get("dhcp_start", pool.dhcp_start)
        next_end = update_data.get("dhcp_end", pool.dhcp_end)
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
                        f"{settings.HOST_AGENT_URL}/api/v1/networks/{pool.libvirt_network_name}/dhcp",
                        headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
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
        action="tenant_network_updated",
        resource_type="ip_pool",
        resource_id=pool.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Tenant network updated: {pool.name}",
        metadata={k: (str(v) if v is not None else None) for k, v in update_data.items()},
    )

    return await _pool_stats(db, pool, tenant_id=tenant_id)


@router.delete("/{pool_id}")
async def delete_tenant_network(
    tenant_id: uuid.UUID,
    pool_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a tenant-owned NAT/Internal network (not shared/global pools)."""
    pool = (
        await db.execute(select(IPPool).where(IPPool.id == pool_id))
    ).scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Network not found")
    if pool.tenant_id != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Only tenant-owned networks can be deleted here (not shared/global pools)",
        )
    if pool.pool_type == PoolType.EXTERNAL:
        raise HTTPException(status_code=400, detail="External pools cannot be deleted by tenants")

    allocated = (
        await db.execute(
            select(func.count(IPAllocation.id)).where(
                IPAllocation.pool_id == pool_id,
                IPAllocation.status == AllocationStatus.ALLOCATED,
            )
        )
    ).scalar()
    if allocated and allocated > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Network still has {allocated} allocated IPs. Delete or reassign VMs first.",
        )

    if pool.libvirt_network_name:
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                await client.delete(
                    f"{settings.HOST_AGENT_URL}/api/v1/networks/{pool.libvirt_network_name}",
                    headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
                )
        except Exception:
            pass

    await db.execute(delete(PortForward).where(PortForward.pool_id == pool_id))
    await db.execute(delete(IPAllocation).where(IPAllocation.pool_id == pool_id))
    await db.delete(pool)
    await db.flush()

    await create_audit_log(
        db,
        action="tenant_network_deleted",
        resource_type="ip_pool",
        resource_id=pool_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Tenant network deleted: {pool.name}",
    )

    return {"status": "success", "message": f"Network '{pool.name}' deleted"}
