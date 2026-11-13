"""
Shared IPAM helpers: tenant-scoped pools, auto-CIDR, public-IP quota.
"""

from __future__ import annotations

import ipaddress
import re
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.models.network import AllocationStatus, IPAllocation, IPPool, IPVersion, PoolType
from app.models.tenant import Tenant

HOST_AGENT_URL = settings.HOST_AGENT_URL
HOST_AGENT_TOKEN = settings.HOST_AGENT_TOKEN


def _agent_headers() -> dict:
    return {"Authorization": f"Bearer {HOST_AGENT_TOKEN}"}


def sanitize_network_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:48] or "net"


def resolve_dhcp_settings(
    *,
    network_cidr: str,
    gateway: str,
    dhcp_enabled: bool,
    dhcp_start: str | None,
    dhcp_end: str | None,
) -> tuple[bool, str | None, str | None]:
    """Validate DHCP range for NAT/INTERNAL pools. Returns (enabled, start, end)."""
    if not dhcp_enabled:
        return False, None, None

    try:
        net = ipaddress.ip_network(network_cidr, strict=False)
        gw = ipaddress.ip_address(gateway)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid network/gateway: {e}")

    hosts = [h for h in net.hosts() if h != gw]
    if not hosts:
        raise HTTPException(status_code=400, detail="Network has no usable host addresses for DHCP")

    try:
        start = ipaddress.ip_address(dhcp_start) if dhcp_start else hosts[0]
        end = ipaddress.ip_address(dhcp_end) if dhcp_end else hosts[-1]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid DHCP range: {e}")

    if start not in net or end not in net:
        raise HTTPException(status_code=400, detail="DHCP range must be inside the network CIDR")
    if start == gw or end == gw:
        raise HTTPException(status_code=400, detail="DHCP range must not include the gateway")
    if int(start) > int(end):
        raise HTTPException(status_code=400, detail="dhcp_start must be <= dhcp_end")
    return True, str(start), str(end)


def pool_owned_by_tenant(pool: IPPool, tenant_id: uuid.UUID) -> bool:
    return pool.tenant_id == tenant_id


async def tenant_has_public_ips_in_pool(
    db: AsyncSession, tenant_id: uuid.UUID, pool_id: uuid.UUID
) -> bool:
    """True if tenant has reserved or allocated public IPs in this external pool."""
    result = await db.execute(
        select(func.count(IPAllocation.id)).where(
            IPAllocation.pool_id == pool_id,
            IPAllocation.tenant_id == tenant_id,
            IPAllocation.status.in_(
                [AllocationStatus.RESERVED, AllocationStatus.ALLOCATED]
            ),
        )
    )
    return int(result.scalar() or 0) > 0


async def pool_visible_to_tenant(
    db: AsyncSession, pool: IPPool, tenant_id: uuid.UUID
) -> bool:
    """Tenant-owned NAT/Internal, or EXTERNAL pools with assigned public IPs.

    Shared NAT/Internal (tenant_id NULL) are NOT visible to tenants.
    """
    if pool.tenant_id == tenant_id:
        return True
    if pool.tenant_id is not None:
        return False
    if pool.pool_type == PoolType.EXTERNAL:
        return await tenant_has_public_ips_in_pool(db, tenant_id, pool.id)
    return False


async def count_public_ips_assigned(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Reserved + allocated public IPs for a tenant (their assigned set)."""
    result = await db.execute(
        select(func.count(IPAllocation.id))
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(
            IPAllocation.tenant_id == tenant_id,
            IPAllocation.status.in_(
                [AllocationStatus.RESERVED, AllocationStatus.ALLOCATED]
            ),
            IPPool.pool_type == PoolType.EXTERNAL,
        )
    )
    return int(result.scalar() or 0)


async def count_public_ips_used(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Count allocated public (EXTERNAL) IPs currently on VMs for a tenant."""
    result = await db.execute(
        select(func.count(IPAllocation.id))
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(
            IPAllocation.tenant_id == tenant_id,
            IPAllocation.status == AllocationStatus.ALLOCATED,
            IPPool.pool_type == PoolType.EXTERNAL,
        )
    )
    return int(result.scalar() or 0)


async def count_public_ips_reserved_free(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(IPAllocation.id))
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(
            IPAllocation.tenant_id == tenant_id,
            IPAllocation.status == AllocationStatus.RESERVED,
            IPPool.pool_type == PoolType.EXTERNAL,
        )
    )
    return int(result.scalar() or 0)


async def check_public_ip_quota(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    additional: int = 1,
) -> tuple[bool, str]:
    """VM create needs a free reserved public IP (not just max_ips number)."""
    free = await count_public_ips_reserved_free(db, tenant_id)
    if free < additional:
        assigned = await count_public_ips_assigned(db, tenant_id)
        used = await count_public_ips_used(db, tenant_id)
        return (
            False,
            f"Keine freie zugewiesene Public-IP. "
            f"Zugewiesen: {assigned}, auf VMs: {used}, frei: {free}.",
        )
    return True, "OK"


async def assert_pool_usable_for_tenant(
    db: AsyncSession,
    pool: IPPool,
    tenant_id: uuid.UUID,
) -> None:
    if not pool.is_active:
        raise ValueError("Network/IP pool not found or inactive")
    if not await pool_visible_to_tenant(db, pool, tenant_id):
        raise ValueError("Network/IP pool is not available for this tenant")
    if pool.pool_type == PoolType.EXTERNAL:
        ok, msg = await check_public_ip_quota(db, tenant_id, additional=1)
        if not ok:
            raise ValueError(msg)


async def list_unassigned_public_ips(db: AsyncSession) -> list[IPAllocation]:
    """Globally free public IPs (AVAILABLE, no tenant)."""
    result = await db.execute(
        select(IPAllocation)
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(
            IPPool.pool_type == PoolType.EXTERNAL,
            IPPool.is_active.is_(True),
            IPAllocation.status == AllocationStatus.AVAILABLE,
            IPAllocation.tenant_id.is_(None),
            IPAllocation.vm_id.is_(None),
        )
        .order_by(IPAllocation.ip_address)
    )
    return list(result.scalars().all())


async def list_tenant_public_ips(db: AsyncSession, tenant_id: uuid.UUID) -> list[IPAllocation]:
    result = await db.execute(
        select(IPAllocation)
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(
            IPPool.pool_type == PoolType.EXTERNAL,
            IPAllocation.tenant_id == tenant_id,
            IPAllocation.status.in_(
                [AllocationStatus.RESERVED, AllocationStatus.ALLOCATED]
            ),
        )
        .order_by(IPAllocation.ip_address)
    )
    return list(result.scalars().all())


async def assign_public_ips_to_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    allocation_ids: list[uuid.UUID],
) -> list[IPAllocation]:
    """Reserve specific public IPs for a tenant. Updates tenant.max_ips to assigned count."""
    if not allocation_ids:
        return []

    result = await db.execute(
        select(IPAllocation, IPPool)
        .join(IPPool, IPPool.id == IPAllocation.pool_id)
        .where(IPAllocation.id.in_(allocation_ids))
    )
    rows = result.all()
    if len(rows) != len(set(allocation_ids)):
        raise HTTPException(status_code=404, detail="One or more public IPs not found")

    assigned: list[IPAllocation] = []
    for alloc, pool in rows:
        if pool.pool_type != PoolType.EXTERNAL:
            raise HTTPException(status_code=400, detail=f"{alloc.ip_address} is not a public IP")
        if alloc.tenant_id is not None and alloc.tenant_id != tenant_id:
            raise HTTPException(
                status_code=409,
                detail=f"IP {alloc.ip_address} is already assigned to another tenant",
            )
        if alloc.status == AllocationStatus.ALLOCATED and alloc.tenant_id == tenant_id:
            assigned.append(alloc)
            continue
        if alloc.status != AllocationStatus.AVAILABLE and not (
            alloc.status == AllocationStatus.RESERVED and alloc.tenant_id == tenant_id
        ):
            raise HTTPException(
                status_code=409,
                detail=f"IP {alloc.ip_address} is not available (status={alloc.status.value})",
            )
        alloc.status = AllocationStatus.RESERVED
        alloc.tenant_id = tenant_id
        alloc.vm_id = None
        db.add(alloc)
        assigned.append(alloc)

    await db.flush()
    return assigned


async def sync_tenant_public_ips(
    db: AsyncSession,
    tenant: Tenant,
    desired_allocation_ids: list[uuid.UUID] | None,
) -> None:
    """Set the tenant's public IP set to exactly desired_allocation_ids (None = no change)."""
    if desired_allocation_ids is None:
        return

    desired = set(desired_allocation_ids)
    current = await list_tenant_public_ips(db, tenant.id)
    current_map = {a.id: a for a in current}

    # Release removed (only if not on a VM)
    for alloc_id, alloc in current_map.items():
        if alloc_id in desired:
            continue
        if alloc.status == AllocationStatus.ALLOCATED:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot unassign {alloc.ip_address}: still allocated to a VM",
            )
        alloc.status = AllocationStatus.AVAILABLE
        alloc.tenant_id = None
        alloc.vm_id = None
        db.add(alloc)

    to_add = [i for i in desired if i not in current_map]
    if to_add:
        await assign_public_ips_to_tenant(db, tenant.id, to_add)

    await db.flush()
    # Quota = number of assigned public IPs
    tenant.max_ips = len(desired)
    db.add(tenant)
    await db.flush()


async def pick_ip_for_tenant_pool(
    db: AsyncSession,
    pool: IPPool,
    tenant_id: uuid.UUID,
    *,
    allocation_id: uuid.UUID | None = None,
) -> IPAllocation | None:
    """Next free IP: reserved public for tenant, or next AVAILABLE in tenant NAT/Internal.

    If allocation_id is set (EXTERNAL only), that specific reserved IP is used.
    """
    if allocation_id is not None:
        result = await db.execute(
            select(IPAllocation).where(IPAllocation.id == allocation_id)
        )
        alloc = result.scalar_one_or_none()
        if not alloc:
            raise ValueError("Selected IP allocation not found")
        if alloc.pool_id != pool.id:
            raise ValueError("Selected IP does not belong to the chosen network")
        if pool.pool_type != PoolType.EXTERNAL:
            raise ValueError("Specific IP selection is only supported for public IPs")
        if alloc.tenant_id != tenant_id:
            raise ValueError("Selected IP is not assigned to this tenant")
        if alloc.status != AllocationStatus.RESERVED:
            raise ValueError("Selected IP is not available (must be reserved/free)")
        return alloc

    if pool.pool_type == PoolType.EXTERNAL:
        result = await db.execute(
            select(IPAllocation)
            .where(
                IPAllocation.pool_id == pool.id,
                IPAllocation.tenant_id == tenant_id,
                IPAllocation.status == AllocationStatus.RESERVED,
            )
            .order_by(IPAllocation.ip_address.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    result = await db.execute(
        select(IPAllocation)
        .where(
            IPAllocation.pool_id == pool.id,
            IPAllocation.status == AllocationStatus.AVAILABLE,
        )
        .order_by(IPAllocation.ip_address.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def next_private_cidr(db: AsyncSession) -> tuple[str, str]:
    """Pick next free 10.X.0.0/24 for tenant NAT/Internal (auto)."""
    result = await db.execute(select(IPPool.network))
    used_networks: list[ipaddress.IPv4Network] = []
    for (cidr,) in result.all():
        try:
            used_networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue

    # Prefer 10.20–10.250 to avoid common LAN defaults (10.0 / 10.10).
    for octet in list(range(20, 251)) + list(range(1, 20)) + list(range(251, 255)):
        candidate = ipaddress.ip_network(f"10.{octet}.0.0/24")
        if any(candidate.overlaps(existing) for existing in used_networks):
            continue
        gateway = str(candidate.network_address + 1)
        return str(candidate), gateway

    raise HTTPException(
        status_code=409,
        detail="No free private /24 left for auto-assignment (10.0.0.0/8 exhausted)",
    )


async def create_libvirt_and_pool(
    db: AsyncSession,
    *,
    name: str,
    description: str | None,
    pool_type: PoolType,
    network: str,
    gateway: str,
    dns_primary: str = "8.8.8.8",
    dns_secondary: str | None = "1.1.1.1",
    vlan_id: int | None = None,
    bridge: str = "",
    internal_dhcp_enabled: bool = True,
    dhcp_start: str | None = None,
    dhcp_end: str | None = None,
    tenant_id: uuid.UUID | None = None,
    ip_version: IPVersion = IPVersion.IPV4,
) -> IPPool:
    """Create IP pool + host libvirt network (NAT/Internal) + IP allocations."""
    if pool_type == PoolType.EXTERNAL and tenant_id is not None:
        raise HTTPException(
            status_code=400,
            detail="External/public pools are global and cannot be tenant-scoped",
        )
    if pool_type not in (PoolType.NAT, PoolType.INTERNAL, PoolType.EXTERNAL):
        raise HTTPException(status_code=400, detail="Invalid pool type")

    existing = (
        await db.execute(select(IPPool).where(IPPool.name == name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Network name already taken: {name}")

    try:
        net = ipaddress.ip_network(network, strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid network CIDR notation")

    libvirt_network_name = None
    resolved_bridge = bridge or "br0"

    dhcp_enabled = False
    resolved_start = resolved_end = None
    if pool_type in (PoolType.NAT, PoolType.INTERNAL):
        dhcp_enabled, resolved_start, resolved_end = resolve_dhcp_settings(
            network_cidr=network,
            gateway=gateway,
            dhcp_enabled=internal_dhcp_enabled,
            dhcp_start=dhcp_start,
            dhcp_end=dhcp_end,
        )

    if pool_type == PoolType.INTERNAL:
        net_name = f"qemu-int-{sanitize_network_slug(name)}"
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                resp = await client.post(
                    f"{HOST_AGENT_URL}/api/v1/networks/create-internal",
                    headers=_agent_headers(),
                    json={
                        "name": net_name,
                        "network": network,
                        "gateway": gateway,
                        "enable_dhcp": dhcp_enabled,
                        "dhcp_start": resolved_start,
                        "dhcp_end": resolved_end,
                    },
                )
                if resp.status_code != 200:
                    detail = resp.json().get("detail", resp.text) if resp.content else resp.text
                    raise HTTPException(
                        status_code=502,
                        detail=f"Host-Agent internal network failed: {detail}",
                    )
                resolved_bridge = f"network:{net_name}"
                libvirt_network_name = net_name
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Host-Agent unreachable: {e}")

    if pool_type == PoolType.NAT:
        net_name = f"qemu-nat-{sanitize_network_slug(name)}"
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                resp = await client.post(
                    f"{HOST_AGENT_URL}/api/v1/networks/create-nat",
                    headers=_agent_headers(),
                    json={
                        "name": net_name,
                        "network": network,
                        "gateway": gateway,
                        "enable_dhcp": dhcp_enabled,
                        "dhcp_start": resolved_start,
                        "dhcp_end": resolved_end,
                    },
                )
                if resp.status_code != 200:
                    detail = resp.json().get("detail", resp.text) if resp.content else resp.text
                    raise HTTPException(
                        status_code=502,
                        detail=f"Host-Agent NAT network creation failed: {detail}",
                    )
                resolved_bridge = f"network:{net_name}"
                libvirt_network_name = net_name
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Host-Agent unreachable: {e}")

    pool = IPPool(
        name=name,
        description=description,
        pool_type=pool_type,
        ip_version=ip_version,
        network=network,
        gateway=gateway,
        dns_primary=dns_primary,
        dns_secondary=dns_secondary,
        vlan_id=vlan_id,
        bridge=resolved_bridge,
        internal_dhcp_enabled=(
            dhcp_enabled if pool_type in (PoolType.NAT, PoolType.INTERNAL) else True
        ),
        dhcp_start=resolved_start,
        dhcp_end=resolved_end,
        libvirt_network_name=libvirt_network_name,
        tenant_id=tenant_id,
    )
    db.add(pool)
    await db.flush()

    gateway_ip = ipaddress.ip_address(gateway)
    for host_ip in net.hosts():
        if host_ip == gateway_ip:
            continue
        db.add(
            IPAllocation(
                pool_id=pool.id,
                ip_address=str(host_ip),
                status=AllocationStatus.AVAILABLE,
            )
        )

    await db.flush()
    await db.refresh(pool)
    return pool


def tenant_pool_visibility_filter(tenant_id: uuid.UUID):
    """SQLAlchemy filter: shared pools + pools for this tenant."""
    return or_(IPPool.tenant_id.is_(None), IPPool.tenant_id == tenant_id)
