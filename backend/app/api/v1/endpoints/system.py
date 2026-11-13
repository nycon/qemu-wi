"""
System diagnostics and debug endpoints (superadmin only).
Provides worker status, queue info, recent task errors, system health,
and host-agent network / libvirt domain management.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_superadmin
from app.models.audit import AuditLog
from app.models.storage import CloudImage
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.models.vm import VMStatus, VirtualMachine
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/system", tags=["System"])

AGENT_BASE_URL = settings.HOST_AGENT_URL
AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}


@router.get("/generate-password")
async def generate_password(
    current_user: Annotated[User, Depends(get_current_user)],
    length: int = 16,
):
    """Generate a secure random password for VM creation."""
    import secrets
    import string

    length = max(8, min(length, 64))
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*_+-="

    # Ensure at least one of each required type
    password = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(special),
    ]
    all_chars = lower + upper + digits + special
    password += [secrets.choice(all_chars) for _ in range(length - 4)]
    # Shuffle to avoid predictable positions
    pw_list = list(password)
    secrets.SystemRandom().shuffle(pw_list)
    return {"password": "".join(pw_list)}


@router.get("/metrics")
async def get_host_metrics(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Proxy host CPU/RAM/disk metrics from the host agent."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            resp = await client.get(f"{AGENT_BASE_URL}/api/v1/metrics", headers=AGENT_HEADERS)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Host agent error: {resp.text}")
            return resp.json()
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Host agent unreachable: {e}") from e


@router.get("/debug")
async def get_debug_info(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Comprehensive system diagnostics for admin debug mode.
    Returns worker health, queue depths, recent errors, and system overview.
    """
    diagnostics = {}

    # --- 1. Database health ---
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        diagnostics["database"] = {"status": "ok", "url": _sanitize_url(settings.DATABASE_URL)}
    except Exception as e:
        diagnostics["database"] = {"status": "error", "error": str(e)}

    # --- 2. Redis / Celery health ---
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.CELERY_BROKER_URL, socket_timeout=3)
        r.ping()
        # Check queue depths
        vm_queue_len = r.llen("vm_operations") or 0
        storage_queue_len = r.llen("storage_operations") or 0
        default_queue_len = r.llen("celery") or 0
        diagnostics["redis"] = {
            "status": "ok",
            "broker_url": _sanitize_url(settings.CELERY_BROKER_URL),
            "queues": {
                "vm_operations": vm_queue_len,
                "storage_operations": storage_queue_len,
                "celery": default_queue_len,
            },
        }
    except Exception as e:
        diagnostics["redis"] = {"status": "error", "error": str(e)}

    # --- 3. Celery worker status ---
    try:
        from app.workers.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        active = inspect.active() or {}
        stats = inspect.stats() or {}
        registered = inspect.registered() or {}

        workers = {}
        for worker_name, worker_stats in stats.items():
            workers[worker_name] = {
                "status": "online",
                "active_tasks": len(active.get(worker_name, [])),
                "pool": worker_stats.get("pool", {}).get("implementation", "unknown"),
                "concurrency": worker_stats.get("pool", {}).get("max-concurrency", "unknown"),
                "registered_tasks": len(registered.get(worker_name, [])),
                "uptime": worker_stats.get("clock", "unknown"),
            }

        if not workers:
            diagnostics["celery_workers"] = {"status": "warning", "message": "No workers online", "workers": {}}
        else:
            diagnostics["celery_workers"] = {"status": "ok", "workers": workers}

    except Exception as e:
        diagnostics["celery_workers"] = {"status": "error", "error": str(e)}

    # --- 4. Recent tasks overview ---
    try:
        now = datetime.now(timezone.utc)
        last_24h = now - timedelta(hours=24)

        # Task counts by status
        status_counts = await db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.created_at >= last_24h)
            .group_by(Task.status)
        )
        task_stats = {row[0].value if hasattr(row[0], 'value') else str(row[0]): row[1] for row in status_counts.all()}

        # Recent failed tasks with error details
        failed_tasks = await db.execute(
            select(Task)
            .where(Task.status == TaskStatus.FAILED)
            .order_by(Task.updated_at.desc())
            .limit(10)
        )
        failed_list = []
        for t in failed_tasks.scalars().all():
            failed_list.append({
                "id": str(t.id),
                "task_type": t.task_type.value if hasattr(t.task_type, 'value') else str(t.task_type),
                "error_message": t.error_message,
                "resource_id": str(t.resource_id) if t.resource_id else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            })

        # Stuck tasks (queued/running for > 10 min)
        stuck_threshold = now - timedelta(minutes=10)
        stuck_tasks = await db.execute(
            select(Task)
            .where(
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                Task.created_at < stuck_threshold,
            )
            .order_by(Task.created_at.asc())
            .limit(10)
        )
        stuck_list = []
        for t in stuck_tasks.scalars().all():
            stuck_list.append({
                "id": str(t.id),
                "task_type": t.task_type.value if hasattr(t.task_type, 'value') else str(t.task_type),
                "status": t.status.value if hasattr(t.status, 'value') else str(t.status),
                "resource_id": str(t.resource_id) if t.resource_id else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "age_minutes": int((now - t.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60) if t.created_at else None,
            })

        diagnostics["tasks_24h"] = {
            "stats": task_stats,
            "recent_failures": failed_list,
            "stuck_tasks": stuck_list,
        }

    except Exception as e:
        diagnostics["tasks_24h"] = {"error": str(e)}

    # --- 5. VM overview ---
    try:
        vm_counts = await db.execute(
            select(VirtualMachine.status, func.count(VirtualMachine.id))
            .where(VirtualMachine.is_deleted.is_(False))
            .group_by(VirtualMachine.status)
        )
        vm_stats = {row[0].value if hasattr(row[0], 'value') else str(row[0]): row[1] for row in vm_counts.all()}
        diagnostics["vms"] = {"counts_by_status": vm_stats}
    except Exception as e:
        diagnostics["vms"] = {"error": str(e)}

    # --- 6. Cloud images overview ---
    try:
        img_counts = await db.execute(
            select(CloudImage.download_status, func.count(CloudImage.id))
            .where(CloudImage.is_active.is_(True))
            .group_by(CloudImage.download_status)
        )
        img_stats = {row[0].value if hasattr(row[0], 'value') else str(row[0]): row[1] for row in img_counts.all()}
        diagnostics["cloud_images"] = {"counts_by_status": img_stats}
    except Exception as e:
        diagnostics["cloud_images"] = {"error": str(e)}

    # --- 7. Config (sanitized) ---
    diagnostics["config"] = {
        "app_name": settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "debug": settings.DEBUG,
        "host_agent_url": settings.HOST_AGENT_URL,
        "cloud_images_path": settings.CLOUD_IMAGES_PATH,
        "default_storage_pool_path": settings.DEFAULT_STORAGE_POOL_PATH,
        "default_bridge": settings.DEFAULT_BRIDGE,
        "jwt_access_token_expire_minutes": settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    }

    return diagnostics


@router.get("/logs")
async def get_system_logs(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str = "agent",
    lines: int = 200,
    since_seconds: int | None = None,
):
    """
    Live log tail for debug mode.
    Sources on host: agent, backend, worker, nginx, firewall, fail2ban, install.
    Source audit: recent audit events from the database.
    """
    lines = max(10, min(lines, 1000))

    if source == "audit":
        result = await db.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(lines)
        )
        entries = list(reversed(result.scalars().all()))
        log_lines = []
        for entry in entries:
            ts = entry.timestamp.isoformat() if entry.timestamp else "-"
            actor = entry.actor_email or "system"
            desc = entry.description or ""
            log_lines.append(
                f"{ts} [{entry.status}] {actor} {entry.action} "
                f"{entry.resource_type}/{entry.resource_id or '-'} {desc}".strip()
            )
        return {
            "source": "audit",
            "lines": log_lines,
            "count": len(log_lines),
            "available_sources": [
                "agent", "backend", "worker", "nginx", "firewall", "fail2ban", "install", "audit",
            ],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    if source == "tasks":
        result = await db.execute(
            select(Task).order_by(Task.updated_at.desc()).limit(lines)
        )
        entries = list(reversed(result.scalars().all()))
        log_lines = []
        for t in entries:
            task_type = t.task_type.value if hasattr(t.task_type, "value") else str(t.task_type)
            status = t.status.value if hasattr(t.status, "value") else str(t.status)
            updated = t.updated_at.isoformat() if t.updated_at else "-"
            err = f" | {t.error_message[:200]}" if t.error_message else ""
            log_lines.append(
                f"{updated} [{status}] {task_type} vm={t.resource_id or '-'}{err}"
            )
        return {
            "source": "tasks",
            "lines": log_lines,
            "count": len(log_lines),
            "available_sources": [
                "agent", "backend", "worker", "nginx", "firewall", "fail2ban", "install", "audit", "tasks",
            ],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        params: dict = {"source": source, "lines": lines}
        if since_seconds is not None:
            params["since_seconds"] = since_seconds
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=25) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/system/logs",
                headers=AGENT_HEADERS,
                params=params,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            data = resp.json()
            sources = list(data.get("available_sources") or [])
            for extra in ("audit", "tasks"):
                if extra not in sources:
                    sources.append(extra)
            data["available_sources"] = sources
            return data
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable (system logs)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/retry-stuck-tasks")
async def retry_stuck_tasks(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Find and reset stuck tasks (queued for > 10 minutes).
    Resets their associated VMs to ERROR state so they can be force-deleted.
    """
    now = datetime.now(timezone.utc)
    stuck_threshold = now - timedelta(minutes=10)

    result = await db.execute(
        select(Task)
        .where(
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
            Task.created_at < stuck_threshold,
        )
    )
    stuck_tasks = result.scalars().all()
    count = 0

    for task in stuck_tasks:
        task.status = TaskStatus.FAILED
        task.error_message = "Automatically marked as failed (stuck > 10 min)"
        task.completed_at = now
        db.add(task)

        # Reset associated VM to error state
        if task.resource_type == "virtual_machine" and task.resource_id:
            vm_result = await db.execute(
                select(VirtualMachine).where(VirtualMachine.id == task.resource_id)
            )
            vm = vm_result.scalar_one_or_none()
            if vm and vm.status not in (VMStatus.RUNNING, VMStatus.STOPPED):
                vm.status = VMStatus.ERROR
                db.add(vm)

        count += 1

    if count > 0:
        await db.commit()

    return {"reset_count": count, "message": f"{count} stuck tasks reset"}


@router.post("/cleanup")
async def cleanup_system(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Comprehensive system cleanup for testing/debug.
    Uses SAVEPOINTs so each step can fail independently without
    aborting the entire PostgreSQL transaction.
    Uses raw SQL for cloud image status to avoid Enum type mismatches.
    """
    now = datetime.now(timezone.utc)
    user_id = current_user.id
    user_email = current_user.email
    report: dict = {
        "vms_reset": 0,
        "vms_purged": 0,
        "tasks_failed": 0,
        "images_reset": 0,
        "errors": [],
    }

    # --- 1. Purge VMs already in error/deleted/deleting state ---
    try:
        async with db.begin_nested():
            from app.models.network import IPAllocation, AllocationStatus

            purge_vms = await db.execute(
                select(VirtualMachine).where(
                    VirtualMachine.is_deleted.is_(False),
                    VirtualMachine.status.in_([VMStatus.ERROR, VMStatus.DELETED, VMStatus.DELETING]),
                )
            )
            for vm in purge_vms.scalars().all():
                ip_allocs = await db.execute(
                    select(IPAllocation).where(IPAllocation.vm_id == vm.id)
                )
                for alloc in ip_allocs.scalars().all():
                    alloc.status = AllocationStatus.AVAILABLE
                    alloc.vm_id = None
                    alloc.tenant_id = None
                    db.add(alloc)

                vm.is_deleted = True
                vm.status = VMStatus.DELETED
                db.add(vm)
                report["vms_purged"] += 1
    except Exception as e:
        report["errors"].append(f"VM-Purge: {e}")

    # --- 2. Reset remaining stuck VMs to ERROR ---
    try:
        async with db.begin_nested():
            stuck_vm_statuses = [
                VMStatus.CREATING, VMStatus.STARTING, VMStatus.STOPPING,
                VMStatus.REBOOTING,
            ]
            stuck_vms = await db.execute(
                select(VirtualMachine).where(
                    VirtualMachine.is_deleted.is_(False),
                    VirtualMachine.status.in_(stuck_vm_statuses),
                )
            )
            for vm in stuck_vms.scalars().all():
                vm.status = VMStatus.ERROR
                db.add(vm)
                report["vms_reset"] += 1
    except Exception as e:
        report["errors"].append(f"VM-Reset: {e}")

    # --- 3. Mark ALL queued/running tasks as failed ---
    try:
        async with db.begin_nested():
            pending_tasks = await db.execute(
                select(Task).where(
                    Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
                )
            )
            for task in pending_tasks.scalars().all():
                task.status = TaskStatus.FAILED
                task.error_message = "System cleanup: manually reset"
                task.completed_at = now
                db.add(task)
                report["tasks_failed"] += 1
    except Exception as e:
        report["errors"].append(f"Task-Reset: {e}")

    # --- 4. Reset stuck cloud image downloads ---
    # Use raw SQL to avoid PostgreSQL ENUM vs VARCHAR type mismatch
    try:
        async with db.begin_nested():
            result = await db.execute(
                text(
                    "UPDATE cloud_images "
                    "SET download_status = 'failed', download_progress = 0 "
                    "WHERE is_active = true "
                    "AND download_status::text IN ('pending', 'downloading', 'extracting')"
                )
            )
            report["images_reset"] = result.rowcount or 0
    except Exception as e:
        report["errors"].append(f"Image-Reset: {e}")

    # --- 5. Cleanup stale libvirt domains on host agent ---
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            response = await client.get(
                f"{AGENT_BASE_URL}/api/v1/vms",
                headers=AGENT_HEADERS,
            )
            if response.status_code == 200:
                host_domains = response.json()
                # Get active domain names from DB
                active_result = await db.execute(
                    select(VirtualMachine.libvirt_domain_name).where(
                        VirtualMachine.is_deleted.is_(False),
                        VirtualMachine.libvirt_domain_name.is_not(None),
                        VirtualMachine.status.in_([VMStatus.RUNNING, VMStatus.STOPPED]),
                    )
                )
                active_names = {row[0] for row in active_result.all()}

                for domain in host_domains:
                    name = domain.get("name", "")
                    if name.startswith("vm-") and name not in active_names:
                        try:
                            await client.delete(
                                f"{AGENT_BASE_URL}/api/v1/vms/{name}",
                                params={"vm_id": "cleanup"},
                                headers=AGENT_HEADERS,
                            )
                        except Exception:
                            pass
    except Exception as e:
        report["errors"].append(f"Libvirt-Cleanup: {e}")

    # --- 6. Audit log + Commit ---
    try:
        await create_audit_log(
            db,
            action="system_cleanup",
            resource_type="system",
            actor_id=user_id,
            actor_email=user_email,
            description=f"Cleanup: {report['vms_purged']}P {report['vms_reset']}R {report['tasks_failed']}T {report['images_reset']}I",
            metadata={k: v for k, v in report.items() if k != "errors"},
        )
        await db.commit()
    except Exception as e:
        report["errors"].append(f"Commit: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    errors = report.pop("errors", [])
    total = sum(report.values())
    result_data = {
        **report,
        "message": f"Cleanup abgeschlossen: {total} Objekte bereinigt",
    }
    if errors:
        result_data["errors"] = errors
        result_data["message"] += f" ({len(errors)} warnings)"
    return result_data


@router.get("/networks")
async def get_available_networks(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """
    Query the host agent for available network bridges and libvirt networks.
    Used by the frontend when creating IP pools to show valid bridge options.
    """
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            response = await client.get(
                f"{AGENT_BASE_URL}/api/v1/networks",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail="Host-Agent unreachable. Please check if the agent service is running.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network query failed: {str(e)}")


@router.post("/cleanup-libvirt")
async def cleanup_libvirt_domains(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Cleanup stale libvirt domains on the host that no longer match active VMs.
    Compares host-agent domain list against database and removes orphans.
    """
    report = {"domains_removed": 0, "domains_kept": 0, "errors": []}

    try:
        # 1. Get all domains from host agent
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            response = await client.get(
                f"{AGENT_BASE_URL}/api/v1/vms",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            host_domains = response.json()

        # 2. Get all active VM domain names from DB
        result = await db.execute(
            select(VirtualMachine.libvirt_domain_name).where(
                VirtualMachine.is_deleted.is_(False),
                VirtualMachine.libvirt_domain_name.is_not(None),
                VirtualMachine.status.in_([VMStatus.RUNNING, VMStatus.STOPPED]),
            )
        )
        active_domain_names = {row[0] for row in result.all()}

        # 3. Remove orphan domains
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            for domain in host_domains:
                name = domain.get("name", "")
                if not name.startswith("vm-"):
                    report["domains_kept"] += 1
                    continue  # Not a managed domain

                if name in active_domain_names:
                    report["domains_kept"] += 1
                    continue  # Still active in DB

                # Orphan – remove it
                try:
                    del_resp = await client.delete(
                        f"{AGENT_BASE_URL}/api/v1/vms/{name}",
                        params={"vm_id": "cleanup"},
                        headers=AGENT_HEADERS,
                    )
                    del_resp.raise_for_status()
                    report["domains_removed"] += 1
                except Exception as e:
                    report["errors"].append(f"Domain '{name}': {str(e)}")

        await create_audit_log(
            db,
            action="libvirt_cleanup",
            resource_type="system",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Libvirt cleanup: {report['domains_removed']} domains removed",
            metadata=report,
        )
        await db.commit()

    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail="Host-Agent unreachable.",
        )
    except Exception as e:
        report["errors"].append(str(e))

    return report


# --- SSL / Domain Management ---


class SSLSetupRequest(BaseModel):
    domain: str
    email: str = ""


@router.get("/ssl/status")
async def get_ssl_status(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get current SSL/TLS certificate status from the host agent."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=10) as client:
            response = await client.get(
                f"{AGENT_BASE_URL}/api/v1/ssl/status",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSL status could not be retrieved: {e}")


@router.post("/ssl/test")
async def test_ssl_readiness(
    body: SSLSetupRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Test if all prerequisites for SSL setup are met."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/ssl/test",
                headers=AGENT_HEADERS,
                json={"domain": body.domain, "email": body.email},
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSL test failed: {e}")


@router.post("/ssl/setup")
async def setup_ssl(
    body: SSLSetupRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Setup Let's Encrypt SSL certificate for the given domain.
    Proxies the request to the host agent which runs certbot.
    """
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=180) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/ssl/setup",
                headers=AGENT_HEADERS,
                json={"domain": body.domain, "email": body.email},
            )
            if response.status_code != 200:
                detail = response.json().get("detail", "Unknown error") if response.headers.get("content-type", "").startswith("application/json") else response.text
                raise HTTPException(status_code=response.status_code, detail=detail)

            result = response.json()

        # Audit log
        await create_audit_log(
            db,
            action="ssl_setup",
            resource_type="system",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"SSL certificate configured for domain: {body.domain}",
        )
        await db.commit()

        return result

    except HTTPException:
        raise
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSL setup failed: {e}")


@router.post("/ssl/regenerate-config")
async def regenerate_ssl_config(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Regenerate nginx config with latest template and reload."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/ssl/regenerate-config",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Config regeneration failed: {e}")


@router.post("/ssl/disable")
async def disable_ssl(
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Disable SSL and revert to HTTP-only."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            response = await client.post(
                f"{AGENT_BASE_URL}/api/v1/ssl/disable",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            result = response.json()

        await create_audit_log(
            db,
            action="ssl_disabled",
            resource_type="system",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description="SSL certificate disabled",
        )
        await db.commit()

        return result

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SSL deactivation failed: {e}")


def _sanitize_url(url: str) -> str:
    """Remove password from database/redis URL for display."""
    try:
        if "@" in url:
            parts = url.split("@")
            prefix = parts[0].split("://")[0]
            return f"{prefix}://***@{parts[1]}"
    except Exception:
        pass
    return "***"


# --- Egress Filtering (Anti-Portscan Protection) ---


class EgressPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class EgressConfigUpdate(BaseModel):
    enabled: bool | None = None
    allowed_ports: list[EgressPortRule] | None = None
    rate_limit_per_sec: int | None = None
    rate_limit_burst: int | None = None


@router.get("/security/egress-rules")
async def get_egress_rules(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get current VM egress filtering configuration from host agent."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/egress-rules",
                headers=AGENT_HEADERS,
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


@router.put("/security/egress-rules")
async def update_egress_rules(
    body: EgressConfigUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update VM egress filtering rules. Changes apply immediately on the host."""
    try:
        payload = {}
        if body.enabled is not None:
            payload["enabled"] = body.enabled
        if body.allowed_ports is not None:
            payload["allowed_ports"] = [
                {"port": r.port, "protocol": r.protocol, "description": r.description}
                for r in body.allowed_ports
            ]
        if body.rate_limit_per_sec is not None:
            payload["rate_limit_per_sec"] = body.rate_limit_per_sec
        if body.rate_limit_burst is not None:
            payload["rate_limit_burst"] = body.rate_limit_burst

        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.put(
                f"{AGENT_BASE_URL}/api/v1/security/egress-rules",
                headers=AGENT_HEADERS,
                json=payload,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="egress_rules_updated",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Egress filtering rules updated (enabled={body.enabled})",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/security/egress-rules/add-port")
async def add_egress_port(
    body: EgressPortRule,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add a single allowed port to VM egress rules."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/security/egress-rules/add-port",
                headers=AGENT_HEADERS,
                json={"port": body.port, "protocol": body.protocol, "description": body.description},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="egress_port_added",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Egress port added: {body.protocol.upper()}:{body.port} ({body.description})",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/security/egress-rules/remove-port")
async def remove_egress_port(
    port: int,
    protocol: str = "tcp",
    current_user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an allowed port from VM egress rules."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.delete(
                f"{AGENT_BASE_URL}/api/v1/security/egress-rules/remove-port",
                headers=AGENT_HEADERS,
                params={"port": port, "protocol": protocol},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="egress_port_removed",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Egress port removed: {protocol.upper()}:{port}",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/security/egress-log")
async def get_egress_blocked_log(
    lines: int = 50,
    current_user: User = Depends(require_superadmin),
):
    """Get recent blocked egress attempts (portscan attempts, etc.)."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/egress-log",
                headers=AGENT_HEADERS,
                params={"lines": lines},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable (egress-log)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Ingress Filtering (Public VM protection) ---


class IngressPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class IngressConfigUpdate(BaseModel):
    enabled: bool | None = None
    allowed_ports: list[IngressPortRule] | None = None
    interfaces: list[str] | None = None


@router.get("/security/ingress-rules")
async def get_ingress_rules(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get current VM ingress filtering configuration from host agent."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/ingress-rules",
                headers=AGENT_HEADERS,
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


@router.put("/security/ingress-rules")
async def update_ingress_rules(
    body: IngressConfigUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update VM ingress filtering rules. Changes apply immediately on the host."""
    try:
        payload: dict = {}
        if body.enabled is not None:
            payload["enabled"] = body.enabled
        if body.allowed_ports is not None:
            payload["allowed_ports"] = [
                {"port": r.port, "protocol": r.protocol, "description": r.description}
                for r in body.allowed_ports
            ]
        if body.interfaces is not None:
            payload["interfaces"] = body.interfaces

        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.put(
                f"{AGENT_BASE_URL}/api/v1/security/ingress-rules",
                headers=AGENT_HEADERS,
                json=payload,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="ingress_rules_updated",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Ingress filtering rules updated (enabled={body.enabled})",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/security/ingress-rules/add-port")
async def add_ingress_port(
    body: IngressPortRule,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add a single allowed port to VM ingress rules."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/security/ingress-rules/add-port",
                headers=AGENT_HEADERS,
                json={"port": body.port, "protocol": body.protocol, "description": body.description},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="ingress_port_added",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Ingress port added: {body.protocol.upper()}:{body.port} ({body.description})",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/security/ingress-rules/remove-port")
async def remove_ingress_port(
    port: int,
    protocol: str = "tcp",
    current_user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an allowed port from VM ingress rules."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.delete(
                f"{AGENT_BASE_URL}/api/v1/security/ingress-rules/remove-port",
                headers=AGENT_HEADERS,
                params={"port": port, "protocol": protocol},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="ingress_port_removed",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Ingress port removed: {protocol.upper()}:{port}",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- IP Blacklist / Fail2Ban ---


class IPBanRequest(BaseModel):
    ip: str
    reason: str = ""
    permanent: bool = True


@router.get("/security/blacklist")
async def get_blacklist(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get all blacklisted IPs (manual bans + fail2ban)."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/blacklist",
                headers=AGENT_HEADERS,
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


@router.post("/security/blacklist/ban")
async def ban_ip(
    body: IPBanRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Manually ban an IP address."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/security/blacklist/ban",
                headers=AGENT_HEADERS,
                json={"ip": body.ip, "reason": body.reason, "permanent": body.permanent},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="ip_banned",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"IP banned: {body.ip} (reason: {body.reason})",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/security/blacklist/unban")
async def unban_ip(
    body: IPBanRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Unban an IP address (removes from manual list and fail2ban)."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.post(
                f"{AGENT_BASE_URL}/api/v1/security/blacklist/unban",
                headers=AGENT_HEADERS,
                json={"ip": body.ip},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="ip_unbanned",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"IP unbanned: {body.ip}",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/security/blacklist/check/{ip}")
async def check_ip(
    ip: str,
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Check if a specific IP is currently banned."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/blacklist/check/{ip}",
                headers=AGENT_HEADERS,
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


@router.get("/security/fail2ban/log")
async def get_fail2ban_log(
    lines: int = 50,
    current_user: User = Depends(require_superadmin),
):
    """Get recent fail2ban log entries."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/fail2ban/log",
                headers=AGENT_HEADERS,
                params={"lines": lines},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable (fail2ban-log)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class Fail2BanJailUpdate(BaseModel):
    jail: str
    bantime: int | None = None
    findtime: int | None = None
    maxretry: int | None = None


@router.get("/security/fail2ban/config")
async def get_fail2ban_config(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get fail2ban jail configuration with runtime values."""
    try:
        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.get(
                f"{AGENT_BASE_URL}/api/v1/security/fail2ban/config",
                headers=AGENT_HEADERS,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable (fail2ban-config)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/security/fail2ban/config")
async def update_fail2ban_config(
    body: Fail2BanJailUpdate,
    current_user: Annotated[User, Depends(require_superadmin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update fail2ban jail settings (bantime, findtime, maxretry). Applied immediately."""
    try:
        payload = {"jail": body.jail}
        if body.bantime is not None:
            payload["bantime"] = body.bantime
        if body.findtime is not None:
            payload["findtime"] = body.findtime
        if body.maxretry is not None:
            payload["maxretry"] = body.maxretry

        async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=15) as client:
            resp = await client.put(
                f"{AGENT_BASE_URL}/api/v1/security/fail2ban/config",
                headers=AGENT_HEADERS,
                json=payload,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)

        await create_audit_log(
            db,
            action="fail2ban_config_updated",
            resource_type="security",
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Fail2Ban jail '{body.jail}' updated: bantime={body.bantime}, findtime={body.findtime}, maxretry={body.maxretry}",
        )
        await db.commit()
        return resp.json()

    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Host-Agent unreachable (fail2ban-config)")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
