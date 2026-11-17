"""
Celery tasks for VM lifecycle operations.
These tasks communicate with the Host Agent to perform actual VM operations.
Each task reads VM data from the database, sends requests to the Host Agent,
and updates VM + Task status accordingly.
"""

import os
import random
import time
import uuid
from datetime import datetime, timezone

import httpx

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.workers.celery_app import celery_app

AGENT_BASE_URL = settings.HOST_AGENT_URL
AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}


def _state_from_status_response(resp: httpx.Response) -> str:
    """
    Parse host-agent VM status response safely.
    Some libvirt edge cases return 500 with "domain is not running" even though
    the effective state is stopped.
    """
    if resp.status_code >= 400:
        body = (resp.text or "").lower()
        if (
            "domain is not running" in body
            or "domain not found" in body
            or "no domain with matching name" in body
        ):
            return "stopped"
        resp.raise_for_status()
    payload = resp.json() or {}
    return str(payload.get("state", "unknown")).lower()


def _generate_mac_address() -> str:
    """Generate a random MAC address with QEMU prefix 52:54:00."""
    return "52:54:00:{:02x}:{:02x}:{:02x}".format(
        random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)
    )


def _get_sync_session():
    """Create a synchronous database session for Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "postgresql://" in sync_url and "+psycopg2" not in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(sync_url)
    return Session(engine)


def _extract_error(e: Exception) -> str:
    """Extract a useful error message from an exception."""
    msg = str(e)[:500]
    if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
        try:
            msg = e.response.text[:500]
        except Exception:
            pass
    return msg


def _fail_task(session, vm_id: str, task_id: str, error_msg: str):
    """Mark VM as ERROR and Task as FAILED."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))
        if vm:
            vm.status = VMStatus.ERROR
        if task_obj:
            task_obj.status = TaskStatus.FAILED
            task_obj.error_message = error_msg
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()


# ─── VM Create ───────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.create", max_retries=3)
def task_vm_create(self, vm_id: str, task_id: str):
    """
    Create a VM via the Host Agent:
    1. Read VM + template data from DB
    2. Build provision request payload
    3. POST to Host Agent /api/v1/vms/provision
    4. Update VM status (RUNNING) and Task status (SUCCESS)
    """
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.storage import CloudImage, StoragePool
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm:
            if task_obj:
                task_obj.status = TaskStatus.FAILED
                task_obj.error_message = "VM not found in database"
                task_obj.completed_at = datetime.now(timezone.utc)
                session.commit()
            return {"error": "VM not found"}

        if not vm.template_id:
            _fail_task(session, vm_id, task_id, "No template assigned")
            return {"error": "No template assigned"}

        template = session.get(CloudImage, vm.template_id)
        if not template or not template.file_path:
            _fail_task(session, vm_id, task_id, "Template media not available")
            return {"error": "Template not found or not ready"}

        # Resolve storage pool path
        storage_pool_path = None
        if vm.storage_pool_id:
            pool = session.get(StoragePool, vm.storage_pool_id)
            if pool and pool.is_active:
                storage_pool_path = pool.path

        # Mark task as running
        if task_obj:
            task_obj.status = TaskStatus.RUNNING
            task_obj.started_at = task_obj.started_at or datetime.now(timezone.utc)
        session.commit()

        # Build provision payload
        disk_format = vm.disk_format
        if hasattr(disk_format, "value"):
            disk_format = disk_format.value

        template_format = template.format.value if hasattr(template.format, "value") else str(template.format)
        is_iso_install = template_format == "iso"
        install_options = {}
        start_after_create = True
        if isinstance(vm.cloud_init_metadata, dict):
            raw_opts = vm.cloud_init_metadata.get("_install_options")
            if isinstance(raw_opts, dict):
                install_options = raw_opts
            if "_start_after_create" in vm.cloud_init_metadata:
                start_after_create = bool(vm.cloud_init_metadata.get("_start_after_create"))

        guest_ip = None
        if isinstance(vm.cloud_init_network, dict):
            ethernets = vm.cloud_init_network.get("ethernets") or {}
            for cfg in ethernets.values():
                if not isinstance(cfg, dict):
                    continue
                addrs = cfg.get("addresses") or []
                if addrs:
                    guest_ip = str(addrs[0]).split("/")[0]
                    break

        payload = {
            "vm_id": str(vm.id),
            "domain_name": vm.libvirt_domain_name,
            "vcpus": vm.vcpus,
            "ram_mb": vm.ram_mb,
            "disk_size_gb": vm.disk_size_gb,
            "disk_format": str(disk_format),
            "template_path": None if is_iso_install else template.file_path,
            "iso_path": template.file_path if is_iso_install else None,
            "virtio_iso_path": install_options.get("virtio_iso_path"),
            "boot_from_iso": is_iso_install,
            "os_profile": str(install_options.get("os_profile", "linux")),
            "mac_address": vm.mac_address or _generate_mac_address(),
            "bridge": vm.bridge or "br0",
            "guest_ip": guest_ip,
            "secondary_interfaces": vm.secondary_interfaces or [],
            "cloud_init_userdata": vm.cloud_init_userdata or {},
            "cloud_init_metadata": vm.cloud_init_metadata or {},
            "cloud_init_network": vm.cloud_init_network,
            "start_after_create": start_after_create,
        }

        # Include custom storage path if a non-default pool is assigned
        if storage_pool_path:
            payload["storage_base_path"] = storage_pool_path

        print(f"[VM {vm_id[:8]}] Sending provision request to Host Agent...")

        # Send to Host Agent
        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=300) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/provision",
                json=payload,
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            result = response.json()

        # Success – update VM and Task
        data = result.get("data", {})
        started = data.get("started")
        if started is None:
            started = start_after_create
        vm.status = VMStatus.RUNNING if started else VMStatus.STOPPED
        if data.get("vnc_port"):
            vm.vnc_port = data["vnc_port"]
        if data.get("disk_path"):
            vm.disk_path = data["disk_path"]

        # Never keep guest passwords in DB after seed was sent to the host-agent
        from app.services.cloud_init_sanitize import sanitize_cloud_init_userdata
        from sqlalchemy.orm.attributes import flag_modified

        cleaned = sanitize_cloud_init_userdata(vm.cloud_init_userdata)
        if cleaned is not None:
            vm.cloud_init_userdata = cleaned
            flag_modified(vm, "cloud_init_userdata")

        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
            task_obj.result = result

        session.commit()

        print(f"[VM {vm_id[:8]}] Provisioned successfully! VNC port: {data.get('vnc_port')}")

        # Email notification: VM created
        try:
            from app.services.notification_dispatcher import dispatch_vm_created
            ip_addr = ""
            if vm.cloud_init_network:
                ethernets = (vm.cloud_init_network or {}).get("ethernets", {})
                for cfg in ethernets.values():
                    addrs = cfg.get("addresses", [])
                    if addrs:
                        ip_addr = str(addrs[0])
                        break
            dispatch_vm_created(vm_id, vm.name, ip_addr)
        except Exception:
            pass

        return result

    except httpx.HTTPError as e:
        session.rollback()
        error_msg = _extract_error(e)
        print(f"[VM {vm_id[:8]}] Provision failed: {error_msg}")
        _fail_task(session, vm_id, task_id, error_msg)

        # Email notification: VM error
        try:
            from app.services.notification_dispatcher import dispatch_vm_error
            dispatch_vm_error(vm_id, vm.name if vm else vm_id[:8], error_msg)
        except Exception:
            pass

        self.retry(exc=e, countdown=30)

    except Exception as e:
        session.rollback()
        error_msg = str(e)[:500]
        print(f"[VM {vm_id[:8]}] Unexpected error: {error_msg}")
        _fail_task(session, vm_id, task_id, error_msg)

        # Email notification: VM error
        try:
            from app.services.notification_dispatcher import dispatch_vm_error
            dispatch_vm_error(vm_id, vm.name if vm else vm_id[:8], error_msg)
        except Exception:
            pass

        raise

    finally:
        session.close()


# ─── VM Start ────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.start", max_retries=3)
def task_vm_start(self, vm_id: str, task_id: str):
    """Start a VM via the Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not vm.libvirt_domain_name:
            _fail_task(session, vm_id, task_id, "VM or domain name not found")
            return

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
            task_obj.started_at = task_obj.started_at or datetime.now(timezone.utc)
        session.commit()

        domain_name = vm.libvirt_domain_name

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=60) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{domain_name}/start",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            result = response.json()

        # Success
        vnc_port = result.get("data", {}).get("vnc_port")
        vm.status = VMStatus.RUNNING
        if vnc_port:
            vm.vnc_port = vnc_port

        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        return result

    except httpx.HTTPError as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, _extract_error(e))
        self.retry(exc=e, countdown=10)

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


# ─── VM Stop ─────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.stop", max_retries=3)
def task_vm_stop(self, vm_id: str, task_id: str):
    """Gracefully stop a VM via the Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not vm.libvirt_domain_name:
            _fail_task(session, vm_id, task_id, "VM or domain name not found")
            return

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
            task_obj.started_at = task_obj.started_at or datetime.now(timezone.utc)
        session.commit()

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=120) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/stop",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()

            # Wait until VM is really stopped on the host.
            # Some guests ignore/slow-handle ACPI shutdown; then we fallback to force-stop.
            graceful_deadline = time.time() + 30
            host_state = "running"
            while time.time() < graceful_deadline:
                status_resp = client.get(
                    f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/status",
                    headers=AGENT_HEADERS,
                )
                host_state = _state_from_status_response(status_resp)
                if host_state in {"stopped", "shutoff"}:
                    break
                time.sleep(2)

            if host_state not in {"stopped", "shutoff"}:
                force_resp = client.post(
                    f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/force-stop",
                    headers=AGENT_HEADERS,
                )
                force_resp.raise_for_status()

                verify_deadline = time.time() + 10
                while time.time() < verify_deadline:
                    status_resp = client.get(
                        f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/status",
                        headers=AGENT_HEADERS,
                    )
                    host_state = _state_from_status_response(status_resp)
                    if host_state in {"stopped", "shutoff"}:
                        break
                    time.sleep(1)

                if host_state not in {"stopped", "shutoff"}:
                    raise RuntimeError(f"VM stop not confirmed (state={host_state})")

        vm.status = VMStatus.STOPPED
        vm.vnc_port = None
        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        return {"status": "stopped"}

    except httpx.HTTPError as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, _extract_error(e))
        self.retry(exc=e, countdown=10)

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


# ─── VM Force Stop ───────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.force_stop", max_retries=1)
def task_vm_force_stop(self, vm_id: str, task_id: str):
    """Force stop a VM via the Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not vm.libvirt_domain_name:
            _fail_task(session, vm_id, task_id, "VM or domain name not found")
            return

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
        session.commit()

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/force-stop",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()

        vm.status = VMStatus.STOPPED
        vm.vnc_port = None
        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        return {"status": "stopped"}

    except httpx.HTTPError as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, _extract_error(e))
        self.retry(exc=e, countdown=5)

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


# ─── VM Reboot ───────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.reboot", max_retries=3)
def task_vm_reboot(self, vm_id: str, task_id: str):
    """Reboot a VM via the Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not vm.libvirt_domain_name:
            _fail_task(session, vm_id, task_id, "VM or domain name not found")
            return

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
        session.commit()

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=120) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/reboot",
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()

        vm.status = VMStatus.RUNNING
        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        return {"status": "rebooted"}

    except httpx.HTTPError as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, _extract_error(e))
        self.retry(exc=e, countdown=10)

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


# ─── VM Delete ───────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.delete", max_retries=3)
def task_vm_delete(self, vm_id: str, task_id: str):
    """Delete a VM via the Host Agent (destroy domain + cleanup disks)."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm:
            if task_obj:
                task_obj.status = TaskStatus.SUCCESS
                task_obj.completed_at = datetime.now(timezone.utc)
                session.commit()
            return {"status": "already deleted"}

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
        session.commit()

        domain_name = vm.libvirt_domain_name

        if domain_name:
            with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=120) as client:
                response = client.delete(
                    f"{AGENT_BASE_URL}/api/v1/vms/{domain_name}",
                    params={"vm_id": str(vm.id)},
                    headers=AGENT_HEADERS,
                )
                response.raise_for_status()

        # Remove per-VM firewall profile (iptables + config)
        try:
            with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=10) as fw_client:
                fw_client.delete(
                    f"{AGENT_BASE_URL}/api/v1/security/vm-firewall/{vm.id}",
                    headers=AGENT_HEADERS,
                )
        except Exception:
            pass  # Best effort

        # Clean up port forwarding rules (DB + iptables)
        from app.models.network import PortForward, IPAllocation, AllocationStatus
        pf_rules = session.query(PortForward).filter(
            PortForward.vm_id == vm.id
        ).all()
        for pf in pf_rules:
            # Remove iptables rule on host
            try:
                with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=10) as pf_client:
                    pf_client.post(
                        f"{AGENT_BASE_URL}/api/v1/networks/port-forward/remove",
                        headers=AGENT_HEADERS,
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
            session.delete(pf)

        # Release IP allocations (public IPs stay reserved for the tenant)
        from app.models.network import IPPool, PoolType

        ip_allocs = session.query(IPAllocation).filter(
            IPAllocation.vm_id == vm.id
        ).all()
        for alloc in ip_allocs:
            pool = session.get(IPPool, alloc.pool_id)
            alloc.vm_id = None
            alloc.description = None
            if (
                pool
                and pool.pool_type == PoolType.EXTERNAL
                and alloc.tenant_id is not None
            ):
                alloc.status = AllocationStatus.RESERVED
            else:
                alloc.status = AllocationStatus.AVAILABLE
                # Private tenant pools keep tenant_id on pool; clear alloc tenant only for shared
                if not pool or pool.tenant_id is None:
                    alloc.tenant_id = None

        # Release storage pool usage
        if vm.storage_pool_id:
            from app.models.storage import StoragePool
            pool = session.get(StoragePool, vm.storage_pool_id)
            if pool:
                pool.used_size_gb = max(0, (pool.used_size_gb or 0) - vm.disk_size_gb)

        # Mark as deleted (soft delete)
        vm.status = VMStatus.DELETED
        vm.is_deleted = True
        vm.vnc_port = None
        vm.vnc_websocket_port = None

        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        return {"status": "deleted"}

    except httpx.HTTPError as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, _extract_error(e))
        self.retry(exc=e, countdown=15)

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


@celery_app.task(bind=True, name="vm.clone", max_retries=3)
def task_vm_clone(self, vm_id: str, task_id: str):
    """
    Clone a VM via the Host Agent:
    1. Read source VM domain name from task result
    2. Send clone request to Host Agent
    3. Start the new VM
    4. Update status
    """
    session = _get_sync_session()

    try:
        from app.models.vm import VirtualMachine, VMStatus
        from app.models.task import Task, TaskStatus

        vm = session.query(VirtualMachine).filter(VirtualMachine.id == uuid.UUID(vm_id)).first()
        task_obj = session.query(Task).filter(Task.id == uuid.UUID(task_id)).first()

        if not vm:
            _fail_task(session, vm_id, task_id, "VM not found in DB")
            return

        # Get source info from task result
        source_domain_name = task_obj.result.get("source_domain_name") if task_obj and task_obj.result else None
        if not source_domain_name:
            _fail_task(session, vm_id, task_id, "Source domain name nicht im Task gespeichert")
            return

        if task_obj:
            task_obj.status = TaskStatus.RUNNING
            task_obj.started_at = datetime.now(timezone.utc)
            session.commit()

        # Call Host Agent clone endpoint
        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=120) as client:
            resp = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/clone",
                headers=AGENT_HEADERS,
                json={
                    "source_domain_name": source_domain_name,
                    "new_domain_name": vm.libvirt_domain_name,
                    "new_vm_id": str(vm.id),
                    "mac_address": vm.mac_address,
                    "bridge": vm.bridge,
                    "cloud_init_userdata": vm.cloud_init_userdata or {},
                    "cloud_init_metadata": vm.cloud_init_metadata or {},
                    "cloud_init_network": vm.cloud_init_network,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        # Start the cloned VM
        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=30) as client:
            start_resp = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/{vm.libvirt_domain_name}/start",
                headers=AGENT_HEADERS,
            )
            if start_resp.status_code == 200:
                start_data = start_resp.json()
                vm.vnc_port = start_data.get("data", {}).get("vnc_port")
                vm.vnc_websocket_port = start_data.get("data", {}).get("vnc_websocket_port")

        vm.status = VMStatus.RUNNING

        from app.services.cloud_init_sanitize import sanitize_cloud_init_userdata
        from sqlalchemy.orm.attributes import flag_modified

        cleaned = sanitize_cloud_init_userdata(vm.cloud_init_userdata)
        if cleaned is not None:
            vm.cloud_init_userdata = cleaned
            flag_modified(vm, "cloud_init_userdata")

        if task_obj:
            task_obj.status = TaskStatus.SUCCESS
            task_obj.completed_at = datetime.now(timezone.utc)
            task_obj.result = {**(task_obj.result or {}), **result}
        session.commit()

    except httpx.HTTPStatusError as e:
        session.rollback()
        detail = e.response.text[:500] if e.response else str(e)
        _fail_task(session, vm_id, task_id, f"Host-Agent error: {detail}")
        raise

    except Exception as e:
        session.rollback()
        _fail_task(session, vm_id, task_id, str(e)[:500])
        raise

    finally:
        session.close()


# ─── VM Backup ────────────────────────────────────────────────────────────────


def _fail_backup(session, backup_id: str, task_id: str, error_msg: str):
    """Mark Backup as FAILED and Task as FAILED."""
    from app.models.backup import VMBackup, BackupStatus
    from app.models.task import Task, TaskStatus

    try:
        backup = session.get(VMBackup, uuid.UUID(backup_id))
        task_obj = session.get(Task, uuid.UUID(task_id))
        if backup:
            backup.status = BackupStatus.FAILED
            backup.error_message = error_msg
        if task_obj:
            task_obj.status = TaskStatus.FAILED
            task_obj.error_message = error_msg
            task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()
    except Exception:
        session.rollback()


@celery_app.task(bind=True, name="vm.backup", max_retries=1)
def task_vm_backup(self, vm_id: str, backup_id: str, task_id: str):
    """
    Create a VM backup via the Host Agent:
    1. Read VM data from DB
    2. POST to Host Agent /api/v1/backups/create
    3. Update Backup and Task status
    """
    from app.models.vm import VirtualMachine
    from app.models.backup import VMBackup, BackupStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        backup = session.get(VMBackup, uuid.UUID(backup_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not backup or not task_obj:
            _fail_backup(session, backup_id, task_id, "VM or backup not found")
            return

        task_obj.status = TaskStatus.RUNNING
        task_obj.started_at = datetime.now(timezone.utc)
        session.commit()

        # Call Host Agent
        import logging
        logger = logging.getLogger("celery.task")
        logger.warning(f"[Backup {backup_id[:8]}] Creating backup for VM {vm.libvirt_domain_name}...")

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=600) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/backups/create",
                headers=AGENT_HEADERS,
                json={
                    "domain_name": vm.libvirt_domain_name,
                    "backup_id": backup_id,
                    "backup_name": backup.name,
                },
            )
            response.raise_for_status()
            result = response.json()

        local_path = result.get("file_path")
        backup.file_path = local_path
        backup.file_size_bytes = result.get("file_size_bytes")
        backup.storage_backend = "internal"
        backup.remote_path = None

        # Optional offload to FTP / S3
        try:
            from app.services.backup_storage import (
                get_backup_storage_config_sync,
                upload_backup_file,
            )
            cfg = get_backup_storage_config_sync(session)
            backend = cfg.get("backend", "internal")
            if backend in ("ftp", "s3") and local_path:
                remote_name = os.path.basename(local_path)
                remote_path = upload_backup_file(cfg, local_path, remote_name)
                backup.storage_backend = backend
                backup.remote_path = remote_path
                if not cfg.get("keep_local_copy", True):
                    try:
                        if os.path.isfile(local_path):
                            os.remove(local_path)
                        backup.file_path = None
                    except OSError:
                        pass
                logger.warning(
                    f"[Backup {backup_id[:8]}] Uploaded to {backend}: {remote_path}"
                )
        except Exception as upload_err:
            logger.warning(f"[Backup {backup_id[:8]}] Remote upload skipped/failed: {upload_err}")

        backup.status = BackupStatus.READY
        task_obj.status = TaskStatus.SUCCESS
        task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        logger.warning(f"[Backup {backup_id[:8]}] Backup created successfully: {local_path}")

        # Email notification: backup completed
        try:
            from app.services.notification_dispatcher import dispatch_backup_completed
            size_str = ""
            if result.get("file_size_bytes"):
                size_mb = result["file_size_bytes"] / (1024 * 1024)
                size_str = f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"
            dispatch_backup_completed(vm_id, vm.name, backup.name, size_str)
        except Exception:
            pass

    except httpx.ConnectError as e:
        session.rollback()
        error_detail = f"Host agent unreachable: {e}"
        _fail_backup(session, backup_id, task_id, error_detail)

        try:
            from app.services.notification_dispatcher import dispatch_backup_failed
            dispatch_backup_failed(vm_id, vm.name if vm else vm_id[:8], backup.name if backup else backup_id[:8], error_detail)
        except Exception:
            pass

        try:
            self.retry(exc=e, countdown=30)
        except Exception:
            pass

    except httpx.HTTPStatusError as e:
        session.rollback()
        detail = e.response.text[:500] if e.response else str(e)
        _fail_backup(session, backup_id, task_id, f"Host-Agent error: {detail}")

        try:
            from app.services.notification_dispatcher import dispatch_backup_failed
            dispatch_backup_failed(vm_id, vm.name if vm else vm_id[:8], backup.name if backup else backup_id[:8], detail)
        except Exception:
            pass

    except Exception as e:
        session.rollback()
        error_detail = str(e)[:500]
        _fail_backup(session, backup_id, task_id, error_detail)

        try:
            from app.services.notification_dispatcher import dispatch_backup_failed
            dispatch_backup_failed(vm_id, vm.name if vm else vm_id[:8], backup.name if backup else backup_id[:8], error_detail)
        except Exception:
            pass

    finally:
        session.close()


# ─── VM Restore ───────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.restore", max_retries=1)
def task_vm_restore(self, vm_id: str, backup_id: str, task_id: str):
    """
    Restore a VM from backup via the Host Agent:
    1. Read VM + Backup data from DB
    2. POST to Host Agent /api/v1/backups/restore
    3. Update Backup and Task status
    """
    from app.models.vm import VirtualMachine
    from app.models.backup import VMBackup, BackupStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        backup = session.get(VMBackup, uuid.UUID(backup_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not backup or not task_obj:
            _fail_backup(session, backup_id, task_id, "VM or backup not found")
            return

        task_obj.status = TaskStatus.RUNNING
        task_obj.started_at = datetime.now(timezone.utc)
        session.commit()

        import logging
        logger = logging.getLogger("celery.task")
        logger.warning(f"[Restore {backup_id[:8]}] Restoring VM {vm.libvirt_domain_name} from backup...")

        restore_path = backup.file_path
        downloaded_tmp = None
        try:
            from app.services.backup_storage import (
                download_backup_file,
                get_backup_storage_config_sync,
            )
            cfg = get_backup_storage_config_sync(session)
            backend = (backup.storage_backend or cfg.get("backend", "internal") or "internal").lower()
            # Host-agent path jail only allows /var/lib/qemu-interface/** — never /tmp.
            # Worker + agent share STORAGE via bind-mount, so download into backups/.
            if (not restore_path or not os.path.isfile(restore_path)) and backup.remote_path and backend in ("ftp", "s3"):
                restore_dir = os.path.join(
                    settings.DEFAULT_STORAGE_POOL_PATH,
                    "backups",
                )
                os.makedirs(restore_dir, exist_ok=True)
                downloaded_tmp = os.path.join(
                    restore_dir,
                    f"qi-restore-{backup_id}.qcow2",
                )
                # Prefer the backup's own backend over the current platform default
                dl_cfg = dict(cfg)
                dl_cfg["backend"] = backend
                download_backup_file(dl_cfg, backup.remote_path, downloaded_tmp)
                restore_path = downloaded_tmp
                logger.warning(f"[Restore {backup_id[:8]}] Downloaded from {backend} to {restore_path}")
        except Exception as dl_err:
            raise RuntimeError(f"Failed to fetch remote backup: {dl_err}") from dl_err

        if not restore_path:
            raise RuntimeError("Backup file path is missing")
        if not os.path.isfile(restore_path):
            raise RuntimeError(f"Backup file not found at {restore_path}")

        try:
            with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=600) as client:
                response = client.post(
                    f"{AGENT_BASE_URL}/api/v1/backups/restore",
                    headers=AGENT_HEADERS,
                    json={
                        "domain_name": vm.libvirt_domain_name,
                        "backup_file_path": restore_path,
                    },
                )
                response.raise_for_status()
        finally:
            if downloaded_tmp and os.path.isfile(downloaded_tmp):
                try:
                    os.remove(downloaded_tmp)
                except OSError:
                    pass

        # Update statuses
        backup.status = BackupStatus.READY
        task_obj.status = TaskStatus.SUCCESS
        task_obj.completed_at = datetime.now(timezone.utc)
        session.commit()

        logger.warning(f"[Restore {backup_id[:8]}] VM restored successfully")

    except httpx.ConnectError as e:
        session.rollback()
        _fail_backup(session, backup_id, task_id, f"Host-Agent nicht erreichbar: {e}")
        try:
            self.retry(exc=e, countdown=30)
        except Exception:
            pass

    except httpx.HTTPStatusError as e:
        session.rollback()
        detail = e.response.text[:500] if e.response else str(e)
        _fail_backup(session, backup_id, task_id, f"Host-Agent error: {detail}")

    except Exception as e:
        session.rollback()
        _fail_backup(session, backup_id, task_id, str(e)[:500])

    finally:
        session.close()


# ─── VM Export ────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.export", max_retries=1)
def task_vm_export(self, vm_id: str, task_id: str):
    """Export a VM disk as a compressed standalone qcow2 via Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not task_obj:
            return

        task_obj.status = TaskStatus.RUNNING
        task_obj.started_at = datetime.now(timezone.utc)
        session.commit()

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=7200) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/export",
                json={
                    "domain_name": vm.libvirt_domain_name,
                    "vm_id": str(vm.id),
                    "vm_name": vm.name,
                },
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            result = response.json()

        task_obj.status = TaskStatus.SUCCESS
        task_obj.completed_at = datetime.now(timezone.utc)
        task_obj.result = result
        session.commit()

        print(f"[Export {vm_id[:8]}] Export completed: {result.get('file_name')}")
        return result

    except Exception as e:
        session.rollback()
        error_msg = str(e)[:500]
        if task_obj:
            task_obj.status = TaskStatus.FAILED
            task_obj.error_message = error_msg
            task_obj.completed_at = datetime.now(timezone.utc)
            session.commit()
        print(f"[Export {vm_id[:8]}] Failed: {error_msg}")

    finally:
        session.close()


# ─── VM Import ────────────────────────────────────────────────────────────────


@celery_app.task(bind=True, name="vm.import_register", max_retries=1)
def task_vm_import_register(self, vm_id: str, task_id: str):
    """Register an imported VM in libvirt via Host Agent."""
    from app.models.vm import VirtualMachine, VMStatus
    from app.models.task import Task, TaskStatus

    session = _get_sync_session()
    try:
        vm = session.get(VirtualMachine, uuid.UUID(vm_id))
        task_obj = session.get(Task, uuid.UUID(task_id))

        if not vm or not task_obj:
            return

        task_obj.status = TaskStatus.RUNNING
        task_obj.started_at = datetime.now(timezone.utc)
        session.commit()

        # Resolve storage pool path
        storage_pool_path = None
        if vm.storage_pool_id:
            from app.models.storage import StoragePool
            pool = session.get(StoragePool, vm.storage_pool_id)
            if pool and pool.is_active:
                storage_pool_path = pool.path

        payload = {
            "vm_id": str(vm.id),
            "domain_name": vm.libvirt_domain_name,
            "disk_format": vm.disk_format.value if hasattr(vm.disk_format, 'value') else str(vm.disk_format),
            "vcpus": vm.vcpus,
            "ram_mb": vm.ram_mb,
            "disk_size_gb": vm.disk_size_gb,
            "mac_address": vm.mac_address,
            "bridge": vm.bridge or "virbr0",
        }
        if storage_pool_path:
            payload["storage_base_path"] = storage_pool_path

        with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=300) as client:
            response = client.post(
                f"{AGENT_BASE_URL}/api/v1/vms/import/{str(vm.id)}/register",
                json=payload,
                headers=AGENT_HEADERS,
            )
            response.raise_for_status()
            result = response.json()

        data = result
        vm.status = VMStatus.STOPPED
        if data.get("disk_path"):
            vm.disk_path = data["disk_path"]
        if data.get("vnc_port"):
            vm.vnc_port = data["vnc_port"]

        task_obj.status = TaskStatus.SUCCESS
        task_obj.completed_at = datetime.now(timezone.utc)
        task_obj.result = result
        session.commit()

        print(f"[Import {vm_id[:8]}] Registered successfully!")
        return result

    except Exception as e:
        session.rollback()
        error_msg = str(e)[:500]
        if task_obj:
            task_obj.status = TaskStatus.FAILED
            task_obj.error_message = error_msg
            task_obj.completed_at = datetime.now(timezone.utc)
        if vm:
            vm.status = VMStatus.ERROR
        try:
            session.commit()
        except Exception:
            session.rollback()
        print(f"[Import {vm_id[:8]}] Failed: {error_msg}")

    finally:
        session.close()
