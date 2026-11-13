"""
Cluster health check worker – periodically syncs all active nodes.
"""

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.cluster import ClusterNode, NodeStatus
from app.workers.celery_app import celery_app


def _get_sync_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "postgresql://" in sync_url and "+psycopg2" not in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(sync_url)
    return SyncSession(engine)


def _sync_node_sync(session: Session, node: ClusterNode) -> None:
    """Synchronously fetch data from a remote node and update cached fields."""
    headers = {"X-API-Key": node.api_key}
    base = node.url.rstrip("/")

    try:
        with httpx.Client(headers=headers, verify=False, timeout=15.0) as client:
            debug_resp = client.get(f"{base}/api/v1/system/debug")
            debug_resp.raise_for_status()
            debug_data = debug_resp.json()

            tenants_resp = client.get(f"{base}/api/v1/tenants")
            tenants_resp.raise_for_status()
            tenants = tenants_resp.json()

            all_vms = []
            for tenant in (tenants if isinstance(tenants, list) else []):
                tid = tenant.get("id", "")
                try:
                    vm_resp = client.get(
                        f"{base}/api/v1/tenants/{tid}/vms",
                        params={"page_size": 500},
                    )
                    vm_resp.raise_for_status()
                    vm_data = vm_resp.json()
                    items = vm_data.get("items", []) if isinstance(vm_data, dict) else vm_data
                    all_vms.extend(items)
                except Exception:
                    continue

            pools_resp = client.get(f"{base}/api/v1/storage-pools")
            pools_resp.raise_for_status()
            pools = pools_resp.json() if isinstance(pools_resp.json(), list) else []

        host = debug_data.get("host_agent", {})
        running = sum(1 for v in all_vms if v.get("status") == "running")
        active_vms = [v for v in all_vms if v.get("status") != "deleted"]

        node.status = NodeStatus.ONLINE
        node.last_seen_at = datetime.now(timezone.utc)
        node.last_error = None
        node.total_vms = len(active_vms)
        node.running_vms = running
        node.total_vcpus = host.get("cpu_count", 0) or 0
        node.total_ram_mb = int((host.get("total_memory_gb", 0) or 0) * 1024)
        node.used_vcpus = sum(v.get("vcpus", 0) for v in active_vms)
        node.used_ram_mb = sum(v.get("ram_mb", 0) for v in active_vms)
        node.total_storage_gb = sum(p.get("total_size_gb", 0) for p in pools)
        node.used_storage_gb = sum(p.get("used_size_gb", 0) for p in pools)
        node.app_version = debug_data.get("app_version")
        node.cached_data = {"debug": debug_data, "vms": all_vms, "pools": pools, "tenants": tenants}

    except Exception as e:
        node.status = NodeStatus.OFFLINE
        node.last_error = str(e)[:500]
        node.last_seen_at = datetime.now(timezone.utc)


@celery_app.task(name="cluster.health_check")
def cluster_health_check():
    """Periodic task: sync all active cluster nodes."""
    session = _get_sync_session()
    try:
        nodes = session.execute(
            select(ClusterNode).where(ClusterNode.is_active.is_(True))
        ).scalars().all()

        online = 0
        offline = 0
        for node in nodes:
            _sync_node_sync(session, node)
            if node.status == NodeStatus.ONLINE:
                online += 1
            else:
                offline += 1

        session.commit()
        return {"online": online, "offline": offline, "total": len(nodes)}

    except Exception as e:
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()
