"""
Load Balancer management endpoints.
"""

import ipaddress
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.load_balancer import LBAlgorithm, LBProtocol, LBTarget, LoadBalancer
from app.models.network import AllocationStatus, IPAllocation, IPPool, PoolType
from app.models.user import TenantUser, User
from app.models.vm import VirtualMachine
from app.schemas.load_balancer import (
    LBCreate,
    LBResponse,
    LBTargetCreate,
    LBTargetResponse,
    LBTargetUpdate,
    LBUpdate,
)
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/load-balancers", tags=["Load Balancer"])


# Classic NAT/private ranges only (not Python's broader is_private, which
# also flags documentation/test nets like 203.0.113.0/24 as private).
_NAT_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
)


def _is_nat_address(ip: str) -> bool:
    try:
        obj = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(obj in net for net in _NAT_NETWORKS)


def _collect_vm_ips(vm: VirtualMachine) -> dict[str, list[str]]:
    """Classify VM cloud-init addresses into nat (RFC1918/CGNAT) vs public."""
    nat: list[str] = []
    public: list[str] = []
    seen: set[str] = set()
    net = vm.cloud_init_network
    if not net or not isinstance(net, dict):
        return {"nat": nat, "public": public}
    ethernets = net.get("ethernets", {}) or {}
    for cfg in ethernets.values():
        if not isinstance(cfg, dict):
            continue
        for addr in cfg.get("addresses", []) or []:
            ip = str(addr).split("/")[0].strip()
            if not ip or ip in seen:
                continue
            seen.add(ip)
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            if _is_nat_address(ip):
                nat.append(ip)
            else:
                public.append(ip)
    return {"nat": nat, "public": public}


def _resolve_target_ip(
    vm: VirtualMachine,
    ip_mode: str,
    preferred_ip: str | None = None,
) -> str | None:
    """
    Resolve the address shown/used for a target.

    nat    → first private IP on the VM
    public → preferred_ip (from public subnet) if set, else first public IP on the VM
    """
    mode = ip_mode if ip_mode in ("nat", "public") else "nat"
    ips = _collect_vm_ips(vm)
    if mode == "nat":
        return (ips.get("nat") or [None])[0]
    if preferred_ip:
        return preferred_ip
    return (ips.get("public") or [None])[0]


def _upstream_for_nginx(
    vm: VirtualMachine,
    ip_mode: str,
    preferred_ip: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Returns (nginx_dial_ip, backend_ip_for_floating).

    nat    → dial NAT IP
    public → dial preferred/public IP; if guest is NAT-only, also pass NAT as
             backend_ip so the host-agent can DNAT floating public → NAT.
    """
    mode = ip_mode if ip_mode in ("nat", "public") else "nat"
    ips = _collect_vm_ips(vm)
    nat = (ips.get("nat") or [None])[0]
    pub_on_vm = (ips.get("public") or [None])[0]
    if mode == "nat":
        return nat, None
    dial = preferred_ip or pub_on_vm
    if not dial:
        return None, None
    if dial in (ips.get("public") or []):
        return dial, None
    # Floating public in front of a NAT guest
    return dial, nat


async def _list_public_pool_ips(db: AsyncSession) -> dict[str, list[str]]:
    """Return free + allocated public IPs from external pools."""
    pools = list((await db.execute(
        select(IPPool).where(
            IPPool.pool_type == PoolType.EXTERNAL,
            IPPool.is_active.is_(True),
        )
    )).scalars().all())
    free: list[str] = []
    allocated: list[str] = []
    for pool in pools:
        allocs = list((await db.execute(
            select(IPAllocation).where(IPAllocation.pool_id == pool.id)
        )).scalars().all())
        for a in allocs:
            ip = str(a.ip_address).split("/")[0]
            if a.status == AllocationStatus.AVAILABLE:
                free.append(ip)
            elif a.status == AllocationStatus.ALLOCATED:
                allocated.append(ip)
    return {"free": free, "allocated": allocated}


async def _allocate_preferred_public_ip(
    db: AsyncSession,
    vm: VirtualMachine,
    preferred_ip: str,
) -> str:
    """Validate/allocate a public IP from an external pool for this VM."""
    ip = preferred_ip.split("/")[0].strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid preferred_ip: {preferred_ip}")

    # Already on the VM → ok
    if ip in (_collect_vm_ips(vm).get("public") or []):
        return ip

    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import INET

    alloc = (await db.execute(
        select(IPAllocation).where(IPAllocation.ip_address == cast(ip, INET))
    )).scalar_one_or_none()
    if not alloc:
        raise HTTPException(
            status_code=400,
            detail=f"Public IP {ip} is not in any IP pool",
        )

    pool = (await db.execute(
        select(IPPool).where(IPPool.id == alloc.pool_id)
    )).scalar_one_or_none()
    if not pool or pool.pool_type != PoolType.EXTERNAL:
        raise HTTPException(
            status_code=400,
            detail=f"IP {ip} is not in a public (external) subnet",
        )

    if alloc.status == AllocationStatus.ALLOCATED and alloc.vm_id and alloc.vm_id != vm.id:
        raise HTTPException(status_code=409, detail=f"Public IP {ip} is already in use")

    if alloc.status == AllocationStatus.AVAILABLE or (
        alloc.status == AllocationStatus.ALLOCATED and alloc.vm_id == vm.id
    ):
        alloc.status = AllocationStatus.ALLOCATED
        alloc.vm_id = vm.id
        alloc.tenant_id = vm.tenant_id
        alloc.description = alloc.description or f"LB public IP for {vm.name}"
        db.add(alloc)
        await db.flush()
        return ip

    raise HTTPException(status_code=400, detail=f"Public IP {ip} is not available")


async def _resolve_or_assign_target_ip(
    db: AsyncSession,
    vm: VirtualMachine,
    ip_mode: str,
    preferred_ip: str | None,
) -> tuple[str, str | None]:
    """
    Returns (display_ip, preferred_ip_stored).
    """
    mode = ip_mode if ip_mode in ("nat", "public") else "nat"
    ips = _collect_vm_ips(vm)
    if mode == "nat":
        nat = (ips.get("nat") or [None])[0]
        if not nat:
            raise HTTPException(
                status_code=400,
                detail=f"VM '{vm.name}' has no NAT IP. Available: public={ips.get('public') or '—'}",
            )
        return nat, None

    # public mode
    chosen = preferred_ip.split("/")[0].strip() if preferred_ip else None
    if not chosen:
        chosen = (ips.get("public") or [None])[0]
    if not chosen:
        free = (await _list_public_pool_ips(db))["free"]
        raise HTTPException(
            status_code=400,
            detail=(
                f"VM '{vm.name}' has no Public IP. "
                f"Pick preferred_ip from the public subnet. Free: {free[:8] or '—'}"
            ),
        )
    stored = await _allocate_preferred_public_ip(db, vm, chosen)
    return stored, stored


async def _release_lb_public_ip(db: AsyncSession, public_ip: str | None) -> None:
    """Free a previous LB VIP back to the external pool (if not on a VM)."""
    if not public_ip:
        return
    ip = public_ip.split("/")[0].strip()
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import INET

    alloc = (await db.execute(
        select(IPAllocation).where(IPAllocation.ip_address == cast(ip, INET))
    )).scalar_one_or_none()
    if not alloc or alloc.vm_id is not None:
        return
    desc = (alloc.description or "")
    if not desc.startswith("LB VIP:"):
        return
    alloc.status = AllocationStatus.AVAILABLE
    alloc.tenant_id = None
    alloc.description = None
    db.add(alloc)
    await db.flush()


async def _reserve_lb_public_ip(
    db: AsyncSession,
    public_ip: str | None,
    *,
    lb_name: str,
    tenant_id: uuid.UUID,
    keep_ip: str | None = None,
) -> str:
    """Reserve one free external-pool IP as the LB VIP (not per backend)."""
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import INET

    if isinstance(public_ip, str):
        public_ip = public_ip.split("/")[0].strip() or None

    if not public_ip:
        free = (await _list_public_pool_ips(db))["free"]
        if keep_ip:
            # Keep existing VIP unless a new one was requested
            public_ip = keep_ip
        elif free:
            public_ip = free[0]
        else:
            raise HTTPException(
                status_code=400,
                detail="No free public IP in external pool for LB VIP",
            )

    ip = public_ip.split("/")[0].strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid public_ip: {public_ip}")

    if keep_ip and ip == keep_ip:
        return ip

    alloc = (await db.execute(
        select(IPAllocation).where(IPAllocation.ip_address == cast(ip, INET))
    )).scalar_one_or_none()
    if not alloc:
        raise HTTPException(status_code=400, detail=f"Public IP {ip} is not in any IP pool")
    pool = (await db.execute(select(IPPool).where(IPPool.id == alloc.pool_id))).scalar_one_or_none()
    if not pool or pool.pool_type != PoolType.EXTERNAL:
        raise HTTPException(status_code=400, detail=f"IP {ip} is not in a public (external) subnet")

    if alloc.status == AllocationStatus.ALLOCATED and alloc.vm_id is not None:
        raise HTTPException(status_code=409, detail=f"Public IP {ip} is already assigned to a VM")
    if (
        alloc.status == AllocationStatus.ALLOCATED
        and alloc.vm_id is None
        and (alloc.description or "").startswith("LB VIP:")
        and not (alloc.description or "").endswith(f":{lb_name}")
        and f"LB VIP:{lb_name}" != (alloc.description or "")
    ):
        raise HTTPException(status_code=409, detail=f"Public IP {ip} is already used by another LB")

    if keep_ip and keep_ip != ip:
        await _release_lb_public_ip(db, keep_ip)

    alloc.status = AllocationStatus.ALLOCATED
    alloc.vm_id = None
    alloc.tenant_id = tenant_id
    alloc.description = f"LB VIP:{lb_name}"
    db.add(alloc)
    await db.flush()
    return ip


async def _build_response(db: AsyncSession, lb: LoadBalancer) -> dict:
    """Build the response dict for a LoadBalancer including its targets."""
    result = await db.execute(
        select(LBTarget).where(LBTarget.load_balancer_id == lb.id)
    )
    targets_raw = list(result.scalars().all())

    targets = []
    for t in targets_raw:
        vm = (await db.execute(
            select(VirtualMachine).where(VirtualMachine.id == t.vm_id)
        )).scalar_one_or_none()
        available = _collect_vm_ips(vm) if vm else {"nat": [], "public": []}
        # Backends always use NAT/internal IP
        vm_ip = (available.get("nat") or available.get("public") or [None])[0]
        targets.append(LBTargetResponse(
            id=t.id,
            vm_id=t.vm_id,
            vm_name=vm.name if vm else None,
            vm_ip=vm_ip,
            ip_mode="nat",
            preferred_ip=None,
            available_ips=available,
            target_port=t.target_port,
            weight=t.weight,
            is_active=t.is_active,
            created_at=t.created_at,
        ))

    return LBResponse(
        id=lb.id,
        tenant_id=lb.tenant_id,
        name=lb.name,
        description=lb.description,
        protocol=lb.protocol.value if isinstance(lb.protocol, LBProtocol) else lb.protocol,
        algorithm=lb.algorithm.value if isinstance(lb.algorithm, LBAlgorithm) else lb.algorithm,
        listen_port=lb.listen_port,
        public_ip=getattr(lb, "public_ip", None),
        health_check_path=lb.health_check_path,
        health_check_interval=lb.health_check_interval,
        is_active=lb.is_active,
        targets=targets,
        created_at=lb.created_at,
        updated_at=lb.updated_at,
    )


async def _sync_lb_to_host_agent(db: AsyncSession):
    """Push all active load-balancer configs to the host agent."""
    result = await db.execute(
        select(LoadBalancer).where(LoadBalancer.is_active.is_(True))
    )
    lbs = list(result.scalars().all())

    payload = []
    for lb in lbs:
        targets_result = await db.execute(
            select(LBTarget).where(
                LBTarget.load_balancer_id == lb.id,
                LBTarget.is_active.is_(True),
            )
        )
        targets_raw = list(targets_result.scalars().all())

        upstreams = []
        for t in targets_raw:
            vm = (await db.execute(
                select(VirtualMachine).where(VirtualMachine.id == t.vm_id)
            )).scalar_one_or_none()
            if not vm:
                continue
            # Always dial NAT/internal backend IPs; one public VIP sits in front
            dial, _backend = _upstream_for_nginx(vm, "nat", None)
            if dial:
                upstreams.append({
                    "ip": dial,
                    "port": t.target_port,
                    "weight": t.weight,
                })

        if not upstreams:
            continue

        payload.append({
            "name": lb.name,
            "protocol": lb.protocol.value if isinstance(lb.protocol, LBProtocol) else lb.protocol,
            "algorithm": lb.algorithm.value if isinstance(lb.algorithm, LBAlgorithm) else lb.algorithm,
            "listen_port": lb.listen_port,
            "public_ip": getattr(lb, "public_ip", None),
            "health_check_path": lb.health_check_path,
            "health_check_interval": lb.health_check_interval,
            "upstreams": upstreams,
        })

    try:
        async with httpx.AsyncClient(
            base_url=settings.HOST_AGENT_URL,
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            verify=AGENT_SSL_VERIFY,
            timeout=30.0,
        ) as client:
            resp = await client.post("/api/v1/lb/apply", json={"load_balancers": payload})
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Host agent LB sync failed: {resp.text}",
                )
            try:
                body = resp.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and body.get("status") not in (None, "success"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Host agent LB sync failed: {body.get('message') or body}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Host agent unreachable: {e}")


@router.get("", response_model=list[LBResponse])
async def list_load_balancers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.is_superadmin:
        result = await db.execute(
            select(LoadBalancer).order_by(LoadBalancer.created_at.desc())
        )
    else:
        memberships = list((await db.execute(
            select(TenantUser).where(TenantUser.user_id == current_user.id)
        )).scalars().all())
        if not memberships:
            return []
        tenant_ids = [m.tenant_id for m in memberships]
        result = await db.execute(
            select(LoadBalancer)
            .where(LoadBalancer.tenant_id.in_(tenant_ids))
            .order_by(LoadBalancer.created_at.desc())
        )

    lbs = list(result.scalars().all())
    return [await _build_response(db, lb) for lb in lbs]


@router.get("/ip-choices")
async def list_ip_choices(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    vm_id: uuid.UUID | None = None,
):
    """NAT IPs of a VM + free public IPs from external subnets."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    public = await _list_public_pool_ips(db)
    nat: list[str] = []
    vm_public: list[str] = []
    if vm_id:
        vm = (await db.execute(
            select(VirtualMachine).where(VirtualMachine.id == vm_id)
        )).scalar_one_or_none()
        if not vm:
            raise HTTPException(status_code=404, detail="VM not found")
        classified = _collect_vm_ips(vm)
        nat = classified["nat"]
        vm_public = classified["public"]

    return {
        "nat": nat,
        "public_on_vm": vm_public,
        "public_free": public["free"],
        "public_choices": list(dict.fromkeys(vm_public + public["free"])),
    }


@router.get("/{lb_id}", response_model=LBResponse)
async def get_load_balancer(
    lb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    lb = (await db.execute(
        select(LoadBalancer).where(LoadBalancer.id == lb_id)
    )).scalar_one_or_none()
    if not lb:
        raise HTTPException(status_code=404, detail="Load balancer not found")
    if not current_user.is_superadmin:
        tenant_ids = [
            m.tenant_id for m in (current_user.tenant_memberships or []) if getattr(m, "is_active", True)
        ]
        if lb.tenant_id not in tenant_ids:
            raise HTTPException(status_code=404, detail="Load balancer not found")
    return await _build_response(db, lb)


@router.post("", response_model=LBResponse, status_code=status.HTTP_201_CREATED)
async def create_load_balancer(
    body: LBCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    # Resolve tenant without scalar_one (admins may have multiple memberships)
    tenant_id = body.tenant_id
    if not tenant_id and body.targets:
        first_vm = (await db.execute(
            select(VirtualMachine).where(VirtualMachine.id == body.targets[0].vm_id)
        )).scalar_one_or_none()
        if first_vm:
            tenant_id = first_vm.tenant_id
    if not tenant_id:
        tu = (await db.execute(
            select(TenantUser)
            .where(TenantUser.user_id == current_user.id)
            .order_by(TenantUser.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        tenant_id = tu.tenant_id if tu else None
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Could not determine tenant")

    vip = await _reserve_lb_public_ip(
        db, body.public_ip, lb_name=body.name.strip(), tenant_id=tenant_id, keep_ip=None
    )

    lb = LoadBalancer(
        tenant_id=tenant_id,
        name=body.name.strip(),
        description=body.description,
        protocol=LBProtocol(body.protocol),
        algorithm=LBAlgorithm(body.algorithm),
        listen_port=body.listen_port,
        public_ip=vip,
        health_check_path=body.health_check_path,
        health_check_interval=body.health_check_interval,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(lb)
    await db.flush()

    for t in body.targets:
        vm = (await db.execute(
            select(VirtualMachine).where(VirtualMachine.id == t.vm_id)
        )).scalar_one_or_none()
        if not vm:
            raise HTTPException(status_code=404, detail=f"VM not found: {t.vm_id}")
        nat_ip = (_collect_vm_ips(vm).get("nat") or [None])[0]
        if not nat_ip:
            raise HTTPException(
                status_code=400,
                detail=f"VM '{vm.name}' has no internal/NAT IP for LB backend",
            )
        target = LBTarget(
            load_balancer_id=lb.id,
            vm_id=t.vm_id,
            ip_mode="nat",
            preferred_ip=None,
            target_port=t.target_port,
            weight=t.weight,
            is_active=True,
        )
        db.add(target)

    await db.flush()
    await db.refresh(lb)

    await _sync_lb_to_host_agent(db)

    await create_audit_log(
        db,
        action="lb_created",
        resource_type="load_balancer",
        resource_id=lb.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Load balancer created: {lb.name} (:{lb.listen_port})",
    )

    return await _build_response(db, lb)


@router.patch("/{lb_id}", response_model=LBResponse)
async def update_load_balancer(
    lb_id: uuid.UUID,
    body: LBUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    lb = (await db.execute(
        select(LoadBalancer).where(LoadBalancer.id == lb_id)
    )).scalar_one_or_none()
    if not lb:
        raise HTTPException(status_code=404, detail="Load balancer not found")

    update_data = body.model_dump(exclude_unset=True)
    if "public_ip" in update_data or not getattr(lb, "public_ip", None):
        update_data["public_ip"] = await _reserve_lb_public_ip(
            db,
            update_data.get("public_ip") if "public_ip" in update_data else lb.public_ip,
            lb_name=update_data.get("name") or lb.name,
            tenant_id=lb.tenant_id,
            keep_ip=getattr(lb, "public_ip", None),
        )
    for key, value in update_data.items():
        if key == "algorithm" and value is not None:
            setattr(lb, key, LBAlgorithm(value))
        else:
            setattr(lb, key, value)

    await db.flush()
    await db.refresh(lb)
    await _sync_lb_to_host_agent(db)

    await create_audit_log(
        db,
        action="lb_updated",
        resource_type="load_balancer",
        resource_id=lb.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=lb.tenant_id,
        description=f"Load balancer updated: {lb.name}",
    )

    return await _build_response(db, lb)


@router.delete("/{lb_id}")
async def delete_load_balancer(
    lb_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    lb = (await db.execute(
        select(LoadBalancer).where(LoadBalancer.id == lb_id)
    )).scalar_one_or_none()
    if not lb:
        raise HTTPException(status_code=404, detail="Load balancer not found")

    name = lb.name
    tenant_id = lb.tenant_id
    vip = getattr(lb, "public_ip", None)

    # Delete targets first
    targets = (await db.execute(
        select(LBTarget).where(LBTarget.load_balancer_id == lb.id)
    )).scalars().all()
    for t in targets:
        await db.delete(t)

    await _release_lb_public_ip(db, vip)
    await db.delete(lb)
    await db.flush()
    await _sync_lb_to_host_agent(db)

    await create_audit_log(
        db,
        action="lb_deleted",
        resource_type="load_balancer",
        resource_id=lb_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Load balancer deleted: {name}",
    )

    return {"status": "success", "message": f"Load balancer '{name}' deleted"}


# --- Target management ---

@router.post("/{lb_id}/targets", response_model=LBTargetResponse, status_code=status.HTTP_201_CREATED)
async def add_target(
    lb_id: uuid.UUID,
    body: LBTargetCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    lb = (await db.execute(
        select(LoadBalancer).where(LoadBalancer.id == lb_id)
    )).scalar_one_or_none()
    if not lb:
        raise HTTPException(status_code=404, detail="Load balancer not found")

    vm = (await db.execute(
        select(VirtualMachine).where(VirtualMachine.id == body.vm_id)
    )).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    existing = (await db.execute(
        select(LBTarget).where(
            LBTarget.load_balancer_id == lb_id,
            LBTarget.vm_id == body.vm_id,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="VM is already a target of this load balancer")

    available = _collect_vm_ips(vm)
    nat_ip = (available.get("nat") or [None])[0]
    if not nat_ip:
        raise HTTPException(
            status_code=400,
            detail=f"VM '{vm.name}' has no internal/NAT IP. LB backends use NAT only.",
        )

    target = LBTarget(
        load_balancer_id=lb_id,
        vm_id=body.vm_id,
        ip_mode="nat",
        preferred_ip=None,
        target_port=body.target_port,
        weight=body.weight,
        is_active=True,
    )
    db.add(target)
    await db.flush()
    await db.refresh(target)
    await _sync_lb_to_host_agent(db)

    return LBTargetResponse(
        id=target.id,
        vm_id=target.vm_id,
        vm_name=vm.name,
        vm_ip=nat_ip,
        ip_mode="nat",
        preferred_ip=None,
        available_ips=available,
        target_port=target.target_port,
        weight=target.weight,
        is_active=target.is_active,
        created_at=target.created_at,
    )


@router.patch("/{lb_id}/targets/{target_id}", response_model=LBTargetResponse)
async def update_target(
    lb_id: uuid.UUID,
    target_id: uuid.UUID,
    body: LBTargetUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    target = (await db.execute(
        select(LBTarget).where(
            LBTarget.id == target_id,
            LBTarget.load_balancer_id == lb_id,
        )
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    vm = (await db.execute(
        select(VirtualMachine).where(VirtualMachine.id == target.vm_id)
    )).scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    data = body.model_dump(exclude_unset=True)
    # Backends are always NAT; ignore ip_mode / preferred_ip from clients.
    target.ip_mode = "nat"
    target.preferred_ip = None
    if "target_port" in data and data["target_port"] is not None:
        target.target_port = data["target_port"]
    if "weight" in data and data["weight"] is not None:
        target.weight = data["weight"]
    if "is_active" in data and data["is_active"] is not None:
        target.is_active = data["is_active"]

    await db.flush()
    await db.refresh(target)
    await _sync_lb_to_host_agent(db)

    available = _collect_vm_ips(vm)
    nat_ip = (available.get("nat") or [None])[0]
    return LBTargetResponse(
        id=target.id,
        vm_id=target.vm_id,
        vm_name=vm.name,
        vm_ip=nat_ip,
        ip_mode="nat",
        preferred_ip=None,
        available_ips=available,
        target_port=target.target_port,
        weight=target.weight,
        is_active=target.is_active,
        created_at=target.created_at,
    )


@router.delete("/{lb_id}/targets/{target_id}")
async def remove_target(
    lb_id: uuid.UUID,
    target_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage load balancers")

    target = (await db.execute(
        select(LBTarget).where(
            LBTarget.id == target_id,
            LBTarget.load_balancer_id == lb_id,
        )
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    await db.delete(target)
    await db.flush()
    await _sync_lb_to_host_agent(db)

    return {"status": "success", "message": "Target removed"}
