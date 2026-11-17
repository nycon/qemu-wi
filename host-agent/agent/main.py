"""
Host Agent API – FastAPI application for executing VM operations on the host.

This agent runs with elevated privileges and accepts only authenticated
requests from the Control Plane. It also provides a WebSocket VNC proxy
for noVNC console access.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import psutil
from fastapi import Depends, FastAPI, HTTPException, Header, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.config import agent_settings
from agent.nginx_platform import (
    lock_cors_to_panel_domain,
    sync_platform_config,
    write_platform_config,
)
from services.cloudinit_service import CloudInitService
from services.libvirt_service import LibvirtService
from services.storage_service import StorageService


# --- Services (singleton-like) ---
libvirt_svc = LibvirtService()
storage_svc = StorageService()
cloudinit_svc = CloudInitService()

# --- VNC Mapping ---
VNC_MAPPING_FILE = "/var/lib/qemu-interface/vnc-mapping.json"


def _load_vnc_mappings() -> dict:
    """Load VNC mappings from disk."""
    if os.path.exists(VNC_MAPPING_FILE):
        try:
            with open(VNC_MAPPING_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_vnc_mapping(vm_id: str, domain_name: str, vnc_port: int):
    """Save a VNC mapping for a VM."""
    mappings = _load_vnc_mappings()
    mappings[vm_id] = {"domain_name": domain_name, "vnc_port": vnc_port}
    os.makedirs(os.path.dirname(VNC_MAPPING_FILE), exist_ok=True)
    with open(VNC_MAPPING_FILE, "w") as f:
        json.dump(mappings, f, indent=2)


def _remove_vnc_mapping(vm_id: str):
    """Remove a VNC mapping for a VM."""
    mappings = _load_vnc_mappings()
    mappings.pop(vm_id, None)
    try:
        with open(VNC_MAPPING_FILE, "w") as f:
            json.dump(mappings, f, indent=2)
    except Exception:
        pass


def _get_vnc_port(vm_id: str) -> int | None:
    """Get the VNC port for a VM."""
    mappings = _load_vnc_mappings()
    entry = mappings.get(vm_id)
    return entry.get("vnc_port") if entry else None


def _get_vnc_mapping(vm_id: str) -> dict | None:
    """Get full VNC mapping entry for a VM."""
    return _load_vnc_mappings().get(vm_id)


def _find_latest_virtio_iso() -> str | None:
    """Auto-detect the newest VirtIO ISO in the images directory."""
    images_dir = Path("/var/lib/qemu-interface/images")
    if not images_dir.exists():
        return None
    candidates = sorted(
        [p for p in images_dir.glob("*") if p.is_file() and p.suffix.lower() == ".iso" and "virtio" in p.name.lower()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def _bootstrap_host_network():
    """Re-apply sysctl and port-forwards after host reboot (iptables-persistent alone is not enough)."""
    import subprocess

    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True)
    if os.path.exists("/sys/class/net/br0"):
        subprocess.run(["sysctl", "-w", "net.ipv4.conf.br0.proxy_arp=1"], capture_output=True)
        subprocess.run(["sysctl", "-w", "net.ipv4.conf.all.proxy_arp=1"], capture_output=True)

    rules = _load_port_forwards()
    restored = 0
    for rule in rules:
        try:
            proto = rule["protocol"]
            ext_port = rule["external_port"]
            int_ip = rule["internal_ip"]
            int_port = rule["internal_port"]
            check = subprocess.run([
                "iptables", "-t", "nat", "-C", "PREROUTING",
                "-p", proto, "--dport", str(ext_port),
                "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
            ], capture_output=True)
            if check.returncode != 0:
                subprocess.run([
                    "iptables", "-t", "nat", "-A", "PREROUTING",
                    "-p", proto, "--dport", str(ext_port),
                    "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
                ], check=True, capture_output=True)
                subprocess.run([
                    "iptables", "-I", "FORWARD",
                    "-p", proto, "-d", int_ip, "--dport", str(int_port),
                    "-j", "ACCEPT",
                ], check=True, capture_output=True)
            restored += 1
        except Exception as e:
            print(f"[Boot] Port-forward restore failed for {rule.get('rule_id', '?')}: {e}")
    if restored:
        print(f"[Boot] Restored {restored} port-forward rule(s)")
    _persist_iptables()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown."""
    print("Host Agent starting...")
    _bootstrap_host_network()
    try:
        enabled = libvirt_svc.ensure_all_vm_autostart()
        if enabled:
            print(f"[Boot] libvirt autostart enabled for {enabled} VM(s)")
    except Exception as e:
        print(f"[Boot] Warning: VM autostart check failed: {e}")
    try:
        sync_platform_config()
        print("[Boot] Nginx platform config synced")
    except Exception as e:
        print(f"[Boot] Warning: nginx platform sync failed: {e}")
    _ensure_egress_filtering()
    _ensure_ingress_filtering()
    _apply_all_vm_firewall_profiles()
    yield
    libvirt_svc.close()
    print("Host Agent stopped.")


app = FastAPI(
    title="QEMU Interface Host Agent",
    version="0.1.0",
    lifespan=lifespan,
)


# --- Auth Dependency ---


async def verify_agent_token(
    authorization: str = Header(...),
) -> str:
    """Verify the Bearer token matches the configured agent token."""
    import secrets

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth format")
    token = authorization[7:]
    expected = agent_settings.AGENT_TOKEN or ""
    if not expected or len(token) != len(expected) or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return token


AuthDep = Annotated[str, Depends(verify_agent_token)]


# --- Schemas ---


class VMCreateRequest(BaseModel):
    vm_id: str
    task_id: str


class VMProvisionRequest(BaseModel):
    vm_id: str
    domain_name: str
    vcpus: int
    ram_mb: int
    disk_size_gb: int
    disk_format: str = "qcow2"
    template_path: str | None = None
    iso_path: str | None = None
    virtio_iso_path: str | None = None
    boot_from_iso: bool = False
    os_profile: str = "linux"
    mac_address: str
    bridge: str = "br0"
    guest_ip: str | None = None  # allocated IPv4 for DHCP host reservation (Windows/ISO)
    secondary_interfaces: list[dict] = Field(default_factory=list)
    cloud_init_userdata: dict | None = None
    cloud_init_metadata: dict | None = None
    cloud_init_network: dict | None = None
    storage_base_path: str | None = None  # Custom storage pool path
    start_after_create: bool = True


class VMActionResponse(BaseModel):
    status: str
    message: str
    data: dict | None = None


class GuestPasswordResetRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    new_password: str = Field(..., min_length=1, max_length=256)
    enable_password_ssh: bool = True


class RefreshVNCMappingRequest(BaseModel):
    vm_id: str


class AttachVirtIORequest(BaseModel):
    iso_path: str | None = None


# --- Health ---


@app.get("/health")
async def health():
    """Agent health check with system metrics."""
    return {
        "status": "healthy",
        "cpu_percent": psutil.cpu_percent(),
        "memory": {
            "total_mb": psutil.virtual_memory().total // (1024 * 1024),
            "available_mb": psutil.virtual_memory().available // (1024 * 1024),
            "percent": psutil.virtual_memory().percent,
        },
        "disk": {
            "total_gb": psutil.disk_usage("/").total // (1024 ** 3),
            "free_gb": psutil.disk_usage("/").free // (1024 ** 3),
            "percent": psutil.disk_usage("/").percent,
        },
    }


# --- Cloud image inspection ---


class ImageInspectRequest(BaseModel):
    file_path: str


@app.post("/api/v1/images/inspect")
async def inspect_cloud_image(body: ImageInspectRequest, token: AuthDep):
    """Detect guest CPU architecture and verify host compatibility."""
    path = os.path.realpath(body.file_path)
    images_root = os.path.realpath(agent_settings.CLOUD_IMAGES_PATH)
    if not (path == images_root or path.startswith(images_root + os.sep)):
        raise HTTPException(status_code=400, detail="Path must be under cloud images directory")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Image file not found")

    try:
        return storage_svc.inspect_image_for_host(path)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- VM Provisioning ---


@app.post("/api/v1/vms/provision", response_model=VMActionResponse)
async def provision_vm(
    body: VMProvisionRequest,
    token: AuthDep,
):
    """
    Full VM provisioning workflow:
    1. Clone base image OR create empty disk (ISO mode)
    2. Create Cloud-Init seed
    3. Define libvirt domain
    4. Optionally start VM (start_after_create, default true)
    """
    try:
        from services.path_security import assert_safe_id, resolve_under_allowlist

        assert_safe_id(body.vm_id, field="vm_id")
        assert_safe_id(body.domain_name, field="domain_name")

        # Compatibility shim:
        # If older clients/workers still send an ISO via template_path,
        # force ISO install mode so the VM boots from CD-ROM.
        effective_iso_path = body.iso_path
        effective_boot_from_iso = body.boot_from_iso
        effective_virtio_iso_path = body.virtio_iso_path
        if (
            not effective_boot_from_iso
            and not effective_iso_path
            and body.template_path
            and body.template_path.lower().endswith(".iso")
        ):
            effective_boot_from_iso = True
            effective_iso_path = body.template_path

        if body.storage_base_path:
            body.storage_base_path = str(
                resolve_under_allowlist(body.storage_base_path, field="storage_base_path")
            )
        if body.template_path:
            body.template_path = str(
                resolve_under_allowlist(body.template_path, field="template_path")
            )
        if effective_iso_path:
            effective_iso_path = str(
                resolve_under_allowlist(effective_iso_path, field="iso_path")
            )
        if effective_virtio_iso_path:
            effective_virtio_iso_path = str(
                resolve_under_allowlist(effective_virtio_iso_path, field="virtio_iso_path")
            )

        if (
            effective_boot_from_iso
            and (body.os_profile or "").lower() == "windows"
            and not effective_virtio_iso_path
        ):
            effective_virtio_iso_path = _find_latest_virtio_iso()
            if effective_virtio_iso_path:
                effective_virtio_iso_path = str(
                    resolve_under_allowlist(effective_virtio_iso_path, field="virtio_iso_path")
                )

        # Step 1: Clone base image or create empty disk for ISO install
        if effective_boot_from_iso:
            if not effective_iso_path:
                raise RuntimeError("ISO mode requires iso_path")
            disk_path = storage_svc.create_volume(
                vm_id=body.vm_id,
                disk_name="disk",
                size_gb=body.disk_size_gb,
                disk_format=body.disk_format,
                storage_base_path=body.storage_base_path,
            )
        else:
            if not body.template_path:
                raise RuntimeError("Cloud image mode requires template_path")
            disk_path = storage_svc.clone_image(
                source_path=body.template_path,
                vm_id=body.vm_id,
                disk_size_gb=body.disk_size_gb,
                disk_format=body.disk_format,
                storage_base_path=body.storage_base_path,
            )

        # Step 2: Create Cloud-Init seed (Linux only; Windows ISO installs skip this)
        seed_path = None
        is_windows = (body.os_profile or "").lower() == "windows"
        if (
            not is_windows
            and body.cloud_init_userdata
            and body.cloud_init_metadata
        ):
            seed_path = cloudinit_svc.create_seed_image(
                vm_id=body.vm_id,
                userdata=body.cloud_init_userdata,
                metadata=body.cloud_init_metadata,
                network_config=body.cloud_init_network,
            )

        # Step 3: Define libvirt domain
        domain_uuid = libvirt_svc.define_vm(
            domain_name=body.domain_name,
            vm_id=body.vm_id,
            vcpus=body.vcpus,
            ram_mb=body.ram_mb,
            disk_path=disk_path,
            seed_path=seed_path,
            mac_address=body.mac_address,
            bridge=body.bridge,
            secondary_interfaces=body.secondary_interfaces,
            iso_path=effective_iso_path,
            virtio_iso_path=effective_virtio_iso_path,
            boot_from_iso=effective_boot_from_iso,
            os_profile=body.os_profile or "linux",
        )

        # ISO guests have no cloud-init networking: reserve DHCP lease for the allocated IP.
        if effective_boot_from_iso and body.guest_ip and body.bridge.startswith("network:"):
            libvirt_svc.upsert_dhcp_host(
                network_name=body.bridge.split(":", 1)[1],
                mac_address=body.mac_address,
                ip_address=body.guest_ip,
                hostname=body.domain_name,
            )

        # Step 4: Optionally start VM
        started = False
        if body.start_after_create:
            # Keep ISO attached for the first installation boot.
            libvirt_svc.start_vm(body.domain_name, skip_iso_autoeject=True)
            started = True

        # Get VNC port (available once defined; useful after later start)
        vnc_port = libvirt_svc.get_vm_vnc_port(body.domain_name)

        # Save VNC mapping for WebSocket proxy
        if vnc_port:
            _save_vnc_mapping(body.vm_id, body.domain_name, vnc_port)

        return VMActionResponse(
            status="success",
            message=(
                f"VM {body.domain_name} successfully created and started"
                if started
                else f"VM {body.domain_name} successfully created (not started)"
            ),
            data={
                "domain_uuid": domain_uuid,
                "disk_path": disk_path,
                "seed_path": seed_path,
                "vnc_port": vnc_port,
                "started": started,
            },
        )

    except Exception as e:
        # Cleanup on failure
        try:
            storage_svc.delete_vm_storage(body.vm_id)
            cloudinit_svc.cleanup_seed(body.vm_id)
        except Exception:
            pass

        raise HTTPException(
            status_code=500,
            detail=f"VM provisioning failed: {str(e)}",
        )


# --- VM Lifecycle ---


@app.post("/api/v1/vms/{domain_name}/start", response_model=VMActionResponse)
async def start_vm(domain_name: str, token: AuthDep):
    """Start a VM."""
    try:
        libvirt_svc.start_vm(domain_name)
        vnc_port = libvirt_svc.get_vm_vnc_port(domain_name)

        # Update VNC mapping
        mappings = _load_vnc_mappings()
        for vm_id, entry in mappings.items():
            if entry.get("domain_name") == domain_name and vnc_port:
                _save_vnc_mapping(vm_id, domain_name, vnc_port)
                break

        return VMActionResponse(
            status="success",
            message=f"VM {domain_name} started",
            data={"vnc_port": vnc_port},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/vms/{domain_name}/stop", response_model=VMActionResponse)
async def stop_vm(domain_name: str, token: AuthDep):
    """Gracefully stop a VM."""
    try:
        libvirt_svc.stop_vm(domain_name)
        return VMActionResponse(
            status="success",
            message=f"VM {domain_name} is shutting down",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/vms/{domain_name}/force-stop", response_model=VMActionResponse)
async def force_stop_vm(domain_name: str, token: AuthDep):
    """Force stop a VM."""
    try:
        libvirt_svc.force_stop_vm(domain_name)
        return VMActionResponse(
            status="success",
            message=f"VM {domain_name} force stopped",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/vms/{domain_name}/reboot", response_model=VMActionResponse)
async def reboot_vm(domain_name: str, token: AuthDep):
    """Reboot a VM."""
    try:
        libvirt_svc.reboot_vm(domain_name)
        return VMActionResponse(
            status="success",
            message=f"VM {domain_name} is restarting",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/vms/{domain_name}/eject-installer", response_model=VMActionResponse)
async def eject_installer_media(domain_name: str, token: AuthDep):
    """Eject installer ISO(s) and switch boot order to disk."""
    try:
        libvirt_svc.eject_installer_media(domain_name)
        return VMActionResponse(
            status="success",
            message=f"Installer media ejected for VM {domain_name}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/vms/{domain_name}/attach-virtio", response_model=VMActionResponse)
async def attach_virtio_media(domain_name: str, body: AttachVirtIORequest, token: AuthDep):
    """Attach VirtIO ISO to a stopped VM (auto-detect path if omitted)."""
    try:
        from services.path_security import assert_safe_id, resolve_under_allowlist

        assert_safe_id(domain_name, field="domain_name")
        iso_path = body.iso_path or _find_latest_virtio_iso()
        if not iso_path:
            raise HTTPException(status_code=404, detail="No VirtIO ISO found in images directory")
        iso_path = str(resolve_under_allowlist(iso_path, field="iso_path"))
        target_dev = libvirt_svc.attach_cdrom_iso(domain_name, iso_path)
        return VMActionResponse(
            status="success",
            message=f"VirtIO ISO attached on {target_dev}",
            data={"iso_path": iso_path, "target_dev": target_dev},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/vms/{domain_name}", response_model=VMActionResponse)
async def delete_vm(domain_name: str, token: AuthDep, vm_id: str = Query(...)):
    """Delete a VM: undefine domain, delete storage, cleanup cloud-init."""
    try:
        # Best-effort: drop DHCP host reservation if this domain was on a libvirt network.
        try:
            domain = libvirt_svc.conn.lookupByName(domain_name)
            xml = domain.XMLDesc(0)
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
            for iface in root.findall("./devices/interface"):
                mac_el = iface.find("mac")
                src = iface.find("source")
                mac = mac_el.get("address") if mac_el is not None else None
                net_name = src.get("network") if src is not None else None
                if mac and net_name:
                    libvirt_svc.remove_dhcp_host(network_name=net_name, mac_address=mac)
        except Exception as e:
            print(f"[Delete] DHCP host cleanup skipped: {e}")

        libvirt_svc.delete_vm(domain_name)
        storage_svc.delete_vm_storage(vm_id)
        cloudinit_svc.cleanup_seed(vm_id)
        _remove_vm_firewall_profile(vm_id)

        # Remove VNC mapping
        _remove_vnc_mapping(vm_id)

        return VMActionResponse(
            status="success",
            message=f"VM {domain_name} and all data deleted",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/vms/{domain_name}/status")
async def get_vm_status(domain_name: str, token: AuthDep):
    """Get VM status from libvirt."""
    try:
        state = libvirt_svc.get_vm_state(domain_name)
        return state
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/vms", response_model=list[dict])
async def list_vms(token: AuthDep):
    """List all domains on this host."""
    return libvirt_svc.list_domains()


# --- WebSocket VNC Proxy ─────────────────────────────────────────────────────


def _verify_console_token(token: str) -> str | None:
    """
    Verify a console JWT token and return the VM ID.
    Returns None if the token is invalid (falls back to legacy VM-ID mode).
    """
    try:
        from jose import jwt as jose_jwt

        # Try config, then env var
        jwt_secret = agent_settings.JWT_SECRET_KEY or os.environ.get("JWT_SECRET_KEY", "")
        if not jwt_secret:
            print("[VNC Proxy] WARNING: JWT_SECRET_KEY not configured – cannot verify console tokens")
            return None

        payload = jose_jwt.decode(token, jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "console":
            return None
        return payload.get("vm_id")
    except ImportError:
        print("[VNC Proxy] WARNING: python-jose not installed – cannot verify console tokens")
        return None
    except Exception as e:
        print(f"[VNC Proxy] JWT verification failed: {e}")
        return None


@app.websocket("/websockify")
async def websocket_vnc_proxy(websocket: WebSocket, token: str = "", domain: str = ""):
    """
    WebSocket-to-TCP proxy for noVNC console access.

    Requires a short-lived JWT console token. The optional `domain` query
    parameter is only accepted when it matches the mapped domain for that VM.
    """
    if not token:
        await websocket.close(code=1008, reason="Token required")
        return

    vm_id = _verify_console_token(token)
    if not vm_id:
        await websocket.close(code=1008, reason="Invalid console token")
        return

    lookup_id = vm_id
    print(f"[VNC Proxy] JWT console token verified for VM {vm_id[:8]}...")

    mapping = _get_vnc_mapping(lookup_id) or {}
    vnc_port = mapping.get("vnc_port")
    mapped_domain = mapping.get("domain_name") or ""

    # Optional domain hint from the panel — must match the mapped domain.
    if domain:
        if mapped_domain and domain != mapped_domain:
            await websocket.close(code=1008, reason="Domain mismatch")
            return
        if not vnc_port:
            try:
                # Only resolve via domain when mapping is missing AND domain matches
                # the conventional vm-<idprefix> form derived from the JWT vm_id.
                expected_prefix = f"vm-{lookup_id.replace('-', '')[:12]}"
                alt_prefix = f"vm-{lookup_id[:8]}"
                if domain not in (expected_prefix, alt_prefix) and not (
                    domain.startswith("vm-") and lookup_id.replace("-", "").startswith(domain[3:])
                ):
                    await websocket.close(code=1008, reason="Unauthorized domain")
                    return
                vnc_port = libvirt_svc.get_vm_vnc_port(domain)
                if vnc_port:
                    _save_vnc_mapping(lookup_id, domain, vnc_port)
                    mapped_domain = domain
                    print(f"[VNC Proxy] Resolved VNC via verified domain: {domain} -> port {vnc_port}")
            except Exception as e:
                print(f"[VNC Proxy] Provided-domain lookup failed: {e}")

    if not vnc_port and mapped_domain:
        try:
            vnc_port = libvirt_svc.get_vm_vnc_port(mapped_domain)
        except Exception as e:
            print(f"[VNC Proxy] Mapped-domain lookup failed: {e}")

    if not vnc_port:
        print(f"[VNC Proxy] No VNC port found for VM {lookup_id[:8] if lookup_id else 'empty'}")
        await websocket.close(code=1008, reason="Invalid token or VM not found")
        return

    # Accept the WebSocket connection with binary subprotocol (required by noVNC)
    await websocket.accept(subprotocol="binary")

    # Connect to VNC on localhost
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", vnc_port)
    except Exception as e:
        print(f"[VNC Proxy] Failed to connect to VNC port {vnc_port}: {e}")
        await websocket.close(code=1011, reason=f"VNC connection failed")
        return

    print(f"[VNC Proxy] Connected to VNC port {vnc_port} for token {token[:8]}...")

    async def ws_to_vnc():
        """Forward WebSocket messages to VNC."""
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            writer.close()

    async def vnc_to_ws():
        """Forward VNC data to WebSocket."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass

    # Run both directions concurrently
    done, pending = await asyncio.wait(
        [asyncio.create_task(ws_to_vnc()), asyncio.create_task(vnc_to_ws())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Clean up
    for task in pending:
        task.cancel()

    try:
        await websocket.close()
    except Exception:
        pass

    print(f"[VNC Proxy] Disconnected from VNC port {vnc_port}")


# --- Network Info ---


@app.get("/api/v1/networks")
async def get_available_networks(token: AuthDep):
    """Get available network bridges and libvirt virtual networks on the host."""
    try:
        networks = libvirt_svc.get_available_networks()
        return {
            "status": "success",
            "data": networks,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- NAT Network Management ---


def _resolve_dhcp_range(
    network_cidr: str,
    gateway: str,
    dhcp_start: str | None = None,
    dhcp_end: str | None = None,
) -> tuple[str, str]:
    """Resolve DHCP start/end within a CIDR, excluding the gateway."""
    import ipaddress

    net = ipaddress.ip_network(network_cidr, strict=False)
    gw = ipaddress.ip_address(gateway)
    hosts = [h for h in net.hosts() if h != gw]
    if not hosts:
        raise ValueError("Network has no usable host addresses for DHCP")

    start = ipaddress.ip_address(dhcp_start) if dhcp_start else hosts[0]
    end = ipaddress.ip_address(dhcp_end) if dhcp_end else hosts[-1]

    if start not in net or end not in net:
        raise ValueError("DHCP range must be inside the network CIDR")
    if start == gw or end == gw:
        raise ValueError("DHCP range must not include the gateway")
    if int(start) > int(end):
        raise ValueError("dhcp_start must be <= dhcp_end")
    return str(start), str(end)


def _build_libvirt_network_xml(
    *,
    name: str,
    gateway: str,
    netmask: str,
    mode: str,
    enable_dhcp: bool,
    dhcp_start: str | None,
    dhcp_end: str | None,
) -> str:
    """Build NAT or isolated libvirt network XML."""
    dhcp_block = ""
    if enable_dhcp and dhcp_start and dhcp_end:
        dhcp_block = f"""
    <dhcp>
      <range start='{dhcp_start}' end='{dhcp_end}'/>
    </dhcp>"""

    if mode == "nat":
        return f"""<network>
  <name>{name}</name>
  <forward mode='nat'>
    <nat>
      <port start='1024' end='65535'/>
    </nat>
  </forward>
  <ip address='{gateway}' netmask='{netmask}'>{dhcp_block}
  </ip>
</network>"""

    # isolated / internal
    return f"""<network>
  <name>{name}</name>
  <ip address='{gateway}' netmask='{netmask}'>{dhcp_block}
  </ip>
</network>"""


class NATNetworkRequest(BaseModel):
    name: str  # e.g. "qemu-nat-internal"
    network: str  # CIDR e.g. "10.10.1.0/24"
    gateway: str  # e.g. "10.10.1.1"
    enable_dhcp: bool = True
    dhcp_start: str | None = None
    dhcp_end: str | None = None


@app.post("/api/v1/networks/create-nat")
async def create_nat_network(body: NATNetworkRequest, token: AuthDep):
    """
    Create a libvirt NAT network with SNAT and optional DHCP.

    Linux guests can still use Cloud-Init static IPs. Windows/ISO guests rely on
    DHCP (optionally with MAC→IP host reservations) for internet access.
    """
    import ipaddress
    import subprocess

    try:
        net = ipaddress.ip_network(body.network, strict=False)
        gw = ipaddress.ip_address(body.gateway)
        netmask = str(net.netmask)
        dhcp_start = dhcp_end = None
        if body.enable_dhcp:
            dhcp_start, dhcp_end = _resolve_dhcp_range(
                body.network, body.gateway, body.dhcp_start, body.dhcp_end
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid network configuration: {e}")

    net_name = body.name

    # Check if network already exists
    try:
        existing = libvirt_svc.conn.networkLookupByName(net_name)
        if existing:
            # Already exists - just ensure it's active
            if not existing.isActive():
                existing.create()
            if not existing.autostart():
                existing.setAutostart(1)
            bridge_name = _get_network_bridge(existing)
            return {
                "status": "success",
                "message": f"Network '{net_name}' already exists",
                "bridge": bridge_name,
                "network_name": net_name,
                "dhcp_start": dhcp_start,
                "dhcp_end": dhcp_end,
            }
    except Exception:
        pass  # Doesn't exist, create it

    network_xml = _build_libvirt_network_xml(
        name=net_name,
        gateway=str(gw),
        netmask=netmask,
        mode="nat",
        enable_dhcp=body.enable_dhcp,
        dhcp_start=dhcp_start,
        dhcp_end=dhcp_end,
    )

    try:
        network = libvirt_svc.conn.networkDefineXML(network_xml)
        if network is None:
            raise RuntimeError("networkDefineXML returned None")

        network.setAutostart(1)
        network.create()

        bridge_name = _get_network_bridge(network)

        # Enable IP forwarding (should already be enabled for KVM)
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True)

        return {
            "status": "success",
            "message": f"NAT network '{net_name}' created",
            "bridge": bridge_name,
            "network_name": net_name,
            "dhcp_enabled": body.enable_dhcp,
            "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network creation failed: {e}")


class InternalNetworkRequest(BaseModel):
    name: str  # e.g. "internal-db"
    network: str  # CIDR e.g. "172.16.0.0/24"
    gateway: str  # e.g. "172.16.0.1"
    enable_dhcp: bool = True  # Optional DHCP for secondary NICs
    dhcp_start: str | None = None
    dhcp_end: str | None = None


@app.post("/api/v1/networks/create-internal")
async def create_internal_network(body: InternalNetworkRequest, token: AuthDep):
    """
    Create an isolated libvirt network (VM-to-VM only, no NAT/internet).
    DHCP can be enabled or disabled.
    """
    import ipaddress

    try:
        net = ipaddress.ip_network(body.network, strict=False)
        gw = ipaddress.ip_address(body.gateway)
        netmask = str(net.netmask)
        dhcp_start = dhcp_end = None
        if body.enable_dhcp:
            dhcp_start, dhcp_end = _resolve_dhcp_range(
                body.network, body.gateway, body.dhcp_start, body.dhcp_end
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid network configuration: {e}")

    net_name = body.name

    # Check if network already exists
    try:
        existing = libvirt_svc.conn.networkLookupByName(net_name)
        if existing:
            if not existing.isActive():
                existing.create()
            if not existing.autostart():
                existing.setAutostart(1)
            bridge_name = _get_network_bridge(existing)
            return {
                "status": "success",
                "message": f"Internal network '{net_name}' already exists",
                "bridge": bridge_name,
                "network_name": net_name,
                "dhcp_start": dhcp_start,
                "dhcp_end": dhcp_end,
            }
    except Exception:
        pass

    network_xml = _build_libvirt_network_xml(
        name=net_name,
        gateway=str(gw),
        netmask=netmask,
        mode="isolated",
        enable_dhcp=body.enable_dhcp,
        dhcp_start=dhcp_start,
        dhcp_end=dhcp_end,
    )

    try:
        network = libvirt_svc.conn.networkDefineXML(network_xml)
        if network is None:
            raise RuntimeError("networkDefineXML returned None")

        network.setAutostart(1)
        network.create()

        bridge_name = _get_network_bridge(network)

        return {
            "status": "success",
            "message": f"Internal network '{net_name}' created",
            "bridge": bridge_name,
            "network_name": net_name,
            "dhcp_enabled": body.enable_dhcp,
            "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network creation failed: {e}")


class UpdateNetworkDHCPRequest(BaseModel):
    network: str  # CIDR
    gateway: str
    mode: str = "nat"  # nat | isolated
    enable_dhcp: bool = True
    dhcp_start: str | None = None
    dhcp_end: str | None = None


@app.put("/api/v1/networks/{network_name}/dhcp")
async def update_network_dhcp(network_name: str, body: UpdateNetworkDHCPRequest, token: AuthDep):
    """
    Update DHCP settings on an existing libvirt NAT/internal network.

    Recreates the network definition (brief interrupt for attached VMs).
    """
    import ipaddress

    mode = body.mode if body.mode in ("nat", "isolated") else "nat"
    try:
        net = ipaddress.ip_network(body.network, strict=False)
        gw = ipaddress.ip_address(body.gateway)
        netmask = str(net.netmask)
        dhcp_start = dhcp_end = None
        if body.enable_dhcp:
            dhcp_start, dhcp_end = _resolve_dhcp_range(
                body.network, body.gateway, body.dhcp_start, body.dhcp_end
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid network configuration: {e}")

    try:
        existing = libvirt_svc.conn.networkLookupByName(network_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Network '{network_name}' not found")

    was_active = bool(existing.isActive())
    autostart = bool(existing.autostart())
    try:
        if was_active:
            existing.destroy()
        existing.undefine()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to tear down network: {e}")

    network_xml = _build_libvirt_network_xml(
        name=network_name,
        gateway=str(gw),
        netmask=netmask,
        mode=mode,
        enable_dhcp=body.enable_dhcp,
        dhcp_start=dhcp_start,
        dhcp_end=dhcp_end,
    )
    try:
        network = libvirt_svc.conn.networkDefineXML(network_xml)
        if network is None:
            raise RuntimeError("networkDefineXML returned None")
        if autostart:
            network.setAutostart(1)
        if was_active:
            network.create()
        return {
            "status": "success",
            "message": f"DHCP updated for '{network_name}'",
            "bridge": _get_network_bridge(network),
            "dhcp_enabled": body.enable_dhcp,
            "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network DHCP update failed: {e}")


class AddNICRequest(BaseModel):
    mac_address: str
    network_name: str  # libvirt network name (e.g. "internal-db")


@app.post("/api/v1/vms/{domain_name}/add-nic")
async def add_nic_to_vm(domain_name: str, body: AddNICRequest, token: AuthDep):
    """Add a secondary NIC to a stopped VM."""
    import xml.etree.ElementTree as ET

    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    # VM must be shut off
    if dom.info()[0] != 5:
        raise HTTPException(status_code=400, detail="VM must be stopped")

    # Verify the network exists
    try:
        net = libvirt_svc.conn.networkLookupByName(body.network_name)
        if not net.isActive():
            net.create()
    except Exception:
        raise HTTPException(status_code=404, detail=f"Network '{body.network_name}' not found")

    # Parse current XML and check if NIC with this MAC already exists
    xml_desc = dom.XMLDesc()
    root = ET.fromstring(xml_desc)
    devices = root.find("devices")
    if devices is None:
        raise HTTPException(status_code=500, detail="VM XML has no <devices> section")

    for iface in root.findall(".//interface"):
        mac_el = iface.find("mac")
        if mac_el is not None and mac_el.get("address") == body.mac_address:
            return {"status": "unchanged", "message": "NIC with this MAC already exists"}

    # Add new interface element
    new_iface = ET.SubElement(devices, "interface", type="network")
    ET.SubElement(new_iface, "mac", address=body.mac_address)
    ET.SubElement(new_iface, "source", network=body.network_name)
    ET.SubElement(new_iface, "model", type="virtio")

    # Redefine domain
    new_xml = ET.tostring(root, encoding="unicode")
    try:
        libvirt_svc.conn.defineXML(new_xml)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VM redefinition failed: {e}")

    return {
        "status": "success",
        "message": f"NIC added: {body.mac_address} → {body.network_name}",
    }


@app.post("/api/v1/vms/{domain_name}/remove-nic")
async def remove_nic_from_vm(domain_name: str, body: AddNICRequest, token: AuthDep):
    """Remove a secondary NIC from a stopped VM by MAC address."""
    import xml.etree.ElementTree as ET

    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    if dom.info()[0] != 5:
        raise HTTPException(status_code=400, detail="VM must be stopped")

    xml_desc = dom.XMLDesc()
    root = ET.fromstring(xml_desc)
    devices = root.find("devices")

    removed = False
    for iface in root.findall(".//interface"):
        mac_el = iface.find("mac")
        if mac_el is not None and mac_el.get("address") == body.mac_address:
            devices.remove(iface)
            removed = True
            break

    if not removed:
        raise HTTPException(status_code=404, detail=f"NIC with MAC {body.mac_address} not found")

    new_xml = ET.tostring(root, encoding="unicode")
    try:
        libvirt_svc.conn.defineXML(new_xml)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"VM redefinition failed: {e}")

    return {
        "status": "success",
        "message": f"NIC removed: {body.mac_address}",
    }


def _get_network_bridge(network) -> str:
    """Extract bridge name from a libvirt network."""
    import xml.etree.ElementTree as ET
    xml_str = network.XMLDesc()
    root = ET.fromstring(xml_str)
    bridge_el = root.find("bridge")
    if bridge_el is not None:
        return bridge_el.get("name", "")
    return ""


@app.delete("/api/v1/networks/{network_name}")
async def delete_nat_network(network_name: str, token: AuthDep):
    """Delete a libvirt NAT network and clean up iptables rules."""
    import subprocess

    try:
        network = libvirt_svc.conn.networkLookupByName(network_name)

        # Remove any port-forward rules for this network's subnet
        bridge = _get_network_bridge(network)

        if network.isActive():
            network.destroy()
        network.undefine()

        return {
            "status": "success",
            "message": f"Network '{network_name}' deleted",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Network deletion failed: {e}")


# --- Port Forwarding (DNAT) ---


PORTFWD_FILE = "/var/lib/qemu-interface/port-forwards.json"


def _load_port_forwards() -> list[dict]:
    """Load port forward rules from disk."""
    if os.path.exists(PORTFWD_FILE):
        try:
            with open(PORTFWD_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_port_forwards(rules: list[dict]):
    """Save port forward rules to disk."""
    os.makedirs(os.path.dirname(PORTFWD_FILE), exist_ok=True)
    with open(PORTFWD_FILE, "w") as f:
        json.dump(rules, f, indent=2)


class PortForwardRequest(BaseModel):
    rule_id: str
    protocol: str = "tcp"  # tcp or udp
    external_port: int
    internal_ip: str
    internal_port: int


@app.post("/api/v1/networks/port-forward/add")
async def add_port_forward(body: PortForwardRequest, token: AuthDep):
    """Add a port forwarding rule (DNAT + FORWARD)."""
    import subprocess

    proto = body.protocol
    ext_port = body.external_port
    int_ip = body.internal_ip
    int_port = body.internal_port

    try:
        # Add DNAT rule: incoming traffic on ext_port -> internal_ip:internal_port
        subprocess.run([
            "iptables", "-t", "nat", "-A", "PREROUTING",
            "-p", proto, "--dport", str(ext_port),
            "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
        ], check=True, capture_output=True, text=True)

        # Add FORWARD rule to allow the forwarded traffic
        subprocess.run([
            "iptables", "-I", "FORWARD",
            "-p", proto, "-d", int_ip, "--dport", str(int_port),
            "-j", "ACCEPT",
        ], check=True, capture_output=True, text=True)

        # Persist rule
        rules = _load_port_forwards()
        rules.append({
            "rule_id": body.rule_id,
            "protocol": proto,
            "external_port": ext_port,
            "internal_ip": int_ip,
            "internal_port": int_port,
        })
        _save_port_forwards(rules)

        # Save iptables rules persistently
        _persist_iptables()

        return {
            "status": "success",
            "message": f"Port-Forward {proto.upper()}:{ext_port} -> {int_ip}:{int_port} created",
        }
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"iptables error: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/networks/port-forward/remove")
async def remove_port_forward(body: PortForwardRequest, token: AuthDep):
    """Remove a port forwarding rule."""
    import subprocess

    proto = body.protocol
    ext_port = body.external_port
    int_ip = body.internal_ip
    int_port = body.internal_port

    try:
        # Remove DNAT rule
        subprocess.run([
            "iptables", "-t", "nat", "-D", "PREROUTING",
            "-p", proto, "--dport", str(ext_port),
            "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
        ], capture_output=True, text=True)

        # Remove FORWARD rule
        subprocess.run([
            "iptables", "-D", "FORWARD",
            "-p", proto, "-d", int_ip, "--dport", str(int_port),
            "-j", "ACCEPT",
        ], capture_output=True, text=True)

        # Remove from persisted rules
        rules = _load_port_forwards()
        rules = [r for r in rules if r.get("rule_id") != body.rule_id]
        _save_port_forwards(rules)

        _persist_iptables()

        return {
            "status": "success",
            "message": f"Port-Forward {proto.upper()}:{ext_port} removed",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/networks/port-forward/restore")
async def restore_port_forwards(token: AuthDep):
    """Restore all port forwarding rules from persisted state (called on boot)."""
    import subprocess

    rules = _load_port_forwards()
    restored = 0
    errors = []

    for rule in rules:
        try:
            proto = rule["protocol"]
            ext_port = rule["external_port"]
            int_ip = rule["internal_ip"]
            int_port = rule["internal_port"]

            # Check if rule already exists (avoid duplicates)
            check = subprocess.run([
                "iptables", "-t", "nat", "-C", "PREROUTING",
                "-p", proto, "--dport", str(ext_port),
                "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
            ], capture_output=True)

            if check.returncode != 0:
                # Rule doesn't exist, add it
                subprocess.run([
                    "iptables", "-t", "nat", "-A", "PREROUTING",
                    "-p", proto, "--dport", str(ext_port),
                    "-j", "DNAT", "--to-destination", f"{int_ip}:{int_port}",
                ], check=True, capture_output=True)
                subprocess.run([
                    "iptables", "-I", "FORWARD",
                    "-p", proto, "-d", int_ip, "--dport", str(int_port),
                    "-j", "ACCEPT",
                ], check=True, capture_output=True)

            restored += 1
        except Exception as e:
            errors.append(f"Rule {rule.get('rule_id', '?')}: {e}")

    return {
        "status": "success",
        "message": f"{restored} port-forward rules restored",
        "errors": errors,
    }


def _persist_iptables():
    """Save current iptables rules to survive reboots."""
    import subprocess
    try:
        subprocess.run(
            "iptables-save > /etc/iptables/rules.v4",
            shell=True, capture_output=True, text=True,
        )
    except Exception:
        # Fallback: try netfilter-persistent
        try:
            subprocess.run(
                ["netfilter-persistent", "save"],
                capture_output=True, text=True,
            )
        except Exception:
            pass


# --- Egress Filtering (Anti-Portscan) ---

EGRESS_CONFIG_FILE = "/etc/qemu-interface/egress-rules.json"

DEFAULT_EGRESS_RULES = {
    # Default: outbound is open unless explicitly restricted.
    "enabled": False,
    "rate_limit": {"connections_per_sec": 20, "burst": 40},
    "allowed_ports": [
        {"port": 53, "protocol": "udp", "description": "DNS"},
        {"port": 53, "protocol": "tcp", "description": "DNS"},
        {"port": 80, "protocol": "tcp", "description": "HTTP"},
        {"port": 443, "protocol": "tcp", "description": "HTTPS"},
        {"port": 22, "protocol": "tcp", "description": "SSH"},
        {"port": 25, "protocol": "tcp", "description": "SMTP"},
        {"port": 465, "protocol": "tcp", "description": "SMTPS"},
        {"port": 587, "protocol": "tcp", "description": "SMTP Submission"},
        {"port": 143, "protocol": "tcp", "description": "IMAP"},
        {"port": 993, "protocol": "tcp", "description": "IMAPS"},
        {"port": 110, "protocol": "tcp", "description": "POP3"},
        {"port": 995, "protocol": "tcp", "description": "POP3S"},
        {"port": 123, "protocol": "udp", "description": "NTP"},
        {"port": 21, "protocol": "tcp", "description": "FTP"},
        {"port": 990, "protocol": "tcp", "description": "FTPS"},
        {"port": 3306, "protocol": "tcp", "description": "MySQL"},
        {"port": 5432, "protocol": "tcp", "description": "PostgreSQL"},
        {"port": 6379, "protocol": "tcp", "description": "Redis"},
        {"port": 8080, "protocol": "tcp", "description": "HTTP Alt"},
        {"port": 8443, "protocol": "tcp", "description": "HTTPS Alt"},
    ],
}


def _load_egress_config() -> dict:
    """Load egress filtering configuration from disk."""
    if os.path.exists(EGRESS_CONFIG_FILE):
        try:
            with open(EGRESS_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_EGRESS_RULES.copy()


def _save_egress_config(config: dict):
    """Save egress filtering configuration to disk."""
    os.makedirs(os.path.dirname(EGRESS_CONFIG_FILE), exist_ok=True)
    with open(EGRESS_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _ensure_egress_filtering():
    """
    Ensure the VM_EGRESS iptables chain exists with proper rules.
    Called at startup and when rules are updated.
    """
    import subprocess

    config = _load_egress_config()
    if not config.get("enabled", False):
        _disable_egress_filtering()
        print("[Egress] Egress filtering is disabled in config")
        return

    try:
        # Create chain (ignore error if exists)
        subprocess.run(["iptables", "-N", "VM_EGRESS"], capture_output=True)
        # Flush existing rules in chain
        subprocess.run(["iptables", "-F", "VM_EGRESS"], capture_output=True, check=True)

        # Add allowed port rules
        for rule in config.get("allowed_ports", []):
            port = str(rule["port"])
            proto = rule.get("protocol", "tcp")
            subprocess.run([
                "iptables", "-A", "VM_EGRESS",
                "-p", proto, "--dport", port, "-j", "ACCEPT",
            ], capture_output=True, check=True)

        # Allow ICMP (ping)
        subprocess.run([
            "iptables", "-A", "VM_EGRESS", "-p", "icmp", "-j", "ACCEPT",
        ], capture_output=True, check=True)

        # Allow established connections
        subprocess.run([
            "iptables", "-A", "VM_EGRESS",
            "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT",
        ], capture_output=True, check=True)

        # Rate-limit new connections (anti-scan)
        rate = config.get("rate_limit", {})
        limit = f"{rate.get('connections_per_sec', 20)}/sec"
        burst = str(rate.get("burst", 40))
        subprocess.run([
            "iptables", "-A", "VM_EGRESS",
            "-m", "state", "--state", "NEW",
            "-m", "limit", "--limit", limit, "--limit-burst", burst,
            "-j", "ACCEPT",
        ], capture_output=True, check=True)

        # Log blocked traffic
        subprocess.run([
            "iptables", "-A", "VM_EGRESS",
            "-m", "limit", "--limit", "5/min",
            "-j", "LOG", "--log-prefix", "VM_EGRESS_BLOCKED: ", "--log-level", "warning",
        ], capture_output=True, check=True)

        # Drop everything else
        subprocess.run([
            "iptables", "-A", "VM_EGRESS", "-j", "DROP",
        ], capture_output=True, check=True)

        # Hook into FORWARD chain for VM traffic going to public internet
        # Remove old hooks first (idempotent)
        for subnet in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]:
            subprocess.run([
                "iptables", "-D", "FORWARD", "-s", subnet, "!", "-d", subnet, "-j", "VM_EGRESS",
            ], capture_output=True)

        for subnet in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]:
            subprocess.run([
                "iptables", "-I", "FORWARD", "-s", subnet, "!", "-d", subnet, "-j", "VM_EGRESS",
            ], capture_output=True, check=True)

        # Allow VM-to-VM internal traffic
        for subnet in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]:
            # Remove old rule first
            subprocess.run([
                "iptables", "-D", "FORWARD", "-s", subnet, "-d", subnet, "-j", "ACCEPT",
            ], capture_output=True)
            subprocess.run([
                "iptables", "-I", "FORWARD", "-s", subnet, "-d", subnet, "-j", "ACCEPT",
            ], capture_output=True, check=True)

        _persist_iptables()
        print("[Egress] VM egress filtering active – blocked ports: RDP(3389), SMB(445), Telnet(23), etc.")

    except subprocess.CalledProcessError as e:
        print(f"[Egress] Warning: Failed to setup egress rules: {e}")
    except Exception as e:
        print(f"[Egress] Warning: Unexpected error: {e}")


def _disable_egress_filtering():
    """Disable VM egress filtering by flushing chain and removing hooks."""
    import subprocess
    subprocess.run(["iptables", "-F", "VM_EGRESS"], capture_output=True)
    for subnet in ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]:
        subprocess.run([
            "iptables", "-D", "FORWARD", "-s", subnet, "!", "-d", subnet, "-j", "VM_EGRESS",
        ], capture_output=True)
    _persist_iptables()


# --- Egress Filtering API ---


class EgressPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class EgressConfigUpdate(BaseModel):
    enabled: bool | None = None
    allowed_ports: list[EgressPortRule] | None = None
    rate_limit_per_sec: int | None = None
    rate_limit_burst: int | None = None


@app.get("/api/v1/security/egress-rules")
async def get_egress_rules(token: AuthDep):
    """Get current VM egress filtering configuration."""
    config = _load_egress_config()
    # Check if chain exists
    import subprocess
    chain_exists = subprocess.run(
        ["iptables", "-L", "VM_EGRESS", "-n"],
        capture_output=True, text=True,
    ).returncode == 0

    return {
        "config": config,
        "chain_active": chain_exists,
    }


@app.put("/api/v1/security/egress-rules")
async def update_egress_rules(body: EgressConfigUpdate, token: AuthDep):
    """Update VM egress filtering rules. Changes apply immediately."""
    config = _load_egress_config()

    if body.enabled is not None:
        config["enabled"] = body.enabled

    if body.allowed_ports is not None:
        config["allowed_ports"] = [
            {"port": r.port, "protocol": r.protocol, "description": r.description}
            for r in body.allowed_ports
        ]

    if body.rate_limit_per_sec is not None or body.rate_limit_burst is not None:
        rate = config.get("rate_limit", {"connections_per_sec": 20, "burst": 40})
        if body.rate_limit_per_sec is not None:
            rate["connections_per_sec"] = body.rate_limit_per_sec
        if body.rate_limit_burst is not None:
            rate["burst"] = body.rate_limit_burst
        config["rate_limit"] = rate

    _save_egress_config(config)

    if config.get("enabled", True):
        _ensure_egress_filtering()
        return {"status": "success", "message": "Egress rules updated and applied"}
    else:
        _disable_egress_filtering()
        return {"status": "success", "message": "Egress filtering disabled"}


# --- Ingress Filtering (Public VM Protection) ---

INGRESS_CONFIG_FILE = "/etc/qemu-interface/ingress-rules.json"

DEFAULT_INGRESS_RULES = {
    # Default: only SSH/HTTP/HTTPS for inbound traffic to bridged VMs.
    "enabled": True,
    "interfaces": ["br0"],
    "allowed_ports": [
        {"port": 22, "protocol": "tcp", "description": "SSH"},
        {"port": 80, "protocol": "tcp", "description": "HTTP"},
        {"port": 443, "protocol": "tcp", "description": "HTTPS"},
    ],
}


def _load_ingress_config() -> dict:
    """Load ingress filtering configuration from disk."""
    if os.path.exists(INGRESS_CONFIG_FILE):
        try:
            with open(INGRESS_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_INGRESS_RULES.copy()


def _save_ingress_config(config: dict):
    """Save ingress filtering configuration to disk."""
    os.makedirs(os.path.dirname(INGRESS_CONFIG_FILE), exist_ok=True)
    with open(INGRESS_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _disable_ingress_filtering():
    """Disable VM ingress filtering by flushing chain and removing hooks."""
    import subprocess
    config = _load_ingress_config()
    interfaces = config.get("interfaces") or ["br0"]

    subprocess.run(["iptables", "-F", "VM_INGRESS"], capture_output=True)
    for iface in interfaces:
        subprocess.run([
            "iptables", "-D", "FORWARD", "-i", iface,
            "-m", "conntrack", "--ctstate", "NEW", "-j", "VM_INGRESS",
        ], capture_output=True)
    _persist_iptables()


def _ensure_ingress_filtering():
    """
    Ensure the VM_INGRESS iptables chain exists with proper rules.
    Protects VMs with public IPs behind host bridge interfaces (e.g. br0).
    """
    import subprocess

    config = _load_ingress_config()
    if not config.get("enabled", True):
        _disable_ingress_filtering()
        print("[Ingress] Ingress filtering is disabled in config")
        return

    interfaces = config.get("interfaces") or ["br0"]

    try:
        subprocess.run(["iptables", "-N", "VM_INGRESS"], capture_output=True)
        subprocess.run(["iptables", "-F", "VM_INGRESS"], capture_output=True, check=True)

        # Allow already established flows
        subprocess.run([
            "iptables", "-A", "VM_INGRESS",
            "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT",
        ], capture_output=True, check=True)

        # Allow ping
        subprocess.run([
            "iptables", "-A", "VM_INGRESS", "-p", "icmp", "-j", "ACCEPT",
        ], capture_output=True, check=True)

        # Allow configured inbound ports
        for rule in config.get("allowed_ports", []):
            port = str(rule["port"])
            proto = rule.get("protocol", "tcp")
            subprocess.run([
                "iptables", "-A", "VM_INGRESS",
                "-p", proto, "--dport", port, "-j", "ACCEPT",
            ], capture_output=True, check=True)

        # Log and drop everything else
        subprocess.run([
            "iptables", "-A", "VM_INGRESS",
            "-m", "limit", "--limit", "5/min",
            "-j", "LOG", "--log-prefix", "VM_INGRESS_BLOCKED: ", "--log-level", "warning",
        ], capture_output=True, check=True)
        subprocess.run(["iptables", "-A", "VM_INGRESS", "-j", "DROP"], capture_output=True, check=True)

        # Re-hook FORWARD for NEW packets arriving on selected bridge interfaces
        for iface in interfaces:
            subprocess.run([
                "iptables", "-D", "FORWARD", "-i", iface,
                "-m", "conntrack", "--ctstate", "NEW", "-j", "VM_INGRESS",
            ], capture_output=True)
            subprocess.run([
                "iptables", "-I", "FORWARD", "-i", iface,
                "-m", "conntrack", "--ctstate", "NEW", "-j", "VM_INGRESS",
            ], capture_output=True, check=True)

        _persist_iptables()
        print("[Ingress] VM ingress filtering active – allowed ports: 22,80,443 (default)")

    except subprocess.CalledProcessError as e:
        print(f"[Ingress] Warning: Failed to setup ingress rules: {e}")
    except Exception as e:
        print(f"[Ingress] Warning: Unexpected error: {e}")


class IngressPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class IngressConfigUpdate(BaseModel):
    enabled: bool | None = None
    allowed_ports: list[IngressPortRule] | None = None
    interfaces: list[str] | None = None


@app.get("/api/v1/security/ingress-rules")
async def get_ingress_rules(token: AuthDep):
    """Get current VM ingress filtering configuration."""
    config = _load_ingress_config()
    import subprocess
    chain_exists = subprocess.run(
        ["iptables", "-L", "VM_INGRESS", "-n"],
        capture_output=True, text=True,
    ).returncode == 0
    return {
        "config": config,
        "chain_active": chain_exists,
    }


@app.put("/api/v1/security/ingress-rules")
async def update_ingress_rules(body: IngressConfigUpdate, token: AuthDep):
    """Update VM ingress filtering rules. Changes apply immediately."""
    config = _load_ingress_config()
    if body.enabled is not None:
        config["enabled"] = body.enabled
    if body.allowed_ports is not None:
        config["allowed_ports"] = [
            {"port": r.port, "protocol": r.protocol, "description": r.description}
            for r in body.allowed_ports
        ]
    if body.interfaces is not None:
        config["interfaces"] = body.interfaces

    _save_ingress_config(config)
    if config.get("enabled", True):
        _ensure_ingress_filtering()
        return {"status": "success", "message": "Ingress rules updated and applied"}
    _disable_ingress_filtering()
    return {"status": "success", "message": "Ingress filtering disabled"}


@app.post("/api/v1/security/ingress-rules/add-port")
async def add_ingress_port(body: IngressPortRule, token: AuthDep):
    """Add a single allowed port to ingress rules."""
    config = _load_ingress_config()
    ports = config.get("allowed_ports", [])
    for p in ports:
        if p["port"] == body.port and p["protocol"] == body.protocol:
            return {"status": "exists", "message": f"Port {body.protocol.upper()}:{body.port} already allowed"}
    ports.append({"port": body.port, "protocol": body.protocol, "description": body.description})
    config["allowed_ports"] = ports
    _save_ingress_config(config)
    _ensure_ingress_filtering()
    return {"status": "success", "message": f"Port {body.protocol.upper()}:{body.port} added to allowed ingress"}


@app.delete("/api/v1/security/ingress-rules/remove-port")
async def remove_ingress_port(port: int, protocol: str = "tcp", token: str = Depends(verify_agent_token)):
    """Remove an allowed port from ingress rules."""
    config = _load_ingress_config()
    ports = config.get("allowed_ports", [])
    original_len = len(ports)
    ports = [p for p in ports if not (p["port"] == port and p["protocol"] == protocol)]
    if len(ports) == original_len:
        raise HTTPException(status_code=404, detail=f"Port {protocol.upper()}:{port} not in allowed list")
    config["allowed_ports"] = ports
    _save_ingress_config(config)
    _ensure_ingress_filtering()
    return {"status": "success", "message": f"Port {protocol.upper()}:{port} removed from allowed ingress"}


@app.post("/api/v1/security/egress-rules/add-port")
async def add_egress_port(body: EgressPortRule, token: AuthDep):
    """Add a single allowed port to egress rules."""
    config = _load_egress_config()
    ports = config.get("allowed_ports", [])

    # Check for duplicate
    for p in ports:
        if p["port"] == body.port and p["protocol"] == body.protocol:
            return {"status": "exists", "message": f"Port {body.protocol.upper()}:{body.port} already allowed"}

    ports.append({"port": body.port, "protocol": body.protocol, "description": body.description})
    config["allowed_ports"] = ports
    _save_egress_config(config)
    _ensure_egress_filtering()

    return {"status": "success", "message": f"Port {body.protocol.upper()}:{body.port} added to allowed egress"}


@app.delete("/api/v1/security/egress-rules/remove-port")
async def remove_egress_port(port: int, protocol: str = "tcp", token: str = Depends(verify_agent_token)):
    """Remove an allowed port from egress rules."""
    config = _load_egress_config()
    ports = config.get("allowed_ports", [])
    original_len = len(ports)
    ports = [p for p in ports if not (p["port"] == port and p["protocol"] == protocol)]

    if len(ports) == original_len:
        raise HTTPException(status_code=404, detail=f"Port {protocol.upper()}:{port} not in allowed list")

    config["allowed_ports"] = ports
    _save_egress_config(config)
    _ensure_egress_filtering()

    return {"status": "success", "message": f"Port {protocol.upper()}:{port} removed from allowed egress"}


@app.get("/api/v1/security/egress-log")
async def get_egress_blocked_log(lines: int = 50, token: str = Depends(verify_agent_token)):
    """Get recent blocked egress attempts from kernel log."""
    import subprocess
    try:
        result = subprocess.run(
            f"journalctl -k --no-pager -n 500 | grep 'VM_EGRESS_BLOCKED' | tail -n {lines}",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        blocked = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            blocked.append(line.strip())
        return {"blocked_entries": blocked, "count": len(blocked)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read egress log: {e}")


# --- Per-VM Firewall Profiles ---

VM_FIREWALL_CONFIG_FILE = "/etc/qemu-interface/vm-firewall-profiles.json"

DEFAULT_VM_FIREWALL_PROFILE = {
    "inbound": {
        "enabled": True,
        "allowed_ports": [
            {"port": 22, "protocol": "tcp", "description": "SSH"},
            {"port": 80, "protocol": "tcp", "description": "HTTP"},
            {"port": 443, "protocol": "tcp", "description": "HTTPS"},
        ],
    },
    "outbound": {
        "enabled": False,
        "allowed_ports": [],
    },
}


class VMFirewallPortRule(BaseModel):
    port: int
    protocol: str = "tcp"
    description: str = ""


class VMFirewallDirection(BaseModel):
    enabled: bool = True
    allowed_ports: list[VMFirewallPortRule] = []


class VMFirewallProfileUpdate(BaseModel):
    vm_ip: str
    bridge: str = "br0"
    inbound: VMFirewallDirection | None = None
    outbound: VMFirewallDirection | None = None


def _load_vm_firewall_profiles() -> dict:
    if os.path.exists(VM_FIREWALL_CONFIG_FILE):
        try:
            with open(VM_FIREWALL_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_vm_firewall_profiles(data: dict):
    os.makedirs(os.path.dirname(VM_FIREWALL_CONFIG_FILE), exist_ok=True)
    with open(VM_FIREWALL_CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _vm_chain_names(vm_id: str) -> tuple[str, str]:
    suffix = hashlib.sha1(vm_id.encode()).hexdigest()[:10]
    return f"QFWI_{suffix}", f"QFWO_{suffix}"


def _run_iptables(args: list[str], check: bool = True):
    import subprocess
    return subprocess.run(["iptables", *args], capture_output=True, text=True, check=check)


def _ensure_chain(name: str):
    _run_iptables(["-N", name], check=False)
    _run_iptables(["-F", name], check=False)


def _apply_vm_firewall_profile(vm_id: str, profile: dict):
    vm_ip = (profile or {}).get("vm_ip")
    bridge = (profile or {}).get("bridge") or "br0"
    inbound = (profile or {}).get("inbound", {})
    outbound = (profile or {}).get("outbound", {})
    if not vm_ip:
        return

    in_chain, out_chain = _vm_chain_names(vm_id)
    suffix = hashlib.sha1(vm_id.encode()).hexdigest()[:10]

    # Remove old hooks first (idempotent)
    _run_iptables(["-D", "FORWARD", "-i", bridge, "-d", vm_ip, "-j", in_chain], check=False)
    _run_iptables(["-D", "FORWARD", "-s", vm_ip, "-j", out_chain], check=False)

    # Inbound chain
    _ensure_chain(in_chain)
    if inbound.get("enabled", True):
        _run_iptables(["-A", in_chain, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"])
        _run_iptables(["-A", in_chain, "-p", "icmp", "-j", "ACCEPT"])
        for rule in inbound.get("allowed_ports", []):
            _run_iptables([
                "-A", in_chain,
                "-p", str(rule.get("protocol", "tcp")),
                "--dport", str(rule.get("port")),
                "-j", "ACCEPT",
            ])
        _run_iptables([
            "-A", in_chain,
            "-m", "limit", "--limit", "10/min",
            "-j", "LOG",
            "--log-prefix", f"QFW-IN-{suffix}: ",
            "--log-level", "warning",
        ], check=False)
        _run_iptables(["-A", in_chain, "-j", "DROP"])
        _run_iptables(["-I", "FORWARD", "-i", bridge, "-d", vm_ip, "-j", in_chain])

    # Outbound chain
    _ensure_chain(out_chain)
    if outbound.get("enabled", False):
        _run_iptables(["-A", out_chain, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"])
        _run_iptables(["-A", out_chain, "-p", "icmp", "-j", "ACCEPT"])
        for rule in outbound.get("allowed_ports", []):
            _run_iptables([
                "-A", out_chain,
                "-p", str(rule.get("protocol", "tcp")),
                "--dport", str(rule.get("port")),
                "-j", "ACCEPT",
            ])
        _run_iptables([
            "-A", out_chain,
            "-m", "limit", "--limit", "10/min",
            "-j", "LOG",
            "--log-prefix", f"QFW-OUT-{suffix}: ",
            "--log-level", "warning",
        ], check=False)
        _run_iptables(["-A", out_chain, "-j", "DROP"])
        _run_iptables(["-I", "FORWARD", "-s", vm_ip, "-j", out_chain])

    _persist_iptables()


def _apply_all_vm_firewall_profiles():
    profiles = _load_vm_firewall_profiles()
    for vm_id, profile in profiles.items():
        try:
            _apply_vm_firewall_profile(vm_id, profile)
        except Exception as e:
            print(f"[VM-FW] Warning: failed to apply profile for {vm_id}: {e}")


@app.get("/api/v1/security/vm-firewall/{vm_id}")
async def get_vm_firewall_profile(vm_id: str, token: AuthDep):
    profiles = _load_vm_firewall_profiles()
    profile = profiles.get(vm_id)
    if not profile:
        return {"vm_id": vm_id, "profile": DEFAULT_VM_FIREWALL_PROFILE, "exists": False}
    return {"vm_id": vm_id, "profile": profile, "exists": True}


@app.put("/api/v1/security/vm-firewall/{vm_id}")
async def update_vm_firewall_profile(vm_id: str, body: VMFirewallProfileUpdate, token: AuthDep):
    import ipaddress
    import re as _re
    from services.path_security import assert_safe_id

    assert_safe_id(vm_id, field="vm_id")
    try:
        vm_ip = str(ipaddress.ip_address((body.vm_ip or "").split("/")[0]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid vm_ip") from exc
    bridge = body.bridge or "br0"
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", bridge or ""):
        raise HTTPException(status_code=400, detail="Invalid bridge")

    profiles = _load_vm_firewall_profiles()
    current = profiles.get(vm_id, {})
    merged = {
        "vm_ip": vm_ip,
        "bridge": bridge or current.get("bridge", "br0"),
        "inbound": body.inbound.model_dump() if body.inbound else current.get("inbound", DEFAULT_VM_FIREWALL_PROFILE["inbound"]),
        "outbound": body.outbound.model_dump() if body.outbound else current.get("outbound", DEFAULT_VM_FIREWALL_PROFILE["outbound"]),
    }
    profiles[vm_id] = merged
    _save_vm_firewall_profiles(profiles)
    _apply_vm_firewall_profile(vm_id, merged)
    return {"status": "success", "vm_id": vm_id, "profile": merged}


@app.get("/api/v1/security/vm-firewall/{vm_id}/log")
async def get_vm_firewall_log(vm_id: str, token: AuthDep, lines: int = 50):
    """Return recent kernel log lines for this VM's per-VM firewall drops."""
    from services.path_security import assert_safe_id
    import ipaddress

    try:
        assert_safe_id(vm_id, field="vm_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    suffix = hashlib.sha1(vm_id.encode()).hexdigest()[:10]
    profiles = _load_vm_firewall_profiles()
    raw_ip = (profiles.get(vm_id) or {}).get("vm_ip", "") or ""
    vm_ip = ""
    if raw_ip:
        try:
            vm_ip = str(ipaddress.ip_address(raw_ip.split("/")[0]))
        except ValueError:
            vm_ip = ""
    try:
        result = subprocess.run(
            ["journalctl", "-k", "--no-pager", "-n", "2000"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        import re as _re
        tag_re = _re.compile(rf"QFW-(IN|OUT)-{_re.escape(suffix)}")
        limit = max(1, min(lines, 200))
        matched: list[str] = []
        for line in result.stdout.splitlines():
            if tag_re.search(line) or (vm_ip and vm_ip in line):
                matched.append(line)
        entries = matched[-limit:]
        return {
            "vm_id": vm_id,
            "vm_ip": vm_ip,
            "blocked_entries": entries,
            "count": len(entries),
            "log_hint": "journalctl -k | grep QFW-IN- or QFW-OUT- on the host",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read VM firewall log: {e}")


def _remove_vm_firewall_profile(vm_id: str) -> bool:
    """Remove per-VM firewall profile and iptables hooks. Returns True if a profile existed."""
    profiles = _load_vm_firewall_profiles()
    profile = profiles.pop(vm_id, None)
    if not profile:
        return False

    _save_vm_firewall_profiles(profiles)

    vm_ip = profile.get("vm_ip")
    bridge = profile.get("bridge", "br0")
    in_chain, out_chain = _vm_chain_names(vm_id)
    _run_iptables(["-D", "FORWARD", "-i", bridge, "-d", vm_ip, "-j", in_chain], check=False)
    _run_iptables(["-D", "FORWARD", "-s", vm_ip, "-j", out_chain], check=False)
    _run_iptables(["-F", in_chain], check=False)
    _run_iptables(["-F", out_chain], check=False)
    _run_iptables(["-X", in_chain], check=False)
    _run_iptables(["-X", out_chain], check=False)
    _persist_iptables()
    return True


@app.delete("/api/v1/security/vm-firewall/{vm_id}")
async def delete_vm_firewall_profile(vm_id: str, token: AuthDep):
    _remove_vm_firewall_profile(vm_id)
    return {"status": "success", "vm_id": vm_id}


# --- IP Blacklist / Fail2Ban ---

BLACKLIST_FILE = "/etc/qemu-interface/ip-blacklist.json"


def _load_blacklist() -> dict:
    """Load IP blacklist from disk."""
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"blocked_ips": []}


def _save_blacklist(data: dict):
    """Save IP blacklist to disk."""
    os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _ensure_blacklist_chain():
    """Ensure the IP_BLACKLIST iptables chain exists."""
    import subprocess
    subprocess.run(["iptables", "-N", "IP_BLACKLIST"], capture_output=True)
    # Hook into INPUT if not already
    check = subprocess.run(
        ["iptables", "-C", "INPUT", "-j", "IP_BLACKLIST"],
        capture_output=True,
    )
    if check.returncode != 0:
        subprocess.run(["iptables", "-I", "INPUT", "1", "-j", "IP_BLACKLIST"], capture_output=True)


class IPBlacklistEntry(BaseModel):
    ip: str
    reason: str = ""
    permanent: bool = True


@app.get("/api/v1/security/blacklist")
async def get_blacklist(token: str = Depends(verify_agent_token)):
    """Get all blacklisted IPs (manual + fail2ban)."""
    import subprocess
    from datetime import datetime

    # Manual blacklist
    manual = _load_blacklist().get("blocked_ips", [])

    # Fail2ban bans
    f2b_bans = []
    try:
        result = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True, text=True, timeout=10,
        )
        jails = []
        for line in result.stdout.split("\n"):
            if "Jail list:" in line:
                jails = [j.strip() for j in line.split(":")[-1].split(",") if j.strip()]

        for jail in jails:
            jr = subprocess.run(
                ["fail2ban-client", "status", jail],
                capture_output=True, text=True, timeout=10,
            )
            for line in jr.stdout.split("\n"):
                if "Banned IP list:" in line:
                    ips = [ip.strip() for ip in line.split(":")[-1].split() if ip.strip()]
                    for ip in ips:
                        f2b_bans.append({
                            "ip": ip,
                            "jail": jail,
                            "source": "fail2ban",
                        })
    except Exception:
        pass

    # Fail2ban stats
    f2b_stats = {}
    try:
        result = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True, text=True, timeout=10,
        )
        f2b_stats["running"] = result.returncode == 0
        f2b_stats["jails"] = []
        jails = []
        for line in result.stdout.split("\n"):
            if "Jail list:" in line:
                jails = [j.strip() for j in line.split(":")[-1].split(",") if j.strip()]

        for jail in jails:
            jr = subprocess.run(
                ["fail2ban-client", "status", jail],
                capture_output=True, text=True, timeout=10,
            )
            jail_info = {"name": jail, "currently_banned": 0, "total_banned": 0, "currently_failed": 0}
            for line in jr.stdout.split("\n"):
                line = line.strip()
                if "Currently banned:" in line:
                    try:
                        jail_info["currently_banned"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
                elif "Total banned:" in line:
                    try:
                        jail_info["total_banned"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
                elif "Currently failed:" in line:
                    try:
                        jail_info["currently_failed"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
            f2b_stats["jails"].append(jail_info)
    except Exception:
        f2b_stats["running"] = False

    return {
        "manual_bans": manual,
        "fail2ban_bans": f2b_bans,
        "fail2ban_stats": f2b_stats,
        "total": len(manual) + len(f2b_bans),
    }


@app.post("/api/v1/security/blacklist/ban")
async def ban_ip(body: IPBlacklistEntry, token: str = Depends(verify_agent_token)):
    """Manually ban an IP address."""
    import subprocess

    ip = body.ip.strip()
    if not ip:
        raise HTTPException(status_code=400, detail="IP address required")

    _ensure_blacklist_chain()

    # Check if already banned
    check = subprocess.run(
        ["iptables", "-C", "IP_BLACKLIST", "-s", ip, "-j", "DROP"],
        capture_output=True,
    )
    if check.returncode == 0:
        return {"status": "exists", "message": f"{ip} is already banned"}

    # Add iptables rule
    subprocess.run(
        ["iptables", "-I", "IP_BLACKLIST", "1", "-s", ip, "-j", "DROP"],
        capture_output=True, check=True,
    )

    # Save to persistent blacklist
    data = _load_blacklist()
    from datetime import datetime, timezone
    data["blocked_ips"].append({
        "ip": ip,
        "reason": body.reason,
        "permanent": body.permanent,
        "banned_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_blacklist(data)
    _persist_iptables()

    return {"status": "success", "message": f"{ip} banned"}


@app.post("/api/v1/security/blacklist/unban")
async def unban_ip(body: IPBlacklistEntry, token: str = Depends(verify_agent_token)):
    """Remove an IP from the blacklist."""
    import subprocess

    ip = body.ip.strip()

    # Remove from iptables
    subprocess.run(
        ["iptables", "-D", "IP_BLACKLIST", "-s", ip, "-j", "DROP"],
        capture_output=True,
    )

    # Remove from persistent blacklist
    data = _load_blacklist()
    data["blocked_ips"] = [e for e in data["blocked_ips"] if e.get("ip") != ip]
    _save_blacklist(data)

    # Also unban in fail2ban (all jails)
    try:
        result = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.split("\n"):
            if "Jail list:" in line:
                jails = [j.strip() for j in line.split(":")[-1].split(",") if j.strip()]
                for jail in jails:
                    subprocess.run(
                        ["fail2ban-client", "set", jail, "unbanip", ip],
                        capture_output=True, timeout=10,
                    )
    except Exception:
        pass

    _persist_iptables()
    return {"status": "success", "message": f"{ip} unbanned"}


@app.get("/api/v1/security/blacklist/check/{ip}")
async def check_ip(ip: str, token: str = Depends(verify_agent_token)):
    """Check if an IP is currently banned."""
    import subprocess

    # Check iptables
    in_iptables = subprocess.run(
        ["iptables", "-C", "IP_BLACKLIST", "-s", ip, "-j", "DROP"],
        capture_output=True,
    ).returncode == 0

    # Check fail2ban
    in_fail2ban = False
    f2b_jail = None
    try:
        result = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.split("\n"):
            if "Jail list:" in line:
                jails = [j.strip() for j in line.split(":")[-1].split(",") if j.strip()]
                for jail in jails:
                    jr = subprocess.run(
                        ["fail2ban-client", "status", jail],
                        capture_output=True, text=True, timeout=10,
                    )
                    if ip in jr.stdout:
                        in_fail2ban = True
                        f2b_jail = jail
                        break
    except Exception:
        pass

    # Check manual list
    data = _load_blacklist()
    manual_entry = next((e for e in data["blocked_ips"] if e.get("ip") == ip), None)

    return {
        "ip": ip,
        "banned": in_iptables or in_fail2ban,
        "in_iptables": in_iptables,
        "in_fail2ban": in_fail2ban,
        "fail2ban_jail": f2b_jail,
        "manual_entry": manual_entry,
    }


@app.get("/api/v1/security/fail2ban/log")
async def get_fail2ban_log(lines: int = 50, token: str = Depends(verify_agent_token)):
    """Get recent fail2ban log entries."""
    import subprocess
    try:
        result = subprocess.run(
            f"journalctl -u fail2ban -n {lines} --no-pager",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        entries = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        return {"entries": entries, "count": len(entries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read fail2ban log: {e}")


QWI_INSTALL_DIR = "/opt/qemu-interface"
SYSTEM_LOG_SOURCES = ("agent", "backend", "worker", "nginx", "firewall", "fail2ban", "install")


def _read_command_output(cmd: list[str], *, timeout: int = 15) -> list[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode not in (0, 1):
        err = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(err[:500])
    return [line.rstrip() for line in result.stdout.splitlines() if line.strip()]


def _fetch_system_log_lines(source: str, lines: int, since_seconds: int | None) -> list[str]:
    if source not in SYSTEM_LOG_SOURCES:
        raise ValueError(f"Unknown log source: {source}")

    if source == "agent":
        cmd = ["journalctl", "-u", "qemu-interface-agent", "--no-pager"]
        if since_seconds:
            cmd.extend(["--since", f"{since_seconds} seconds ago"])
        else:
            cmd.extend(["-n", str(lines)])
        return _read_command_output(cmd)

    if source == "fail2ban":
        cmd = ["journalctl", "-u", "fail2ban", "--no-pager"]
        if since_seconds:
            cmd.extend(["--since", f"{since_seconds} seconds ago"])
        else:
            cmd.extend(["-n", str(lines)])
        return _read_command_output(cmd)

    if source == "firewall":
        tail = str(lines)
        cmd = [
            "bash", "-lc",
            (
                f"journalctl -k --no-pager -n 3000 | "
                f"grep -E 'VM_INGRESS_BLOCKED|VM_EGRESS_BLOCKED|QFW-' | tail -n {tail}"
            ),
        ]
        return _read_command_output(cmd)

    if source == "install":
        log_path = "/var/log/qemu-interface/install.log"
        if not os.path.exists(log_path):
            return [f"(no install log at {log_path})"]
        cmd = ["tail", "-n", str(lines), log_path]
        return _read_command_output(cmd)

    compose_dir = QWI_INSTALL_DIR
    service = source  # backend | worker | nginx
    cmd = ["bash", "-lc"]
    if since_seconds:
        compose_cmd = (
            f"cd {shlex.quote(compose_dir)} && "
            f"docker compose logs --since {since_seconds}s --no-log-prefix {service}"
        )
    else:
        compose_cmd = (
            f"cd {shlex.quote(compose_dir)} && "
            f"docker compose logs --tail {lines} --no-log-prefix {service}"
        )
    cmd.append(compose_cmd)
    out = _read_command_output(cmd, timeout=20)
    if not out:
        return [f"(no docker logs for service '{service}')"]
    return out[-lines:] if len(out) > lines else out


@app.get("/api/v1/system/logs")
async def get_system_logs(
    token: AuthDep,
    source: str = Query("agent"),
    lines: int = Query(200, ge=10, le=1000),
    since_seconds: int | None = Query(None, ge=1, le=3600),
):
    """Tail host, docker, kernel firewall, or install logs for the debug UI."""
    from datetime import datetime, timezone

    try:
        log_lines = _fetch_system_log_lines(source, lines, since_seconds)
        return {
            "source": source,
            "lines": log_lines,
            "count": len(log_lines),
            "available_sources": list(SYSTEM_LOG_SOURCES),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {e}")


FAIL2BAN_JAIL_CONF = "/etc/fail2ban/jail.d/qemu-interface.conf"


class Fail2BanConfigUpdate(BaseModel):
    jail: str
    bantime: int | None = None
    findtime: int | None = None
    maxretry: int | None = None


@app.get("/api/v1/security/fail2ban/config")
async def get_fail2ban_config(token: str = Depends(verify_agent_token)):
    """Get fail2ban jail configuration and runtime values."""
    import subprocess

    jails_config = {}
    try:
        result = subprocess.run(
            ["fail2ban-client", "status"],
            capture_output=True, text=True, timeout=10,
        )
        jail_names = []
        for line in result.stdout.split("\n"):
            if "Jail list:" in line:
                jail_names = [j.strip() for j in line.split(":")[-1].split(",") if j.strip()]

        for jail in jail_names:
            jail_info = {"name": jail}
            for prop in ["bantime", "findtime", "maxretry"]:
                pr = subprocess.run(
                    ["fail2ban-client", "get", jail, prop],
                    capture_output=True, text=True, timeout=10,
                )
                try:
                    jail_info[prop] = int(pr.stdout.strip())
                except (ValueError, AttributeError):
                    jail_info[prop] = None

            sr = subprocess.run(
                ["fail2ban-client", "status", jail],
                capture_output=True, text=True, timeout=10,
            )
            for line in sr.stdout.split("\n"):
                line = line.strip()
                if "Currently banned:" in line:
                    try:
                        jail_info["currently_banned"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
                elif "Total banned:" in line:
                    try:
                        jail_info["total_banned"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass
                elif "Currently failed:" in line:
                    try:
                        jail_info["currently_failed"] = int(line.split(":")[-1].strip())
                    except ValueError:
                        pass

            jails_config[jail] = jail_info

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read fail2ban config: {e}")

    return {"jails": jails_config}


@app.put("/api/v1/security/fail2ban/config")
async def update_fail2ban_config(body: Fail2BanConfigUpdate, token: str = Depends(verify_agent_token)):
    """Update fail2ban jail settings at runtime and persist to config file."""
    import subprocess

    jail = body.jail
    changes = []

    # Apply at runtime via fail2ban-client
    try:
        if body.bantime is not None:
            subprocess.run(
                ["fail2ban-client", "set", jail, "bantime", str(body.bantime)],
                capture_output=True, text=True, check=True, timeout=10,
            )
            changes.append(f"bantime={body.bantime}")

        if body.findtime is not None:
            subprocess.run(
                ["fail2ban-client", "set", jail, "findtime", str(body.findtime)],
                capture_output=True, text=True, check=True, timeout=10,
            )
            changes.append(f"findtime={body.findtime}")

        if body.maxretry is not None:
            subprocess.run(
                ["fail2ban-client", "set", jail, "maxretry", str(body.maxretry)],
                capture_output=True, text=True, check=True, timeout=10,
            )
            changes.append(f"maxretry={body.maxretry}")

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Failed to apply: {e.stderr}")

    # Persist to config file
    try:
        if os.path.exists(FAIL2BAN_JAIL_CONF):
            with open(FAIL2BAN_JAIL_CONF) as f:
                content = f.read()

            import re
            lines = content.split("\n")
            in_jail = False
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(f"[{jail}]"):
                    in_jail = True
                    new_lines.append(line)
                    continue
                elif stripped.startswith("[") and stripped.endswith("]") and in_jail:
                    in_jail = False

                if in_jail:
                    if body.bantime is not None and stripped.startswith("bantime"):
                        new_lines.append(f"bantime = {body.bantime}")
                        continue
                    if body.findtime is not None and stripped.startswith("findtime"):
                        new_lines.append(f"findtime = {body.findtime}")
                        continue
                    if body.maxretry is not None and stripped.startswith("maxretry"):
                        new_lines.append(f"maxretry = {body.maxretry}")
                        continue

                new_lines.append(line)

            with open(FAIL2BAN_JAIL_CONF, "w") as f:
                f.write("\n".join(new_lines))

    except Exception:
        pass  # Runtime changes already applied, config persistence is best-effort

    return {
        "status": "success",
        "message": f"Jail '{jail}' updated: {', '.join(changes)}",
        "applied_at_runtime": True,
        "persisted_to_config": True,
    }


# --- Storage ---


@app.post("/api/v1/storage/import-image")
async def import_image(
    archive_path: str,
    image_name: str,
    token: AuthDep,
):
    """Import and extract a cloud image archive (.tar.xz)."""
    try:
        from services.path_security import assert_safe_name, resolve_under_allowlist

        assert_safe_name(image_name, field="image_name")
        archive_path = str(resolve_under_allowlist(archive_path, field="archive_path"))
        extracted_path = storage_svc.extract_tar_xz(archive_path, image_name)
        info = storage_svc.get_image_info(extracted_path)
        checksum = storage_svc.compute_checksum(extracted_path)

        return {
            "status": "success",
            "file_path": extracted_path,
            "image_info": info,
            "checksum_sha256": checksum,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- SSL / Let's Encrypt ─────────────────────────────────────────────────────


SSL_CONFIG_FILE = "/opt/qemu-interface/config/ssl.json"


def _load_ssl_config() -> dict:
    """Load SSL configuration from disk."""
    if os.path.exists(SSL_CONFIG_FILE):
        try:
            with open(SSL_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"domain": "", "ssl_enabled": False, "cert_path": "", "key_path": ""}


def _save_ssl_config(config: dict):
    """Save SSL configuration to disk."""
    os.makedirs(os.path.dirname(SSL_CONFIG_FILE), exist_ok=True)
    with open(SSL_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


class SSLSetupRequest(BaseModel):
    domain: str
    email: str = ""


@app.get("/api/v1/ssl/status")
async def get_ssl_status(token: AuthDep):
    """Get current SSL/TLS configuration status."""
    config = _load_ssl_config()

    # Check if certificates actually exist
    cert_exists = False
    cert_expiry = None
    if config.get("cert_path") and os.path.exists(config["cert_path"]):
        cert_exists = True
        # Get certificate expiry using openssl
        try:
            import subprocess
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", config["cert_path"]],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Parse "notAfter=Mar 15 12:00:00 2026 GMT"
                expiry_str = result.stdout.strip().split("=", 1)[1]
                cert_expiry = expiry_str
        except Exception:
            pass

    return {
        "domain": config.get("domain", ""),
        "ssl_enabled": config.get("ssl_enabled", False),
        "cert_exists": cert_exists,
        "cert_expiry": cert_expiry,
        "cert_path": config.get("cert_path", ""),
    }


@app.post("/api/v1/ssl/setup")
async def setup_ssl(body: SSLSetupRequest, token: AuthDep):
    """
    Setup Let's Encrypt SSL certificate for the given domain.
    Includes pre-flight checks, webroot mode with standalone fallback.
    """
    import subprocess
    import socket

    domain = body.domain.strip().lower()
    email = body.email.strip()

    if not domain:
        raise HTTPException(status_code=400, detail="Domain is required")

    if " " in domain or "/" in domain:
        raise HTTPException(status_code=400, detail="Invalid domain format")

    steps = []
    warnings = []

    # --- Pre-flight 1: DNS resolution check ---
    try:
        resolved_ips = socket.getaddrinfo(domain, 80, socket.AF_INET)
        resolved_ip = resolved_ips[0][4][0] if resolved_ips else None
        steps.append(f"DNS resolved: {domain} → {resolved_ip}")
    except socket.gaierror:
        raise HTTPException(
            status_code=400,
            detail=f"DNS resolution failed: '{domain}' could not be resolved. "
                   f"Please ensure an A-record for this domain points to the server IP."
        )

    # --- Pre-flight 2: Get own public IP and compare ---
    try:
        own_ip_result = subprocess.run(
            ["curl", "-s", "-4", "--max-time", "5", "https://ifconfig.me"],
            capture_output=True, text=True, timeout=10
        )
        own_ip = own_ip_result.stdout.strip() if own_ip_result.returncode == 0 else None
        if own_ip and resolved_ip and own_ip != resolved_ip:
            warnings.append(
                f"WARNING: Domain points to {resolved_ip}, but this server's public IP is {own_ip}. "
                f"Let's Encrypt will fail if the domain does not point to this server."
            )
            steps.append(f"IP-Check: Server={own_ip}, Domain={resolved_ip} (MISMATCH)")
        elif own_ip:
            steps.append(f"IP-Check: Server={own_ip}, Domain={resolved_ip} (OK)")
    except Exception:
        steps.append("IP-Check: Could not determine public IP (skipped)")

    # --- Step 1: Ensure certbot is installed ---
    try:
        result = subprocess.run(["which", "certbot"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            subprocess.run(
                ["apt-get", "install", "-y", "certbot"],
                capture_output=True, text=True, timeout=120
            )
        steps.append("certbot available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Certbot not available: {e}")

    # --- Step 2: Create webroot directory with correct permissions ---
    webroot = "/var/lib/letsencrypt"
    os.makedirs(f"{webroot}/.well-known/acme-challenge", exist_ok=True)
    # Ensure readable by nginx container (runs as uid 101 / nginx user)
    subprocess.run(["chmod", "-R", "755", webroot], capture_output=True, timeout=5)
    steps.append("Webroot created")

    # --- Step 3: Ensure platform nginx config exists & reload ---
    try:
        sync_platform_config()
        result = subprocess.run(
            ["docker", "exec", "qemu-interface-proxy", "nginx", "-s", "reload"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            steps.append("Nginx reloaded for ACME challenge")
        else:
            subprocess.run(
                ["docker", "restart", "qemu-interface-proxy"],
                capture_output=True, text=True, timeout=30
            )
            steps.append("Nginx restarted for ACME challenge")
        import time
        time.sleep(2)
    except Exception as e:
        warnings.append(f"Nginx reload: {e}")

    # --- Step 4: Build email args ---
    email_args = ["--email", email] if email else ["--register-unsafely-without-email"]

    # --- Step 5: Pre-check – test if ACME challenge path is reachable ---
    import urllib.request
    try:
        test_token = "qemu-interface-ssl-test"
        test_path = f"{webroot}/.well-known/acme-challenge/{test_token}"
        with open(test_path, "w") as f:
            f.write("OK")
        # Ensure readable
        os.chmod(test_path, 0o644)

        try:
            test_url = f"http://{domain}/.well-known/acme-challenge/{test_token}"
            req = urllib.request.Request(test_url, method="GET")
            resp = urllib.request.urlopen(req, timeout=10)
            body = resp.read().decode().strip()
            if body == "OK":
                steps.append(f"ACME challenge path reachable")
            else:
                warnings.append(f"ACME challenge: response was '{body[:50]}' instead of 'OK'")
        except urllib.error.HTTPError as e:
            # 404 = nginx doesn't serve the directory
            raise HTTPException(
                status_code=500,
                detail=f"ACME challenge path returns HTTP {e.code}!\n\n"
                       f"The file exists on the host at:\n"
                       f"  {test_path}\n\n"
                       f"But Nginx returns {e.code} for:\n"
                       f"  http://{domain}/.well-known/acme-challenge/{test_token}\n\n"
                       f"SOLUTION: Run on the server:\n"
                       f"  docker compose restart nginx\n"
                       f"  curl -I http://localhost/.well-known/acme-challenge/{test_token}\n\n"
                       f"If 404: Check if /var/lib/letsencrypt is mounted as a volume in docker-compose.yml."
            )
        except Exception as e:
            warnings.append(f"ACME challenge pre-check: {e}")
        finally:
            try:
                os.remove(test_path)
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception:
        pass

    # --- Step 5: Run certbot (webroot mode only – no nginx stop!) ---
    certbot_cmd = [
        "certbot", "certonly",
        "--webroot", "-w", webroot,
        "-d", domain,
        "--non-interactive", "--agree-tos",
        "--keep-until-expiring",
    ] + email_args

    try:
        result = subprocess.run(certbot_cmd, capture_output=True, text=True, timeout=120)
        certbot_output = (result.stdout + "\n" + result.stderr).strip()

        if result.returncode == 0:
            steps.append("Certificate created/renewed")
        else:
            # Parse certbot output for helpful error messages
            error_lines = [l for l in certbot_output.split("\n") if l.strip() and "debug" not in l.lower()]
            short_error = "\n".join(error_lines[-8:]) if error_lines else certbot_output[-500:]

            hints = []
            output_lower = certbot_output.lower()
            if "connection refused" in output_lower or "timeout" in output_lower:
                hints.append("Port 80 does not seem reachable. Check: ufw allow 80 / iptables")
            if "unauthorized" in output_lower or "invalid response" in output_lower:
                hints.append(f"Domain '{domain}' does not respond correctly. Check the DNS A-record")
            if "dns" in output_lower and "resolve" in output_lower:
                hints.append(f"DNS issue: '{domain}' cannot be resolved")
            if "challenges have failed" in output_lower:
                hints.append("Let's Encrypt could not verify the server")
                hints.append(f"Check: dig +short {domain} → must be the server IP")
                hints.append("Check: curl -I http://" + domain + "/.well-known/acme-challenge/ → must return 200/403")

            detail = f"Certbot failed:\n{short_error}"
            if hints:
                detail += "\n\nHINTS:\n• " + "\n• ".join(hints)

            raise HTTPException(status_code=500, detail=detail)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Certbot error: {e}")

    # --- Step 6: Determine cert paths ---
    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    key_path = f"/etc/letsencrypt/live/{domain}/privkey.pem"

    if not os.path.exists(cert_path):
        raise HTTPException(
            status_code=500,
            detail=f"Certificate was created but not found at {cert_path}"
        )

    # --- Step 7: Save SSL config + write host platform nginx (not git-tracked) ---
    ssl_config = {
        "domain": domain,
        "email": email,
        "ssl_enabled": True,
        "cert_path": cert_path,
        "key_path": key_path,
    }
    _save_ssl_config(ssl_config)
    write_platform_config(ssl_config=ssl_config)
    steps.append("SSL configuration saved")
    steps.append("Nginx platform HTTPS config written")
    # Domain is defined here by the admin → lock CORS/ALLOWED_HOSTS to it
    steps.extend(lock_cors_to_panel_domain(domain))

    # --- Step 9: Reload nginx with new SSL config ---
    # First try graceful reload, then full restart as fallback
    try:
        result = subprocess.run(
            ["docker", "exec", "qemu-interface-proxy", "nginx", "-s", "reload"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            steps.append("Nginx reloaded with SSL config")
        else:
            # Reload failed (possibly because new config has ssl_certificate
            # pointing to certs that need fresh volume mounts) → full restart
            result = subprocess.run(
                ["docker", "restart", "qemu-interface-proxy"],
                capture_output=True, text=True, timeout=30
            )
            steps.append("Nginx restarted with SSL (full restart)")
    except Exception as e:
        warnings.append(f"Nginx restart warning: {e}")

    # --- Step 10: Setup auto-renewal cron ---
    try:
        cron_line = "0 3 * * * certbot renew --quiet --deploy-hook 'docker exec qemu-interface-proxy nginx -s reload'"
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing_crontab = existing.stdout if existing.returncode == 0 else ""
        if "certbot renew" not in existing_crontab:
            new_crontab = existing_crontab.rstrip() + "\n" + cron_line + "\n"
            proc = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=new_crontab)
            steps.append("Auto-renewal cron configured")
        else:
            steps.append("Auto-renewal cron already present")
    except Exception as e:
        warnings.append(f"Cron warning: {e}")

    return {
        "status": "success",
        "domain": domain,
        "ssl_enabled": True,
        "cert_path": cert_path,
        "steps": steps,
        "warnings": warnings,
    }


@app.post("/api/v1/ssl/test")
async def test_ssl_readiness(body: SSLSetupRequest, token: AuthDep):
    """Test if all prerequisites for SSL setup are met."""
    import subprocess
    import socket
    import urllib.request

    domain = body.domain.strip().lower()
    checks = []
    all_ok = True

    # 1. DNS resolution
    resolved_ip = None
    try:
        resolved_ips = socket.getaddrinfo(domain, 80, socket.AF_INET)
        resolved_ip = resolved_ips[0][4][0] if resolved_ips else None
        checks.append({"name": "DNS resolution", "status": "ok", "detail": f"{domain} → {resolved_ip}"})
    except socket.gaierror:
        checks.append({"name": "DNS resolution", "status": "error", "detail": f"'{domain}' cannot be resolved. A-record missing."})
        all_ok = False

    # 2. Public IP match
    try:
        own_ip_result = subprocess.run(
            ["curl", "-s", "-4", "--max-time", "5", "https://ifconfig.me"],
            capture_output=True, text=True, timeout=10
        )
        own_ip = own_ip_result.stdout.strip() if own_ip_result.returncode == 0 else None
        if own_ip and resolved_ip:
            if own_ip == resolved_ip:
                checks.append({"name": "IP match", "status": "ok", "detail": f"Server-IP={own_ip}, Domain-IP={resolved_ip}"})
            else:
                checks.append({"name": "IP match", "status": "error", "detail": f"Server-IP={own_ip} ≠ Domain-IP={resolved_ip}"})
                all_ok = False
        else:
            checks.append({"name": "IP match", "status": "warning", "detail": "Could not determine public IP"})
    except Exception:
        checks.append({"name": "IP match", "status": "warning", "detail": "Check skipped"})

    # 3. Port 80 reachable
    try:
        test_token = "ssl-readiness-test"
        webroot = "/var/lib/letsencrypt"
        os.makedirs(f"{webroot}/.well-known/acme-challenge", exist_ok=True)
        test_path = f"{webroot}/.well-known/acme-challenge/{test_token}"
        with open(test_path, "w") as f:
            f.write("OK")

        test_url = f"http://{domain}/.well-known/acme-challenge/{test_token}"
        req = urllib.request.Request(test_url, method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        body_text = resp.read().decode().strip()
        os.remove(test_path)

        if body_text == "OK":
            checks.append({"name": "HTTP challenge path", "status": "ok", "detail": f"{test_url} reachable"})
        else:
            checks.append({"name": "HTTP challenge path", "status": "warning", "detail": "Reachable but unexpected response"})
    except Exception as e:
        try:
            os.remove(test_path)
        except Exception:
            pass
        checks.append({"name": "HTTP challenge path", "status": "error", "detail": f"Not reachable: {e}"})
        all_ok = False

    # 4. Certbot available
    try:
        result = subprocess.run(["which", "certbot"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            checks.append({"name": "Certbot", "status": "ok", "detail": result.stdout.strip()})
        else:
            checks.append({"name": "Certbot", "status": "error", "detail": "Certbot not installed"})
            all_ok = False
    except Exception:
        checks.append({"name": "Certbot", "status": "error", "detail": "Certbot check failed"})
        all_ok = False

    return {"ready": all_ok, "checks": checks}


@app.post("/api/v1/ssl/regenerate-config")
async def regenerate_ssl_config(token: AuthDep):
    """Regenerate and reload the nginx config based on current SSL state."""
    import subprocess

    config = _load_ssl_config()
    if not config.get("ssl_enabled") or not config.get("domain"):
        raise HTTPException(status_code=400, detail="SSL is not configured")

    write_platform_config(ssl_config=config)

    # Graceful reload
    result = subprocess.run(
        ["docker", "exec", "qemu-interface-proxy", "nginx", "-s", "reload"],
        capture_output=True, text=True, timeout=15
    )

    return {
        "status": "success" if result.returncode == 0 else "warning",
        "message": "Config regenerated and Nginx reloaded" if result.returncode == 0 else f"Config written but reload failed: {result.stderr}",
    }


@app.post("/api/v1/ssl/disable")
async def disable_ssl(token: AuthDep):
    """Disable SSL and revert to HTTP-only nginx config."""
    import subprocess

    config = _load_ssl_config()
    config["ssl_enabled"] = False
    _save_ssl_config(config)
    write_platform_config(ssl_config=config)

    # Restart nginx
    try:
        subprocess.run(
            ["docker", "restart", "qemu-interface-proxy"],
            capture_output=True, text=True, timeout=30
        )
    except Exception:
        pass

    return {"status": "success", "ssl_enabled": False}




# --- Metrics ---


@app.get("/api/v1/metrics")
async def get_metrics(token: AuthDep):
    """Get host system metrics."""
    return {
        "cpu": {
            "count": psutil.cpu_count(),
            "percent": psutil.cpu_percent(percpu=True),
            "load_avg": os.getloadavg(),
        },
        "memory": {
            "total_mb": psutil.virtual_memory().total // (1024 * 1024),
            "used_mb": psutil.virtual_memory().used // (1024 * 1024),
            "available_mb": psutil.virtual_memory().available // (1024 * 1024),
            "percent": psutil.virtual_memory().percent,
        },
        "disk": {
            "total_gb": psutil.disk_usage("/").total // (1024 ** 3),
            "used_gb": psutil.disk_usage("/").used // (1024 ** 3),
            "free_gb": psutil.disk_usage("/").free // (1024 ** 3),
            "percent": psutil.disk_usage("/").percent,
        },
        "network": {
            iface: {
                "bytes_sent": stats.bytes_sent,
                "bytes_recv": stats.bytes_recv,
            }
            for iface, stats in psutil.net_io_counters(pernic=True).items()
        },
    }


# --- VM Snapshots ─────────────────────────────────────────────────────────────


class VMCloneRequest(BaseModel):
    source_domain_name: str
    new_domain_name: str
    new_vm_id: str
    mac_address: str
    bridge: str = "br0"
    cloud_init_userdata: dict
    cloud_init_metadata: dict
    cloud_init_network: dict | None = None


@app.post("/api/v1/vms/clone")
async def clone_vm(body: VMCloneRequest, token: AuthDep):
    """Clone a VM by copying its disk and defining a new domain."""
    import shutil

    try:
        # Find source domain disk
        source_dom = libvirt_svc.conn.lookupByName(body.source_domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Source domain '{body.source_domain_name}' not found")

    import xml.etree.ElementTree as ET
    src_xml = source_dom.XMLDesc()
    src_root = ET.fromstring(src_xml)

    # Find source disk path
    source_disk = None
    for disk in src_root.findall(".//disk[@type='file']"):
        source_el = disk.find("source")
        target_el = disk.find("target")
        if source_el is not None and target_el is not None:
            dev = target_el.get("dev", "")
            if dev in ("vda", "sda", "hda"):
                source_disk = source_el.get("file")
                break

    if not source_disk:
        raise HTTPException(status_code=400, detail="Source disk not found")

    from services.path_security import assert_safe_id, resolve_under_allowlist

    assert_safe_id(body.new_vm_id, field="new_vm_id")
    assert_safe_id(body.new_domain_name, field="new_domain_name")
    assert_safe_id(body.source_domain_name, field="source_domain_name")
    source_disk = str(resolve_under_allowlist(source_disk, field="source_disk"))

    # Clone disk — independent full copy (not a qcow2 backing chain).
    new_storage_dir = storage_svc.storage_path / body.new_vm_id
    new_storage_dir.mkdir(parents=True, exist_ok=True)
    resolve_under_allowlist(new_storage_dir, field="new_storage_dir")
    new_disk_path = str(new_storage_dir / "disk.qcow2")

    try:
        result = subprocess.run(
            ["qemu-img", "convert", "-U", "-O", "qcow2", source_disk, new_disk_path],
            capture_output=True, text=True, timeout=3600,
        )
        if result.returncode != 0:
            raise Exception(f"qemu-img failed: {result.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Disk clone failed: {e}")

    # Create cloud-init seed
    try:
        seed_path = cloudinit_svc.create_seed_image(
            vm_id=body.new_vm_id,
            userdata=body.cloud_init_userdata,
            metadata=body.cloud_init_metadata,
            network_config=body.cloud_init_network,
        )
    except Exception as e:
        shutil.rmtree(new_storage_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Cloud-Init failed: {e}")

    # Get source VM specs
    vcpus = int(src_root.findtext("vcpu", "1"))
    # Memory is in KiB in libvirt XML
    memory_kib = int(src_root.findtext("memory", "1048576"))
    ram_mb = memory_kib // 1024

    # Define new domain via libvirt
    try:
        domain_uuid = libvirt_svc.define_vm(
            domain_name=body.new_domain_name,
            vm_id=body.new_vm_id,
            vcpus=vcpus,
            ram_mb=ram_mb,
            disk_path=new_disk_path,
            seed_path=seed_path,
            mac_address=body.mac_address,
            bridge=body.bridge,
            secondary_interfaces=[],
            iso_path=None,
            virtio_iso_path=None,
            boot_from_iso=False,
            os_profile="linux",
            vnc_port=-1,
            vnc_websocket_port=-1,
        )
    except Exception as e:
        shutil.rmtree(new_storage_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Domain definition failed: {e}")

    return {
        "status": "success",
        "domain_name": body.new_domain_name,
        "domain_uuid": domain_uuid,
        "disk_path": new_disk_path,
    }


class SnapshotCreate(BaseModel):
    name: str
    description: str = ""


@app.get("/api/v1/vms/{domain_name}/snapshots")
async def list_snapshots(domain_name: str, token: AuthDep):
    """List all snapshots for a VM."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    snapshots = []
    for snap in dom.listAllSnapshots():
        import xml.etree.ElementTree as ET
        xml_desc = snap.getXMLDesc()
        root = ET.fromstring(xml_desc)
        name = root.findtext("name", "")
        desc = root.findtext("description", "")
        created = root.findtext("creationTime", "0")
        state_el = root.find("state")
        state = state_el.text if state_el is not None else "unknown"
        is_current = snap.isCurrent()
        snapshots.append({
            "name": name,
            "description": desc,
            "created_at": int(created),
            "state": state,
            "is_current": is_current,
        })
    # Sort newest first
    snapshots.sort(key=lambda s: s["created_at"], reverse=True)
    return snapshots


@app.post("/api/v1/vms/{domain_name}/snapshots")
async def create_snapshot(domain_name: str, body: SnapshotCreate, token: AuthDep):
    """Create a snapshot of a VM."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    # Build snapshot XML
    snap_xml = f"""<domainsnapshot>
  <name>{body.name}</name>
  <description>{body.description}</description>
</domainsnapshot>"""

    try:
        snap = dom.snapshotCreateXML(snap_xml, 0)
        return {
            "status": "success",
            "name": snap.getName(),
            "message": f"Snapshot '{body.name}' created",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot creation failed: {e}")


@app.post("/api/v1/vms/{domain_name}/snapshots/{snapshot_name}/revert")
async def revert_snapshot(domain_name: str, snapshot_name: str, token: AuthDep):
    """Revert a VM to a snapshot."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    try:
        snap = dom.snapshotLookupByName(snapshot_name)
        dom.revertToSnapshot(snap)
        return {"status": "success", "message": f"VM reverted to snapshot '{snapshot_name}'"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot restore failed: {e}")


@app.delete("/api/v1/vms/{domain_name}/snapshots/{snapshot_name}")
async def delete_snapshot(domain_name: str, snapshot_name: str, token: AuthDep):
    """Delete a snapshot."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    try:
        snap = dom.snapshotLookupByName(snapshot_name)
        snap.delete(0)
        return {"status": "success", "message": f"Snapshot '{snapshot_name}' deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snapshot deletion failed: {e}")


# --- Per-VM Metrics ───────────────────────────────────────────────────────────


@app.get("/api/v1/vms/{domain_name}/stats")
async def get_vm_stats(domain_name: str, token: AuthDep):
    """Get real-time resource usage for a single VM via libvirt."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    info = dom.info()
    # info: [state, maxMem_kb, memory_kb, nrVirtCpu, cpuTime_ns]
    state_map = {0: "nostate", 1: "running", 2: "blocked", 3: "paused",
                 4: "shutdown", 5: "shutoff", 6: "crashed", 7: "pmsuspended"}

    max_memory_kb = info[1]
    used_memory_kb = info[2]  # fallback: allocated = max (shows 100%)
    memory_percent = 100.0
    guest_agent_active = False

    # Try to get real memory stats from qemu-guest-agent via balloon driver
    try:
        mem_stats = dom.memoryStats()
        # mem_stats can contain: actual, rss, usable, available, unused,
        # last_update, disk_caches, major_fault, minor_fault, swap_in, swap_out
        if "available" in mem_stats and "unused" in mem_stats:
            # Guest agent reports actual usage
            total_kb = mem_stats.get("available", max_memory_kb)
            unused_kb = mem_stats.get("unused", 0)
            used_memory_kb = total_kb - unused_kb
            max_memory_kb = total_kb
            memory_percent = round((used_memory_kb / total_kb) * 100, 1) if total_kb > 0 else 0
            guest_agent_active = True
        elif "rss" in mem_stats:
            # Fallback: use RSS (resident set size from host perspective)
            used_memory_kb = mem_stats["rss"]
            memory_percent = round((used_memory_kb / max_memory_kb) * 100, 1) if max_memory_kb > 0 else 0
            guest_agent_active = True
    except Exception:
        # No guest agent or balloon driver – use fallback
        memory_percent = round((info[2] / info[1]) * 100, 1) if info[1] > 0 else 0

    stats = {
        "state": state_map.get(info[0], "unknown"),
        "max_memory_kb": max_memory_kb,
        "memory_kb": used_memory_kb,
        "vcpus": info[3],
        "cpu_time_ns": info[4],
        "memory_percent": memory_percent,
        "guest_agent_active": guest_agent_active,
    }

    # Try to get block stats (disk I/O)
    try:
        import xml.etree.ElementTree as ET
        xml_desc = dom.XMLDesc()
        root = ET.fromstring(xml_desc)
        disks = []
        for disk in root.findall(".//disk"):
            target = disk.find("target")
            if target is not None:
                dev = target.get("dev", "")
                try:
                    rd_req, rd_bytes, wr_req, wr_bytes, _errs = dom.blockStats(dev)
                    disks.append({
                        "device": dev,
                        "read_bytes": rd_bytes,
                        "write_bytes": wr_bytes,
                        "read_requests": rd_req,
                        "write_requests": wr_req,
                    })
                except Exception:
                    pass
        stats["disks"] = disks
    except Exception:
        stats["disks"] = []

    # Try to get network stats
    try:
        ifaces = []
        for iface in root.findall(".//interface"):
            target = iface.find("target")
            if target is not None:
                dev = target.get("dev", "")
                try:
                    net_stats = dom.interfaceStats(dev)
                    # (rx_bytes, rx_packets, rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop)
                    ifaces.append({
                        "device": dev,
                        "rx_bytes": net_stats[0],
                        "rx_packets": net_stats[1],
                        "tx_bytes": net_stats[4],
                        "tx_packets": net_stats[5],
                    })
                except Exception:
                    pass
        stats["interfaces"] = ifaces
    except Exception:
        stats["interfaces"] = []

    return stats


# --- VM Resize ────────────────────────────────────────────────────────────────


class VMResizeRequest(BaseModel):
    vcpus: int | None = None
    ram_mb: int | None = None
    disk_size_gb: int | None = None  # Only grow, never shrink


def _qga_command(domain_name: str, payload: dict, timeout: int = 15) -> dict:
    """Execute a QEMU guest-agent command via virsh and parse JSON response."""
    result = subprocess.run(
        ["virsh", "qemu-agent-command", domain_name, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or "virsh qemu-agent-command failed")
    try:
        return json.loads(result.stdout or "{}")
    except Exception as e:
        raise RuntimeError(f"invalid guest-agent response: {e}")


async def _qga_exec_shell(domain_name: str, command: str, timeout_s: int = 25) -> tuple[int, str, str]:
    """Run a shell command in guest and return (exitcode, stdout, stderr)."""
    start = _qga_command(
        domain_name,
        {
            "execute": "guest-exec",
            "arguments": {
                "path": "/bin/sh",
                "arg": ["-lc", command],
                "capture-output": True,
            },
        },
    )
    pid = start.get("return", {}).get("pid")
    if not pid:
        raise RuntimeError("guest-exec did not return pid")

    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        status = _qga_command(
            domain_name,
            {"execute": "guest-exec-status", "arguments": {"pid": pid}},
        ).get("return", {})
        if status.get("exited"):
            code = int(status.get("exitcode", 1))
            out_b64 = status.get("out-data") or ""
            err_b64 = status.get("err-data") or ""
            out = base64.b64decode(out_b64).decode("utf-8", errors="replace") if out_b64 else ""
            err = base64.b64decode(err_b64).decode("utf-8", errors="replace") if err_b64 else ""
            return code, out, err
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("guest command timeout")
        await asyncio.sleep(0.5)


@app.post("/api/v1/vms/{domain_name}/guest-password")
async def reset_guest_password_live(domain_name: str, body: GuestPasswordResetRequest, token: AuthDep):
    """Reset guest user password on a running VM via qemu-guest-agent."""
    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    state = dom.info()[0]
    if state != 1:  # 1 = running
        raise HTTPException(status_code=409, detail="VM must be running for live password reset")

    username = body.username.strip()
    if not re.match(r"^[a-z_][a-z0-9_-]*[$]?$", username):
        raise HTTPException(status_code=400, detail="Invalid username format")

    q_user = shlex.quote(username)
    q_pass = shlex.quote(body.new_password)
    commands = [
        f"id -u {q_user} >/dev/null 2>&1 || useradd -m -s /bin/bash {q_user}",
        f"echo {q_user}:{q_pass} | chpasswd",
        f"passwd -u {q_user} >/dev/null 2>&1 || true",
    ]
    if body.enable_password_ssh:
        commands.extend(
            [
                "sed -ri 's/^\\s*#?\\s*PasswordAuthentication\\s+.*/PasswordAuthentication yes/' /etc/ssh/sshd_config || true",
                "grep -q '^PasswordAuthentication yes' /etc/ssh/sshd_config || echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config",
                "systemctl restart ssh || systemctl restart sshd || service ssh restart || service sshd restart || true",
            ]
        )

    for cmd in commands:
        code, _out, err = await _qga_exec_shell(domain_name, cmd, timeout_s=30)
        if code != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Live password reset command failed (exit={code}): {(err or '').strip()[:200]}",
            )

    return {
        "status": "success",
        "message": f"Password for user '{username}' reset in running guest",
    }


@app.post("/api/v1/vms/{domain_name}/resize")
async def resize_vm(domain_name: str, body: VMResizeRequest, token: AuthDep):
    """Resize a stopped VM's vCPUs, RAM, and/or disk."""
    import xml.etree.ElementTree as ET

    try:
        dom = libvirt_svc.conn.lookupByName(domain_name)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found")

    # VM must be shut off
    state = dom.info()[0]
    if state != 5:  # 5 = shutoff
        raise HTTPException(status_code=400, detail="VM must be stopped for resize")

    changes = []
    xml_desc = dom.XMLDesc()
    root = ET.fromstring(xml_desc)

    # Update vCPUs
    if body.vcpus is not None and body.vcpus > 0:
        vcpu_el = root.find("vcpu")
        if vcpu_el is not None:
            vcpu_el.text = str(body.vcpus)
            changes.append(f"vCPUs → {body.vcpus}")

    # Update RAM (libvirt uses KiB)
    if body.ram_mb is not None and body.ram_mb >= 256:
        ram_kib = body.ram_mb * 1024
        memory_el = root.find("memory")
        if memory_el is not None:
            memory_el.text = str(ram_kib)
            memory_el.set("unit", "KiB")
        current_mem = root.find("currentMemory")
        if current_mem is not None:
            current_mem.text = str(ram_kib)
            current_mem.set("unit", "KiB")
        changes.append(f"RAM → {body.ram_mb} MB")

    # Resize disk (only grow)
    if body.disk_size_gb is not None and body.disk_size_gb > 0:
        # Find the main disk path
        disk_path = None
        for disk in root.findall(".//disk[@type='file']"):
            source_el = disk.find("source")
            target_el = disk.find("target")
            if source_el is not None and target_el is not None:
                dev = target_el.get("dev", "")
                if dev in ("vda", "sda", "hda"):
                    disk_path = source_el.get("file")
                    break

        if disk_path:
            try:
                result = subprocess.run(
                    ["qemu-img", "resize", disk_path, f"{body.disk_size_gb}G"],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    changes.append(f"Disk → {body.disk_size_gb} GB")
                else:
                    raise Exception(result.stderr.strip())
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Disk resize failed: {e}")
        else:
            raise HTTPException(status_code=400, detail="Disk not found")

    # Apply vCPU/RAM changes by redefining the domain
    if body.vcpus is not None or body.ram_mb is not None:
        new_xml = ET.tostring(root, encoding="unicode")
        try:
            libvirt_svc.conn.defineXML(new_xml)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"VM redefinition failed: {e}")

    if not changes:
        return {"status": "unchanged", "message": "No changes requested"}

    return {
        "status": "success",
        "message": f"VM updated: {', '.join(changes)}",
        "changes": changes,
    }


@app.post("/api/v1/vms/{domain_name}/refresh-vnc-mapping")
async def refresh_vnc_mapping(domain_name: str, body: RefreshVNCMappingRequest, token: AuthDep):
    """Refresh vm_id -> VNC port mapping for console websocket routing."""
    try:
        vnc_port = libvirt_svc.get_vm_vnc_port(domain_name)
        if not vnc_port:
            raise HTTPException(status_code=404, detail=f"No VNC port for domain '{domain_name}'")
        _save_vnc_mapping(body.vm_id, domain_name, vnc_port)
        return {
            "status": "success",
            "vm_id": body.vm_id,
            "domain_name": domain_name,
            "vnc_port": vnc_port,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not refresh VNC mapping: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  ADDITIONAL DISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════


class DiskCreateRequest(BaseModel):
    vm_id: str
    disk_name: str
    size_gb: int
    disk_format: str = "qcow2"
    storage_base_path: str | None = None


class DiskAttachRequest(BaseModel):
    domain_name: str
    disk_path: str
    target_dev: str = "vdb"
    disk_format: str = "qcow2"
    bus: str = "virtio"
    readonly: bool = False


class DiskDetachRequest(BaseModel):
    domain_name: str
    disk_path: str
    target_dev: str = "vdb"


class DiskResizeAPIRequest(BaseModel):
    disk_path: str
    new_size_gb: int


@app.post("/api/v1/disks/create")
async def create_additional_disk(body: DiskCreateRequest, token: AuthDep):
    """Create a new empty disk volume for a VM."""
    try:
        disk_path = storage_svc.create_volume(
            vm_id=body.vm_id,
            disk_name=body.disk_name,
            size_gb=body.size_gb,
            disk_format=body.disk_format,
            storage_base_path=body.storage_base_path,
        )
        file_size = os.path.getsize(disk_path)
        return {
            "status": "success",
            "disk_path": disk_path,
            "file_size_bytes": file_size,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Disk creation failed: {e}")


@app.post("/api/v1/disks/attach")
async def attach_disk(body: DiskAttachRequest, token: AuthDep):
    """
    Attach a disk to a VM. Supports hot-attach if VM is running.
    For stopped VMs, the disk is added to the domain XML definition.
    """
    import xml.etree.ElementTree as ET
    import libvirt as libvirt_mod

    if not os.path.exists(body.disk_path):
        raise HTTPException(status_code=404, detail="Disk file not found")

    dom = libvirt_svc._get_domain(body.domain_name)

    disk_xml = f"""<disk type='file' device='disk'>
  <driver name='qemu' type='{body.disk_format}' cache='writeback' discard='unmap'/>
  <source file='{body.disk_path}'/>
  <target dev='{body.target_dev}' bus='{body.bus}'/>
  {"<readonly/>" if body.readonly else ""}
</disk>"""

    state, _ = dom.state()
    is_running = state == libvirt_mod.VIR_DOMAIN_RUNNING

    if is_running:
        # Hot-attach to running VM
        try:
            flags = libvirt_mod.VIR_DOMAIN_AFFECT_LIVE | libvirt_mod.VIR_DOMAIN_AFFECT_CONFIG
            dom.attachDeviceFlags(disk_xml, flags)
            return {"status": "success", "hot_attach": True, "target_dev": body.target_dev}
        except libvirt_mod.libvirtError as e:
            raise HTTPException(status_code=500, detail=f"Hot-attach failed: {e}")
    else:
        # Cold-attach: modify domain XML definition
        try:
            xml_desc = dom.XMLDesc(0)
            root = ET.fromstring(xml_desc)
            devices = root.find("devices")
            disk_el = ET.fromstring(disk_xml)
            devices.append(disk_el)
            new_xml = ET.tostring(root, encoding="unicode")
            conn = libvirt_svc.conn
            conn.defineXML(new_xml)
            return {"status": "success", "hot_attach": False, "target_dev": body.target_dev}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Attach failed: {e}")


@app.post("/api/v1/disks/detach")
async def detach_disk(body: DiskDetachRequest, token: AuthDep):
    """
    Detach a disk from a VM. Supports hot-detach if VM is running.
    """
    import xml.etree.ElementTree as ET
    import libvirt as libvirt_mod

    dom = libvirt_svc._get_domain(body.domain_name)
    state, _ = dom.state()
    is_running = state == libvirt_mod.VIR_DOMAIN_RUNNING

    # Find the disk in the current XML
    xml_desc = dom.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    disk_el = None
    for disk in root.findall(".//devices/disk"):
        target = disk.find("target")
        source = disk.find("source")
        if target is not None and target.get("dev") == body.target_dev:
            if source is not None and source.get("file") == body.disk_path:
                disk_el = disk
                break

    if disk_el is None:
        raise HTTPException(status_code=404, detail=f"Disk {body.target_dev} not found in VM")

    disk_xml = ET.tostring(disk_el, encoding="unicode")

    if is_running:
        # Hot-detach from running VM
        try:
            flags = libvirt_mod.VIR_DOMAIN_AFFECT_LIVE | libvirt_mod.VIR_DOMAIN_AFFECT_CONFIG
            dom.detachDeviceFlags(disk_xml, flags)
            return {"status": "success", "hot_detach": True}
        except libvirt_mod.libvirtError as e:
            raise HTTPException(status_code=500, detail=f"Hot-detach failed: {e}")
    else:
        # Cold-detach: remove from domain XML
        try:
            devices = root.find("devices")
            devices.remove(disk_el)
            new_xml = ET.tostring(root, encoding="unicode")
            conn = libvirt_svc.conn
            conn.defineXML(new_xml)
            return {"status": "success", "hot_detach": False}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Detach failed: {e}")


@app.post("/api/v1/disks/resize")
async def resize_disk_api(body: DiskResizeAPIRequest, token: AuthDep):
    """Resize a disk (grow only). VM should be stopped for safety."""
    if not os.path.exists(body.disk_path):
        raise HTTPException(status_code=404, detail="Disk file not found")

    try:
        storage_svc.resize_volume(body.disk_path, body.new_size_gb)
        file_size = os.path.getsize(body.disk_path)
        return {
            "status": "success",
            "disk_path": body.disk_path,
            "new_size_gb": body.new_size_gb,
            "file_size_bytes": file_size,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Disk resize failed: {e}")


@app.delete("/api/v1/disks/file")
async def delete_disk_file(disk_path: str = Query(...), token: AuthDep = None):
    """Delete a disk file from the filesystem."""
    safe_path = os.path.realpath(disk_path)
    storage_base = os.path.realpath(agent_settings.STORAGE_BASE_PATH)

    if not safe_path.startswith(storage_base):
        raise HTTPException(status_code=403, detail="Cannot delete files outside storage path")

    if not os.path.exists(safe_path):
        raise HTTPException(status_code=404, detail="Disk file not found")

    try:
        os.remove(safe_path)
        return {"status": "success", "message": f"Disk deleted: {os.path.basename(safe_path)}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  BACKUP / RESTORE
# ══════════════════════════════════════════════════════════════════════════════

BACKUP_DIR = os.path.join(agent_settings.STORAGE_BASE_PATH, "backups")


class RegenerateSeedRequest(BaseModel):
    vm_id: str
    cloud_init_userdata: dict
    cloud_init_metadata: dict
    cloud_init_network: dict | None = None


@app.post("/api/v1/vms/regenerate-seed")
async def regenerate_seed(body: RegenerateSeedRequest, token: AuthDep):
    """
    Regenerate the Cloud-Init seed image for a VM.
    Used after IP or network config changes.
    """
    try:
        seed_path = cloudinit_svc.create_seed_image(
            vm_id=body.vm_id,
            userdata=body.cloud_init_userdata,
            metadata=body.cloud_init_metadata,
            network_config=body.cloud_init_network,
        )

        return {
            "status": "success",
            "seed_path": seed_path,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Seed regeneration failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  REVERSE PROXY
# ══════════════════════════════════════════════════════════════════════════════

PROXY_CONF_DIR = "/var/lib/qemu-interface/proxy-configs"


class ProxyRule(BaseModel):
    domain: str
    target_ip: str
    target_port: int = 80
    ssl_enabled: bool = False
    ssl_mode: str = "none"
    websocket_support: bool = False


class ProxyApplyRequest(BaseModel):
    rules: list[ProxyRule]


class ProxySSLRequest(BaseModel):
    domain: str


def _generate_nginx_vhost(rule: ProxyRule) -> str:
    """Generate an Nginx server block for a proxy rule."""
    ws_config = ""
    if rule.websocket_support:
        ws_config = """
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;"""

    if rule.ssl_enabled:
        return f"""# Proxy: {rule.domain} -> {rule.target_ip}:{rule.target_port}
server {{
    listen 80;
    server_name {rule.domain};

    location /.well-known/acme-challenge/ {{
        root /var/lib/letsencrypt;
    }}

    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl;
    server_name {rule.domain};

    ssl_certificate /etc/letsencrypt/live/{rule.domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{rule.domain}/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {{
        proxy_pass http://{rule.target_ip}:{rule.target_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;{ws_config}
    }}
}}
"""
    else:
        return f"""# Proxy: {rule.domain} -> {rule.target_ip}:{rule.target_port}
server {{
    listen 80;
    server_name {rule.domain};

    location /.well-known/acme-challenge/ {{
        root /var/lib/letsencrypt;
    }}

    location / {{
        proxy_pass http://{rule.target_ip}:{rule.target_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;{ws_config}
    }}
}}
"""


def _nginx_test_and_reload() -> None:
    """Run nginx -t and reload inside the proxy container. Raises on failure."""
    import subprocess

    result = subprocess.run(
        ["docker", "exec", "qemu-interface-proxy", "nginx", "-t"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "nginx -t failed").strip()
        raise RuntimeError(detail)

    reload = subprocess.run(
        ["docker", "exec", "qemu-interface-proxy", "nginx", "-s", "reload"],
        capture_output=True, text=True, timeout=15,
    )
    if reload.returncode != 0:
        detail = (reload.stderr or reload.stdout or "nginx reload failed").strip()
        raise RuntimeError(detail)


@app.post("/api/v1/proxy/apply")
async def apply_proxy_rules(body: ProxyApplyRequest, token: AuthDep):
    """Write Nginx vhost configs for all proxy rules and reload Nginx."""
    import glob as glob_mod

    os.makedirs(PROXY_CONF_DIR, exist_ok=True)

    # Remove existing proxy configs
    for f in glob_mod.glob(os.path.join(PROXY_CONF_DIR, "proxy_*.conf")):
        os.remove(f)

    # Write new configs
    written = []
    for rule in body.rules:
        safe_domain = rule.domain.replace(".", "_").replace("*", "wildcard")
        filename = f"proxy_{safe_domain}.conf"
        filepath = os.path.join(PROXY_CONF_DIR, filename)

        config = _generate_nginx_vhost(rule)
        with open(filepath, "w") as f:
            f.write(config)
        written.append(filename)

    try:
        _nginx_test_and_reload()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Nginx apply failed: {e}",
        ) from e

    return {
        "status": "success",
        "message": f"{len(written)} proxy rule(s) applied",
        "written": written,
    }


@app.post("/api/v1/proxy/ssl")
async def request_proxy_ssl(body: ProxySSLRequest, token: AuthDep):
    """Request a Let's Encrypt SSL certificate for a domain."""
    import subprocess

    domain = body.domain.strip().lower()

    # Run certbot
    try:
        result = subprocess.run(
            [
                "certbot", "certonly",
                "--webroot",
                "--webroot-path", "/var/lib/letsencrypt",
                "-d", domain,
                "--non-interactive",
                "--agree-tos",
                "--email", "admin@" + domain.split(".")[-2] + "." + domain.split(".")[-1],
                "--keep-until-expiring",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"Certbot failed: {result.stderr.strip()}",
            }

        return {
            "status": "success",
            "message": f"SSL certificate issued for {domain}",
            "domain": domain,
        }

    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="certbot is not installed. Install with: apt install certbot",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Certbot timed out")


class BackupCreateRequest(BaseModel):
    domain_name: str
    backup_id: str
    backup_name: str


class BackupRestoreRequest(BaseModel):
    domain_name: str
    backup_file_path: str


def _primary_disk_file_and_target(dom) -> tuple[str, str]:
    """Return (disk_file_path, target_dev) for the first file-backed disk."""
    import xml.etree.ElementTree as ET

    xml_str = dom.XMLDesc(0)
    root = ET.fromstring(xml_str)
    for disk in root.findall(".//devices/disk"):
        if disk.get("device") != "disk":
            continue
        source = disk.find("source")
        target = disk.find("target")
        if source is None or target is None:
            continue
        path = source.get("file")
        dev = target.get("dev")
        if path and dev:
            return path, dev
    raise HTTPException(status_code=400, detail="No file-backed disk found for this VM")


def _qemu_img_convert_compressed(src: str, dest: str) -> None:
    import subprocess

    result = subprocess.run(
        ["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", "-c", src, dest],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "qemu-img convert failed")


def _live_backup_with_overlay(dom, disk_source: str, disk_target: str, backup_path: str) -> str:
    """
    Live backup for a running VM:
      1) optional guest fsfreeze
      2) external disk-only snapshot (overlay) so the base becomes consistent
      3) qemu-img convert of the frozen base
      4) active blockcommit + pivot to merge overlay back
    Returns mode label "live".
    """
    import time
    import uuid as _uuid
    import libvirt as lv

    overlay_path = f"{disk_source}.qi-bak-{_uuid.uuid4().hex[:8]}"
    snap_name = f"qi-bak-{_uuid.uuid4().hex[:8]}"
    frozen = False

    try:
        # Best-effort filesystem quiesce via qemu-guest-agent
        try:
            dom.fsFreeze()
            frozen = True
        except Exception:
            frozen = False

        snap_xml = f"""<domainsnapshot>
  <name>{snap_name}</name>
  <disks>
    <disk name='{disk_target}' snapshot='external'>
      <source file='{overlay_path}'/>
    </disk>
  </disks>
</domainsnapshot>"""
        flags = (
            lv.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY
            | lv.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC
            | lv.VIR_DOMAIN_SNAPSHOT_CREATE_NO_METADATA
        )
        try_flags = flags
        if not frozen and hasattr(lv, "VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE"):
            try_flags = flags | lv.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE
        try:
            dom.snapshotCreateXML(snap_xml, try_flags)
        except lv.libvirtError:
            if try_flags != flags:
                dom.snapshotCreateXML(snap_xml, flags)
            else:
                raise
    finally:
        if frozen:
            try:
                dom.fsThaw()
            except Exception:
                pass

    try:
        _qemu_img_convert_compressed(disk_source, backup_path)

        # Merge overlay back into base and pivot live disk pointer
        commit_flags = lv.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE
        dom.blockCommit(disk_target, None, None, 0, commit_flags)

        deadline = time.time() + 3600
        while time.time() < deadline:
            info = dom.blockJobInfo(disk_target, 0)
            if not info:
                break
            cur = info.get("cur", 0) if isinstance(info, dict) else 0
            end = info.get("end", 0) if isinstance(info, dict) else 0
            if end and cur >= end:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("blockcommit timed out while merging live backup overlay")

        try:
            dom.blockJobAbort(disk_target, lv.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
        except lv.libvirtError as e:
            # Job may already be finished/pivoted on some libvirt versions
            msg = str(e).lower()
            if "not found" not in msg and "no current block job" not in msg:
                raise

        return "live"
    finally:
        # Best-effort overlay cleanup if pivot left the file behind unused
        if os.path.exists(overlay_path):
            try:
                # If still referenced as live source, do not delete
                current, _ = _primary_disk_file_and_target(dom)
                if os.path.abspath(current) != os.path.abspath(overlay_path):
                    os.remove(overlay_path)
            except Exception:
                pass


@app.post("/api/v1/backups/create")
async def create_backup(body: BackupCreateRequest, token: AuthDep):
    """
    Create a full disk backup.

    - Stopped VM: direct qemu-img convert
    - Running VM: temporary external snapshot + convert + blockcommit (live backup)
    """
    import libvirt as libvirt_mod

    os.makedirs(BACKUP_DIR, exist_ok=True)

    dom = libvirt_svc._get_domain(body.domain_name)
    if not dom:
        raise HTTPException(status_code=404, detail=f"VM '{body.domain_name}' not found")

    state, _ = dom.state()
    is_running = state == libvirt_mod.VIR_DOMAIN_RUNNING

    try:
        disk_source, disk_target = _primary_disk_file_and_target(dom)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to resolve VM disk: {e}")

    safe_name = body.backup_name.replace(" ", "_").replace("/", "_")[:50]
    backup_filename = f"{body.backup_id}_{safe_name}.qcow2"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)

    try:
        mode = "offline"
        if is_running:
            mode = _live_backup_with_overlay(dom, disk_source, disk_target, backup_path)
        else:
            _qemu_img_convert_compressed(disk_source, backup_path)

        file_size = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0
        return {
            "status": "success",
            "file_path": backup_path,
            "file_size_bytes": file_size,
            "backup_id": body.backup_id,
            "mode": mode,
        }

    except Exception as e:
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        if "timed out" in str(e).lower() or e.__class__.__name__ == "TimeoutExpired":
            raise HTTPException(status_code=500, detail="Backup timeout (> 1h)")
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@app.post("/api/v1/backups/restore")
async def restore_backup(body: BackupRestoreRequest, token: AuthDep):
    """
    Restore a VM disk from a backup file.
    VM must be stopped. Replaces the primary disk with the backup.
    """
    import subprocess
    import xml.etree.ElementTree as ET

    dom = libvirt_svc._get_domain(body.domain_name)
    if not dom:
        raise HTTPException(status_code=404, detail=f"VM '{body.domain_name}' not found")

    # Check VM is not running
    state, _ = dom.state()
    import libvirt
    if state == libvirt.VIR_DOMAIN_RUNNING:
        raise HTTPException(status_code=409, detail="VM must be stopped for restore")

    from services.path_security import assert_safe_id, resolve_under_allowlist

    assert_safe_id(body.domain_name, field="domain_name")
    try:
        backup_file_path = str(
            resolve_under_allowlist(body.backup_file_path, field="backup_file_path")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not os.path.exists(backup_file_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    # Find primary disk path
    xml_str = dom.XMLDesc(0)
    root = ET.fromstring(xml_str)
    disk_source = None
    for disk in root.findall(".//devices/disk"):
        if disk.get("device") == "disk":
            source = disk.find("source")
            if source is not None:
                disk_source = source.get("file") or source.get("dev")
                break

    if not disk_source:
        raise HTTPException(status_code=400, detail="No disk found for this VM")
    try:
        disk_source = str(resolve_under_allowlist(disk_source, field="disk_source"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        # Create a safety copy of current disk before overwriting
        safety_path = disk_source + ".pre-restore"
        subprocess.run(
            ["cp", "--sparse=auto", disk_source, safety_path],
            capture_output=True, text=True, timeout=3600,
        )

        # Restore: copy backup over the original disk
        result = subprocess.run(
            [
                "qemu-img", "convert",
                "-f", "qcow2",
                "-O", "qcow2",
                backup_file_path,
                disk_source,
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )

        if result.returncode != 0:
            # Restore failed – try to recover from safety copy
            if os.path.exists(safety_path):
                subprocess.run(["mv", safety_path, disk_source], capture_output=True)
            raise Exception(result.stderr.strip())

        # Success – remove safety copy
        if os.path.exists(safety_path):
            os.remove(safety_path)

        return {
            "status": "success",
            "message": f"VM '{body.domain_name}' restored from backup",
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Restore timeout (> 1h)")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")


@app.delete("/api/v1/backups/{backup_filename}")
async def delete_backup(backup_filename: str, token: AuthDep):
    """Delete a backup file from disk."""
    # Security: only allow deletion from the backup directory
    backup_path = os.path.join(BACKUP_DIR, os.path.basename(backup_filename))

    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    try:
        os.remove(backup_path)
        return {"status": "success", "message": f"Backup '{backup_filename}' deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Deletion failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  VM EXPORT / IMPORT
# ══════════════════════════════════════════════════════════════════════════════

EXPORT_DIR = os.path.join(agent_settings.STORAGE_BASE_PATH, "exports")


class VMExportRequest(BaseModel):
    domain_name: str
    vm_id: str
    vm_name: str


class VMImportRequest(BaseModel):
    vm_id: str
    domain_name: str
    disk_format: str = "qcow2"
    vcpus: int = 1
    ram_mb: int = 1024
    disk_size_gb: int = 20
    mac_address: str | None = None
    bridge: str = "virbr0"
    storage_base_path: str | None = None


@app.post("/api/v1/vms/export")
async def export_vm(body: VMExportRequest, token: AuthDep):
    """
    Export a VM as a compressed standalone qcow2 file (VM must be stopped).
    Returns the export file path and size.
    """
    import subprocess
    import xml.etree.ElementTree as ET
    import libvirt as libvirt_mod

    os.makedirs(EXPORT_DIR, exist_ok=True)

    dom = libvirt_svc._get_domain(body.domain_name)
    if not dom:
        raise HTTPException(status_code=404, detail=f"VM '{body.domain_name}' not found")

    state, _ = dom.state()
    if state == libvirt_mod.VIR_DOMAIN_RUNNING:
        raise HTTPException(status_code=409, detail="VM must be stopped for export")

    xml_str = dom.XMLDesc(0)
    root = ET.fromstring(xml_str)
    disk_source = None
    for disk in root.findall(".//devices/disk"):
        if disk.get("device") == "disk":
            source = disk.find("source")
            if source is not None:
                disk_source = source.get("file") or source.get("dev")
                break

    if not disk_source:
        raise HTTPException(status_code=400, detail="No disk found for this VM")

    safe_name = body.vm_name.replace(" ", "_").replace("/", "_")[:50]
    export_filename = f"export_{body.vm_id[:8]}_{safe_name}.qcow2"
    export_path = os.path.join(EXPORT_DIR, export_filename)

    try:
        result = subprocess.run(
            ["qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", "-c", disk_source, export_path],
            capture_output=True, text=True, timeout=7200,
        )
        if result.returncode != 0:
            raise Exception(result.stderr.strip())

        file_size = os.path.getsize(export_path) if os.path.exists(export_path) else 0
        return {
            "status": "success",
            "file_path": export_path,
            "file_name": export_filename,
            "file_size_bytes": file_size,
        }

    except subprocess.TimeoutExpired:
        if os.path.exists(export_path):
            os.remove(export_path)
        raise HTTPException(status_code=500, detail="Export timeout (> 2h)")
    except Exception as e:
        if os.path.exists(export_path):
            os.remove(export_path)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")


@app.get("/api/v1/vms/export/{filename}")
async def download_export(filename: str, token: AuthDep):
    """Stream-download an exported VM disk image."""
    from fastapi.responses import FileResponse

    safe_filename = os.path.basename(filename)
    file_path = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(path=file_path, filename=safe_filename, media_type="application/octet-stream")


@app.delete("/api/v1/vms/export/{filename}")
async def delete_export(filename: str, token: AuthDep):
    """Delete an export file from disk."""
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(EXPORT_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Export file not found")
    os.remove(file_path)
    return {"status": "success", "message": "Export deleted"}


@app.post("/api/v1/vms/import/prepare")
async def prepare_import(body: VMImportRequest, token: AuthDep):
    """Prepare storage directory for an imported VM disk."""
    base_path = Path(body.storage_base_path) if body.storage_base_path else Path(agent_settings.STORAGE_BASE_PATH)
    vm_dir = base_path / body.vm_id
    vm_dir.mkdir(parents=True, exist_ok=True)
    disk_path = str(vm_dir / f"disk.{body.disk_format}")
    return {"status": "ready", "disk_path": disk_path, "vm_dir": str(vm_dir)}


@app.put("/api/v1/vms/import/{vm_id}/disk")
async def upload_import_disk(vm_id: str, request, token: AuthDep):
    """Stream-upload a disk image for VM import."""
    import subprocess
    import json as json_mod

    base_path = Path(agent_settings.STORAGE_BASE_PATH)
    vm_dir = base_path / vm_id
    disk_path = vm_dir / "disk.qcow2"

    if not vm_dir.exists():
        raise HTTPException(status_code=404, detail="VM directory not found. Call /import/prepare first.")

    try:
        with open(disk_path, "wb") as f:
            async for chunk in request.stream():
                f.write(chunk)

        file_size = os.path.getsize(disk_path)

        info = subprocess.run(
            ["qemu-img", "info", "--output=json", str(disk_path)],
            capture_output=True, text=True, timeout=30,
        )
        if info.returncode != 0:
            os.remove(disk_path)
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid disk image")

        disk_info = json_mod.loads(info.stdout)
        virtual_size_gb = disk_info.get("virtual-size", 0) // (1024**3)

        return {
            "status": "success",
            "disk_path": str(disk_path),
            "file_size_bytes": file_size,
            "virtual_size_gb": virtual_size_gb,
            "format": disk_info.get("format", "unknown"),
        }
    except HTTPException:
        raise
    except Exception as e:
        if disk_path.exists():
            os.remove(disk_path)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")


@app.post("/api/v1/vms/import/{vm_id}/register")
async def register_imported_vm(vm_id: str, body: VMImportRequest, token: AuthDep):
    """Register an imported VM in libvirt (define the domain). Disk must exist."""
    import subprocess

    base_path = Path(body.storage_base_path) if body.storage_base_path else Path(agent_settings.STORAGE_BASE_PATH)
    disk_path = base_path / vm_id / f"disk.{body.disk_format}"

    if not disk_path.exists():
        raise HTTPException(status_code=404, detail="Disk not found. Upload the disk first.")

    # Resize if needed
    try:
        import json as json_mod
        info_result = subprocess.run(
            ["qemu-img", "info", "--output=json", str(disk_path)],
            capture_output=True, text=True, timeout=30,
        )
        disk_info = json_mod.loads(info_result.stdout)
        virtual_size = disk_info.get("virtual-size", 0)
        target_bytes = body.disk_size_gb * 1024**3
        if virtual_size < target_bytes:
            subprocess.run(
                ["qemu-img", "resize", str(disk_path), f"{body.disk_size_gb}G"],
                capture_output=True, text=True, timeout=120,
            )
    except Exception:
        pass

    mac = body.mac_address or f"52:54:00:{os.urandom(1).hex()}:{os.urandom(1).hex()}:{os.urandom(1).hex()}"

    domain_xml = f"""<domain type='kvm'>
  <name>{body.domain_name}</name>
  <memory unit='MiB'>{body.ram_mb}</memory>
  <vcpu placement='static'>{body.vcpus}</vcpu>
  <os><type arch='x86_64' machine='pc-q35-6.2'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough'/>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='{body.disk_format}' cache='writeback' discard='unmap'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='bridge'>
      <mac address='{mac}'/>
      <source bridge='{body.bridge}'/>
      <model type='virtio'/>
    </interface>
    <channel type='unix'>
      <source mode='bind' path='/var/lib/libvirt/qemu/channel/target/{body.domain_name}.org.qemu.guest_agent.0'/>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
    <video><model type='virtio'/></video>
    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
  </devices>
</domain>"""

    try:
        conn = libvirt_svc.conn
        dom = conn.defineXML(domain_xml)
        if not dom:
            raise Exception("libvirt defineXML returned None")

        vnc_port = None
        try:
            import xml.etree.ElementTree as ET
            desc = dom.XMLDesc(0)
            root = ET.fromstring(desc)
            graphics = root.find(".//graphics[@type='vnc']")
            if graphics is not None:
                vnc_port = int(graphics.get("port", -1))
        except Exception:
            pass

        return {
            "status": "success",
            "domain_name": body.domain_name,
            "disk_path": str(disk_path),
            "mac_address": mac,
            "vnc_port": vnc_port,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to register VM: {e}")


# ────────────────────────────── Load Balancer ──────────────────────────────

LB_CONF_DIR = "/var/lib/qemu-interface/lb-configs"


class LBUpstream(BaseModel):
    ip: str
    port: int = 80
    weight: int = 1


class LBConfig(BaseModel):
    name: str
    protocol: str = "http"
    algorithm: str = "round_robin"
    listen_port: int
    # One public VIP on the host bridge; nginx RR to NAT upstreams
    public_ip: str | None = None
    health_check_path: str | None = "/"
    health_check_interval: int = 10
    upstreams: list[LBUpstream]


class LBApplyRequest(BaseModel):
    load_balancers: list[LBConfig]


_ALGO_MAP = {
    "round_robin": "",
    "least_conn": "least_conn;",
    "ip_hash": "ip_hash;",
}

LB_FLOAT_CHAIN = "QI_LB_FLOAT"


def _lb_float_bridge() -> str:
    for name in ("br0", "virbr0"):
        if os.path.exists(f"/sys/class/net/{name}"):
            return name
    return "br0"


def _apply_lb_vips(load_balancers: list[LBConfig]) -> list[str]:
    """Bind each LB's public VIP /32 on the host bridge (no per-backend DNAT)."""
    import subprocess

    bridge = _lb_float_bridge()
    desired_pubs = {
        (lb.public_ip or "").split("/")[0].strip()
        for lb in load_balancers
        if lb.public_ip
    }
    desired_pubs.discard("")

    # Flush legacy DNAT chain from older per-target floating mode
    subprocess.run(["iptables", "-t", "nat", "-N", LB_FLOAT_CHAIN], capture_output=True)
    subprocess.run(["iptables", "-t", "nat", "-F", LB_FLOAT_CHAIN], capture_output=True)
    for hook in ("PREROUTING", "OUTPUT"):
        while True:
            rm = subprocess.run(
                ["iptables", "-t", "nat", "-D", hook, "-j", LB_FLOAT_CHAIN],
                capture_output=True,
            )
            if rm.returncode != 0:
                break

    state_path = "/var/lib/qemu-interface/lb-floating-ips.json"
    prev: list[str] = []
    try:
        import json as _json
        if os.path.exists(state_path):
            prev = list(_json.load(open(state_path)))
    except Exception:
        prev = []
    for old in prev:
        if old not in desired_pubs:
            subprocess.run(
                ["ip", "addr", "del", f"{old}/32", "dev", bridge],
                capture_output=True, text=True,
            )

    applied: list[str] = []
    for pub in sorted(desired_pubs):
        check = subprocess.run(
            ["ip", "-4", "addr", "show", "dev", bridge],
            capture_output=True, text=True,
        )
        if pub not in (check.stdout or ""):
            subprocess.run(
                ["ip", "addr", "add", f"{pub}/32", "dev", bridge],
                capture_output=True, text=True,
            )
        applied.append(f"{pub}/32@{bridge}")
    try:
        import json as _json
        os.makedirs("/var/lib/qemu-interface", exist_ok=True)
        with open(state_path, "w") as fh:
            _json.dump(sorted(desired_pubs), fh)
    except Exception:
        pass
    return applied


def _generate_lb_nginx_config(lb: LBConfig) -> str:
    """Generate an Nginx upstream + server block for a load balancer.

    Listen on all interfaces inside the container; the host VIP is bound on
    br0 so docker-proxy (0.0.0.0:port) accepts traffic for that public IP.
    Upstreams are always NAT/internal guest IPs (round-robin).
    """
    safe_name = lb.name.replace(" ", "_").replace("-", "_").lower()
    algo_directive = _ALGO_MAP.get(lb.algorithm, "")
    vip = (lb.public_ip or "").split("/")[0].strip() or "*"

    servers = "\n".join(
        f"    server {u.ip}:{u.port} weight={u.weight} max_fails=3 fail_timeout=10s;"
        for u in lb.upstreams
    )

    if lb.protocol == "tcp":
        return f"""# LB (stream): {lb.name} VIP={vip} -> :{lb.listen_port}
upstream lb_{safe_name} {{
    {algo_directive}
{servers}
}}

server {{
    listen {lb.listen_port};
    proxy_pass lb_{safe_name};
    proxy_timeout 300s;
    proxy_connect_timeout 5s;
    proxy_next_upstream on;
}}
"""

    return f"""# LB (http): {lb.name} VIP={vip} -> :{lb.listen_port}
upstream lb_{safe_name} {{
    {algo_directive}
{servers}
}}

server {{
    listen {lb.listen_port};
    server_name _;

    location / {{
        proxy_pass http://lb_{safe_name};
        proxy_connect_timeout 3s;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""


@app.post("/api/v1/lb/apply")
async def apply_lb_config(body: LBApplyRequest, token: AuthDep):
    """Write Nginx load balancer configs and reload Nginx.

    HTTP/HTTPS LBs → lb-configs/http_*.conf (included in http {})
    TCP LBs        → lb-configs/stream_*.conf (included in stream {})
    """
    import glob as glob_mod
    import re

    os.makedirs(LB_CONF_DIR, exist_ok=True)

    for f in glob_mod.glob(os.path.join(LB_CONF_DIR, "http_*.conf")):
        os.remove(f)
    for f in glob_mod.glob(os.path.join(LB_CONF_DIR, "stream_*.conf")):
        os.remove(f)
    # Legacy single-file layout
    for f in glob_mod.glob(os.path.join(LB_CONF_DIR, "lb_*.conf")):
        os.remove(f)

    written = []
    for lb in body.load_balancers:
        safe_name = re.sub(r"[^a-z0-9_]+", "_", lb.name.lower()).strip("_") or "lb"
        prefix = "stream" if lb.protocol == "tcp" else "http"
        filename = f"{prefix}_{safe_name}.conf"
        filepath = os.path.join(LB_CONF_DIR, filename)

        config = _generate_lb_nginx_config(lb)
        with open(filepath, "w") as f:
            f.write(config)
        written.append(filename)

    # Keep include globs valid when there are zero LBs
    if not any(glob_mod.glob(os.path.join(LB_CONF_DIR, "http_*.conf"))):
        open(os.path.join(LB_CONF_DIR, "http_.conf"), "w").close()
    if not any(glob_mod.glob(os.path.join(LB_CONF_DIR, "stream_*.conf"))):
        open(os.path.join(LB_CONF_DIR, "stream_.conf"), "w").close()

    vips = _apply_lb_vips(body.load_balancers)

    try:
        _nginx_test_and_reload()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Nginx LB apply failed: {e}",
        ) from e

    return {
        "status": "success",
        "message": f"{len(written)} load balancer(s) applied",
        "written": written,
        "vips": vips,
        "floating": vips,  # backwards-compatible key
    }
