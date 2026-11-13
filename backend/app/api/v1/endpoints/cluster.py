"""
Cluster management endpoints.
Allows the local QEMU Interface to act as a cluster master by
connecting to remote QEMU Interface instances via their API.
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.cluster import ClusterNode, NodeStatus
from app.models.user import User
from app.schemas.cluster import (
    ClusterNodeCreate,
    ClusterNodeResponse,
    ClusterNodeUpdate,
    ClusterOverview,
    ClusterVMItem,
)

router = APIRouter(prefix="/cluster", tags=["Cluster"])


def _node_response(node: ClusterNode) -> ClusterNodeResponse:
    key = node.api_key or ""
    return ClusterNodeResponse(
        id=node.id,
        name=node.name,
        description=node.description,
        url=node.url,
        api_key_prefix=key[:8] + "..." if len(key) > 8 else "***",
        status=node.status.value if isinstance(node.status, NodeStatus) else node.status,
        is_active=node.is_active,
        last_seen_at=node.last_seen_at,
        last_error=node.last_error,
        total_vms=node.total_vms,
        running_vms=node.running_vms,
        total_vcpus=node.total_vcpus,
        total_ram_mb=node.total_ram_mb,
        used_vcpus=node.used_vcpus,
        used_ram_mb=node.used_ram_mb,
        total_storage_gb=node.total_storage_gb,
        used_storage_gb=node.used_storage_gb,
        app_version=node.app_version,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


async def _fetch_node_data(node: ClusterNode) -> dict:
    """Fetch system info and VM list from a remote node."""
    headers = {"X-API-Key": node.api_key}
    base = node.url.rstrip("/")

    async with httpx.AsyncClient(headers=headers, verify=False, timeout=15.0) as client:
        debug_resp = await client.get(f"{base}/api/v1/system/debug")
        debug_resp.raise_for_status()
        debug_data = debug_resp.json()

        tenants_resp = await client.get(f"{base}/api/v1/tenants")
        tenants_resp.raise_for_status()
        tenants = tenants_resp.json()

        all_vms = []
        for tenant in tenants:
            tid = tenant.get("id", "")
            try:
                vm_resp = await client.get(
                    f"{base}/api/v1/tenants/{tid}/vms",
                    params={"page_size": 500},
                )
                vm_resp.raise_for_status()
                vm_data = vm_resp.json()
                items = vm_data.get("items", []) if isinstance(vm_data, dict) else vm_data
                all_vms.extend(items)
            except Exception:
                continue

        pools_resp = await client.get(f"{base}/api/v1/storage-pools")
        pools_resp.raise_for_status()
        pools = pools_resp.json()

    return {
        "debug": debug_data,
        "vms": all_vms,
        "pools": pools if isinstance(pools, list) else [],
        "tenants": tenants if isinstance(tenants, list) else [],
    }


async def _sync_node(db: AsyncSession, node: ClusterNode) -> ClusterNode:
    """Fetch live data from a node and update cached fields."""
    try:
        data = await _fetch_node_data(node)
        vms = data.get("vms", [])
        pools = data.get("pools", [])
        debug = data.get("debug", {})

        running = sum(1 for v in vms if v.get("status") == "running")
        used_vcpus = sum(v.get("vcpus", 0) for v in vms if v.get("status") != "deleted")
        used_ram = sum(v.get("ram_mb", 0) for v in vms if v.get("status") != "deleted")
        total_storage = sum(p.get("total_size_gb", 0) for p in pools)
        used_storage = sum(p.get("used_size_gb", 0) for p in pools)

        host = debug.get("host_agent", {})
        total_vcpus = host.get("cpu_count", 0) or 0
        total_ram_mb = int((host.get("total_memory_gb", 0) or 0) * 1024)

        node.status = NodeStatus.ONLINE
        node.last_seen_at = datetime.now(timezone.utc)
        node.last_error = None
        node.total_vms = len([v for v in vms if v.get("status") != "deleted"])
        node.running_vms = running
        node.total_vcpus = total_vcpus
        node.total_ram_mb = total_ram_mb
        node.used_vcpus = used_vcpus
        node.used_ram_mb = used_ram
        node.total_storage_gb = total_storage
        node.used_storage_gb = used_storage
        node.app_version = debug.get("app_version")
        node.cached_data = data

    except Exception as e:
        node.status = NodeStatus.OFFLINE
        node.last_error = str(e)[:500]
        node.last_seen_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(node)
    return node


# --- CRUD ---

@router.get("/nodes", response_model=list[ClusterNodeResponse])
async def list_nodes(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")
    result = await db.execute(
        select(ClusterNode).order_by(ClusterNode.name)
    )
    return [_node_response(n) for n in result.scalars().all()]


@router.post("/nodes", response_model=ClusterNodeResponse, status_code=status.HTTP_201_CREATED)
async def add_node(
    body: ClusterNodeCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    existing = (await db.execute(
        select(ClusterNode).where(ClusterNode.name == body.name)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Node '{body.name}' already exists")

    node = ClusterNode(
        name=body.name.strip(),
        description=body.description,
        url=body.url.rstrip("/"),
        api_key=body.api_key,
        status=NodeStatus.UNKNOWN,
        is_active=True,
    )
    db.add(node)
    await db.flush()

    # Immediately try to connect
    node = await _sync_node(db, node)

    return _node_response(node)


@router.patch("/nodes/{node_id}", response_model=ClusterNodeResponse)
async def update_node(
    node_id: uuid.UUID,
    body: ClusterNodeUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    node = (await db.execute(
        select(ClusterNode).where(ClusterNode.id == node_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "url" and value:
            value = value.rstrip("/")
        setattr(node, key, value)

    await db.flush()
    await db.refresh(node)
    return _node_response(node)


@router.delete("/nodes/{node_id}")
async def remove_node(
    node_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    node = (await db.execute(
        select(ClusterNode).where(ClusterNode.id == node_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    name = node.name
    await db.delete(node)
    await db.flush()
    return {"status": "success", "message": f"Node '{name}' removed"}


# --- Sync & Overview ---

@router.post("/nodes/{node_id}/sync", response_model=ClusterNodeResponse)
async def sync_node(
    node_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Manually trigger a sync for a specific node."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    node = (await db.execute(
        select(ClusterNode).where(ClusterNode.id == node_id)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    node = await _sync_node(db, node)
    return _node_response(node)


@router.post("/sync-all")
async def sync_all_nodes(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Sync all active nodes."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(
        select(ClusterNode).where(ClusterNode.is_active.is_(True))
    )
    nodes = list(result.scalars().all())

    synced = 0
    errors = 0
    for node in nodes:
        await _sync_node(db, node)
        if node.status == NodeStatus.ONLINE:
            synced += 1
        else:
            errors += 1

    return {"synced": synced, "errors": errors, "total": len(nodes)}


@router.get("/overview", response_model=ClusterOverview)
async def cluster_overview(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Aggregated overview of all cluster nodes using cached data."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(
        select(ClusterNode).where(ClusterNode.is_active.is_(True)).order_by(ClusterNode.name)
    )
    nodes = list(result.scalars().all())

    total_vms = 0
    running_vms = 0
    total_vcpus = 0
    used_vcpus = 0
    total_ram = 0
    used_ram = 0
    total_storage = 0
    used_storage = 0
    online = 0
    all_vms: list[ClusterVMItem] = []

    for node in nodes:
        total_vms += node.total_vms
        running_vms += node.running_vms
        total_vcpus += node.total_vcpus
        used_vcpus += node.used_vcpus
        total_ram += node.total_ram_mb
        used_ram += node.used_ram_mb
        total_storage += node.total_storage_gb
        used_storage += node.used_storage_gb
        if node.status == NodeStatus.ONLINE:
            online += 1

        # Extract VMs from cached data
        cached = node.cached_data or {}
        for vm in cached.get("vms", []):
            if vm.get("status") == "deleted":
                continue
            all_vms.append(ClusterVMItem(
                id=vm.get("id", ""),
                name=vm.get("name", ""),
                hostname=vm.get("hostname", ""),
                status=vm.get("status", "unknown"),
                vcpus=vm.get("vcpus", 0),
                ram_mb=vm.get("ram_mb", 0),
                disk_size_gb=vm.get("disk_size_gb", 0),
                node_name=node.name,
                node_id=str(node.id),
            ))

    return ClusterOverview(
        total_nodes=len(nodes),
        online_nodes=online,
        total_vms=total_vms,
        running_vms=running_vms,
        total_vcpus=total_vcpus,
        used_vcpus=used_vcpus,
        total_ram_mb=total_ram,
        used_ram_mb=used_ram,
        total_storage_gb=total_storage,
        used_storage_gb=used_storage,
        nodes=[_node_response(n) for n in nodes],
        vms=all_vms,
    )
