"""
VM business logic: quota checks, provisioning orchestration, lifecycle.
"""

import random
import uuid
from typing import Optional

import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.network import AllocationStatus, IPAllocation, IPPool, PoolType
from app.models.storage import CloudImage, StoragePool
from app.models.task import Task, TaskStatus, TaskType
from app.models.tenant import Project, ProjectUser, Tenant
from app.models.user import TenantUser, User, UserRole
from app.models.vm import DiskFormat, VMDisk, VMStatus, VirtualMachine
from app.schemas.vm import VMCloneRequest, VMCreate


def _merge_cloud_init(base: dict, override: dict) -> dict:
    """Recursively merge cloud-init dicts while keeping defaults."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_cloud_init(merged[key], value)
        elif key in merged and isinstance(merged[key], list) and isinstance(value, list):
            merged[key] = [*merged[key], *value]
        else:
            merged[key] = value
    return merged


def _ensure_qemu_guest_agent(userdata: dict) -> None:
    """Guarantee qemu-guest-agent package + enable for Linux cloud-init guests."""
    packages = list(userdata.get("packages") or [])
    if "qemu-guest-agent" not in packages:
        packages.insert(0, "qemu-guest-agent")
    userdata["packages"] = packages

    enable_cmd = "systemctl enable --now qemu-guest-agent"
    runcmd = list(userdata.get("runcmd") or [])
    if enable_cmd not in runcmd:
        runcmd.insert(0, enable_cmd)
    userdata["runcmd"] = runcmd


def _default_ssh_hardening_cloud_init() -> dict:
    """
    Guest-side SSH + network defaults so idle sessions stay alive and the VM
    remains reachable without requiring client ~/.ssh/config changes.

    - sshd ClientAlive* keeps the TCP session (and NAT/firewall state) warm
    - a small gateway ping timer prevents Hetzner vSwitch/ARP idle dropouts
      that otherwise make the VM look "dead" after an SSH disconnect
    - fail2ban is enabled but not overly aggressive (avoids self-lockouts)
    """
    return {
        "packages": ["qemu-guest-agent", "fail2ban", "iputils-ping", "iproute2"],
        "write_files": [
            {
                "path": "/etc/ssh/sshd_config.d/99-qemu-interface.conf",
                "content": (
                    "# Managed by QEMU Interface – keep SSH sessions stable on public IPs\n"
                    "ClientAliveInterval 30\n"
                    "ClientAliveCountMax 120\n"
                    "TCPKeepAlive yes\n"
                    "UseDNS no\n"
                    "LoginGraceTime 120\n"
                    "MaxStartups 100:30:200\n"
                    "MaxSessions 20\n"
                ),
                "permissions": "0644",
            },
            {
                "path": "/etc/fail2ban/jail.d/sshd.local",
                "content": (
                    "[sshd]\n"
                    "enabled = true\n"
                    "maxretry = 12\n"
                    "findtime = 900\n"
                    "bantime = 600\n"
                    "ignoreip = 127.0.0.1/8 ::1\n"
                ),
                "permissions": "0644",
            },
            {
                "path": "/usr/local/sbin/qemu-interface-net-keepalive",
                "content": (
                    "#!/bin/bash\n"
                    "# Keep L3/ARP state warm toward the default gateway (Hetzner vSwitch etc.)\n"
                    "set -euo pipefail\n"
                    "GW=$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')\n"
                    "if [[ -n \"${GW:-}\" ]]; then\n"
                    "  ping -c 1 -W 2 \"$GW\" >/dev/null 2>&1 || true\n"
                    "fi\n"
                ),
                "permissions": "0755",
            },
            {
                "path": "/etc/systemd/system/qemu-interface-net-keepalive.service",
                "content": (
                    "[Unit]\n"
                    "Description=QEMU Interface gateway keepalive (anti idle-drop)\n"
                    "After=network-online.target\n"
                    "Wants=network-online.target\n"
                    "\n"
                    "[Service]\n"
                    "Type=oneshot\n"
                    "ExecStart=/usr/local/sbin/qemu-interface-net-keepalive\n"
                ),
                "permissions": "0644",
            },
            {
                "path": "/etc/systemd/system/qemu-interface-net-keepalive.timer",
                "content": (
                    "[Unit]\n"
                    "Description=Run gateway keepalive every 30s\n"
                    "\n"
                    "[Timer]\n"
                    "OnBootSec=30s\n"
                    "OnUnitActiveSec=30s\n"
                    "AccuracySec=5s\n"
                    "Unit=qemu-interface-net-keepalive.service\n"
                    "\n"
                    "[Install]\n"
                    "WantedBy=timers.target\n"
                ),
                "permissions": "0644",
            },
        ],
        "runcmd": [
            "systemctl enable --now qemu-guest-agent",
            "systemctl enable --now fail2ban 2>/dev/null || true",
            "systemctl daemon-reload",
            "systemctl enable --now qemu-interface-net-keepalive.timer",
            "systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || true",
        ],
    }


def _parse_custom_userdata_yaml(raw_yaml: str | None) -> dict | None:
    """Parse optional custom user-data YAML to a dict."""
    if not raw_yaml:
        return None
    if not raw_yaml.strip():
        return None
    try:
        parsed = yaml.safe_load(raw_yaml)
    except Exception as e:
        raise ValueError(f"Invalid custom user-data YAML: {e}")
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise ValueError("Custom user-data YAML must be a mapping/object at top-level")
    return parsed


def generate_mac_address() -> str:
    """Generate a random MAC address with QEMU prefix 52:54:00."""
    return "52:54:00:{:02x}:{:02x}:{:02x}".format(
        random.randint(0, 255),
        random.randint(0, 255),
        random.randint(0, 255),
    )


async def check_tenant_quota(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    required_vcpus: int,
    required_ram_mb: int,
    required_disk_gb: int,
) -> tuple[bool, str]:
    """Check if tenant has sufficient quota for a new VM."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()

    if not tenant:
        return False, "Tenant not found"

    # Count current usage
    result = await db.execute(
        select(
            func.count(VirtualMachine.id),
            func.coalesce(func.sum(VirtualMachine.vcpus), 0),
            func.coalesce(func.sum(VirtualMachine.ram_mb), 0),
            func.coalesce(func.sum(VirtualMachine.disk_size_gb), 0),
        ).where(
            VirtualMachine.tenant_id == tenant_id,
            VirtualMachine.is_deleted.is_(False),
        )
    )
    row = result.one()
    current_vms, current_vcpus, current_ram, current_disk = row

    if current_vms + 1 > tenant.max_vms:
        return False, f"VM-Limit erreicht ({tenant.max_vms})"
    if current_vcpus + required_vcpus > tenant.max_vcpus:
        return False, f"vCPU-Limit erreicht ({tenant.max_vcpus})"
    if current_ram + required_ram_mb > tenant.max_ram_mb:
        return False, f"RAM-Limit erreicht ({tenant.max_ram_mb} MB)"
    if current_disk + required_disk_gb > tenant.max_storage_gb:
        return False, f"Storage-Limit erreicht ({tenant.max_storage_gb} GB)"

    return True, "OK"


async def check_tenant_quota_for_resize(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    current_vcpus: int,
    new_vcpus: int,
    current_ram_mb: int,
    new_ram_mb: int,
    current_disk_gb: int,
    new_disk_gb: int,
) -> tuple[bool, str]:
    """Check tenant quota when resizing an existing VM (delta against current allocation)."""
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if not tenant:
        return False, "Tenant not found"

    result = await db.execute(
        select(
            func.coalesce(func.sum(VirtualMachine.vcpus), 0),
            func.coalesce(func.sum(VirtualMachine.ram_mb), 0),
            func.coalesce(func.sum(VirtualMachine.disk_size_gb), 0),
        ).where(
            VirtualMachine.tenant_id == tenant_id,
            VirtualMachine.is_deleted.is_(False),
        )
    )
    current_vcpus_total, current_ram_total, current_disk_total = result.one()

    # Replace this VM's current allocation with the proposed values
    projected_vcpus = int(current_vcpus_total) - current_vcpus + new_vcpus
    projected_ram = int(current_ram_total) - current_ram_mb + new_ram_mb
    projected_disk = int(current_disk_total) - current_disk_gb + new_disk_gb

    if projected_vcpus > tenant.max_vcpus:
        return False, f"vCPU-Limit erreicht ({tenant.max_vcpus})"
    if projected_ram > tenant.max_ram_mb:
        remaining = tenant.max_ram_mb - (int(current_ram_total) - current_ram_mb)
        return False, f"RAM-Limit erreicht (max {remaining} MB verfügbar für diese VM, Quota {tenant.max_ram_mb} MB)"
    if projected_disk > tenant.max_storage_gb:
        return False, f"Storage-Limit erreicht ({tenant.max_storage_gb} GB)"

    return True, "OK"


async def check_user_vm_limit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[bool, str]:
    """Check optional per-user VM limit configured on tenant membership."""
    membership = (
        await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not membership or membership.max_vms is None:
        return True, "OK"

    user_vm_count = (
        await db.execute(
            select(func.count(VirtualMachine.id)).where(
                VirtualMachine.tenant_id == tenant_id,
                VirtualMachine.created_by == user_id,
                VirtualMachine.is_deleted.is_(False),
            )
        )
    ).scalar() or 0

    if user_vm_count + 1 > membership.max_vms:
        return False, f"User VM-Limit reached ({membership.max_vms})"
    return True, "OK"


async def _validate_user_project_access(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user: User,
    project_id: uuid.UUID,
) -> None:
    """Restrict non-admin users to assigned projects when assignments exist."""
    if user.is_superadmin:
        return

    is_tenant_admin = any(
        membership.tenant_id == tenant_id and membership.role in (UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN)
        for membership in user.tenant_memberships
    )
    if is_tenant_admin:
        return

    assigned_project_ids = (
        await db.execute(
            select(ProjectUser.project_id)
            .join(Project, Project.id == ProjectUser.project_id)
            .where(
                ProjectUser.user_id == user.id,
                Project.tenant_id == tenant_id,
                Project.is_active.is_(True),
            )
        )
    ).scalars().all()
    if assigned_project_ids and project_id not in assigned_project_ids:
        raise ValueError("No access to selected project")


async def create_vm(
    db: AsyncSession,
    body: VMCreate,
    user: User,
    tenant_id: uuid.UUID,
) -> tuple[VirtualMachine, Task]:
    """
    Create a VM record and queue the provisioning task.
    Returns (vm, task) tuple.
    """
    # Verify project belongs to tenant
    project = (
        await db.execute(
            select(Project).where(
                Project.id == body.project_id,
                Project.tenant_id == tenant_id,
                Project.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if not project:
        raise ValueError("Project not found or does not belong to tenant")
    await _validate_user_project_access(db, tenant_id, user, body.project_id)

    # Verify template exists (non-admins may only use public images)
    template_query = select(CloudImage).where(
        CloudImage.id == body.template_id,
        CloudImage.is_active.is_(True),
    )
    if not user.is_superadmin:
        template_query = template_query.where(CloudImage.is_public.is_(True))
    template = (await db.execute(template_query)).scalar_one_or_none()

    if not template:
        raise ValueError("Cloud image (template) not found")

    template_format = template.format.value if hasattr(template.format, "value") else str(template.format)
    is_iso_install = template_format == "iso"

    # Check minimum requirements
    if body.vcpus < template.min_vcpus:
        raise ValueError(f"At least {template.min_vcpus} vCPUs required for this image")
    if body.ram_mb < template.min_ram_mb:
        raise ValueError(f"At least {template.min_ram_mb} MB RAM required for this image")
    if body.disk_size_gb < template.min_disk_gb:
        raise ValueError(f"At least {template.min_disk_gb} GB disk required for this image")

    virtio_iso_path: str | None = None
    if body.virtio_iso_id is not None:
        virtio_image = (
            await db.execute(
                select(CloudImage).where(
                    CloudImage.id == body.virtio_iso_id,
                    CloudImage.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not virtio_image or not virtio_image.file_path:
            raise ValueError("VirtIO ISO not found or not ready")
        virtio_format = virtio_image.format.value if hasattr(virtio_image.format, "value") else str(virtio_image.format)
        if virtio_format != "iso":
            raise ValueError("VirtIO image must be ISO format")
        if not is_iso_install:
            raise ValueError("VirtIO ISO is only supported for ISO installations")
        virtio_iso_path = virtio_image.file_path

    # Check quota
    ok, msg = await check_tenant_quota(
        db, tenant_id, body.vcpus, body.ram_mb, body.disk_size_gb
    )
    if not ok:
        raise ValueError(f"Quota exceeded: {msg}")

    ok_user, msg_user = await check_user_vm_limit(db, tenant_id, user.id)
    if not ok_user:
        raise ValueError(msg_user)

    project = (
        await db.execute(
            select(Project).where(
                Project.id == body.project_id,
                Project.tenant_id == tenant_id,
                Project.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not project:
        raise ValueError("Project not found or does not belong to tenant")
    await _validate_user_project_access(db, tenant_id, user, body.project_id)

    # Resolve storage pool (shared/global or assigned to this tenant only)
    def _pool_visible_to_tenant(pool: StoragePool) -> bool:
        return pool.tenant_id is None or pool.tenant_id == tenant_id

    storage_pool_id = body.storage_pool_id
    if storage_pool_id:
        chosen_pool = (await db.execute(
            select(StoragePool).where(
                StoragePool.id == storage_pool_id,
                StoragePool.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if not chosen_pool:
            raise ValueError("Selected storage pool not found or inactive")
        if not _pool_visible_to_tenant(chosen_pool):
            raise ValueError("Selected storage pool is not available for this tenant")
    else:
        result = await db.execute(
            select(StoragePool).where(
                StoragePool.is_default.is_(True),
                StoragePool.is_active.is_(True),
                or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id == tenant_id),
            )
        )
        default_pool = result.scalar_one_or_none()
        if default_pool:
            storage_pool_id = default_pool.id
        else:
            # Fall back to any active shared/tenant pool if no default is set
            fallback = (await db.execute(
                select(StoragePool).where(
                    StoragePool.is_active.is_(True),
                    or_(StoragePool.tenant_id.is_(None), StoragePool.tenant_id == tenant_id),
                ).order_by(StoragePool.name).limit(1)
            )).scalar_one_or_none()
            if fallback:
                storage_pool_id = fallback.id

    # Build cloud-init config
    ci_identity = body.cloud_init_identity

    user_entry: dict = {
        "name": ci_identity.default_user,
        "sudo": "ALL=(ALL) NOPASSWD:ALL",
        "shell": "/bin/bash",
        "ssh_authorized_keys": ci_identity.ssh_authorized_keys,
    }

    # If a password is set, unlock the account and use chpasswd for plaintext
    ssh_defaults = _default_ssh_hardening_cloud_init()
    cloud_init_userdata: dict = {
        "hostname": ci_identity.hostname,
        "fqdn": ci_identity.fqdn or ci_identity.hostname,
        "users": [user_entry],
        "ssh_pwauth": True,  # Always allow SSH password auth when password is set
        "packages": list(ssh_defaults["packages"]),
        "write_files": list(ssh_defaults["write_files"]),
        "runcmd": list(ssh_defaults["runcmd"]),
    }

    if not is_iso_install:
        # Fallback if host lacks virt-customize: show boot/login on VNC (tty0)
        cloud_init_userdata.setdefault("write_files", []).append(
            {
                "path": "/etc/default/grub.d/99-qemu-interface.cfg",
                "content": (
                    'GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"\n'
                    'GRUB_TERMINAL_OUTPUT="console serial gfxterm"\n'
                ),
                "permissions": "0644",
            }
        )
        cloud_init_userdata["runcmd"] = [
            "update-grub 2>/dev/null || grub-mkconfig -o /boot/grub/grub.cfg 2>/dev/null || true",
            *cloud_init_userdata["runcmd"],
        ]

    if ci_identity.password:
        user_entry["lock_passwd"] = False
        # Use chpasswd with "list" format for maximum compatibility
        # This works with all cloud-init versions and accepts plaintext passwords
        cloud_init_userdata["chpasswd"] = {
            "expire": False,
            "list": [f"{ci_identity.default_user}:{ci_identity.password}"],
        }
        cloud_init_userdata["ssh_pwauth"] = not ci_identity.disable_password_login
    elif ci_identity.disable_password_login:
        cloud_init_userdata["ssh_pwauth"] = False

    if body.cloud_init_userdata:
        ud = body.cloud_init_userdata
        if ud.packages:
            # Merge with default packages (avoid duplicates)
            existing = set(cloud_init_userdata.get("packages", []))
            for pkg in ud.packages:
                if pkg not in existing:
                    cloud_init_userdata.setdefault("packages", []).append(pkg)
        if ud.write_files:
            cloud_init_userdata["write_files"] = ud.write_files
        if ud.runcmd:
            # Append user runcmd after default commands
            cloud_init_userdata.setdefault("runcmd", []).extend(ud.runcmd)
        if ud.bootcmd:
            cloud_init_userdata["bootcmd"] = ud.bootcmd
        if ud.timezone:
            cloud_init_userdata["timezone"] = ud.timezone
        if ud.locale:
            cloud_init_userdata["locale"] = ud.locale

        # Keyboard layout configuration
        if ud.keyboard_layout:
            kb = ud.keyboard_layout.lower().strip()
            # Cloud-Init native keyboard config
            cloud_init_userdata["keyboard"] = {
                "layout": kb,
            }
            # Set locale if not already set
            locale_map = {
                "de": "de_DE.UTF-8", "ch": "de_CH.UTF-8", "at": "de_AT.UTF-8",
                "fr": "fr_FR.UTF-8", "es": "es_ES.UTF-8", "it": "it_IT.UTF-8",
                "pt": "pt_PT.UTF-8", "nl": "nl_NL.UTF-8", "pl": "pl_PL.UTF-8",
                "ru": "ru_RU.UTF-8", "ja": "ja_JP.UTF-8", "ko": "ko_KR.UTF-8",
                "us": "en_US.UTF-8", "gb": "en_GB.UTF-8",
            }
            if not ud.locale and kb in locale_map:
                cloud_init_userdata["locale"] = locale_map[kb]

            # Ensure keyboard is set via runcmd as fallback (some images ignore keyboard config)
            kb_commands = [
                f"localectl set-keymap {kb}",
                f"localectl set-x11-keymap {kb}",
            ]
            existing_runcmd = cloud_init_userdata.get("runcmd", [])
            cloud_init_userdata["runcmd"] = kb_commands + list(existing_runcmd)

    image_default_userdata = _parse_custom_userdata_yaml(template.default_userdata_yaml)
    if image_default_userdata:
        cloud_init_userdata = _merge_cloud_init(cloud_init_userdata, image_default_userdata)

    custom_userdata = _parse_custom_userdata_yaml(body.custom_userdata_yaml)
    if custom_userdata:
        cloud_init_userdata = _merge_cloud_init(cloud_init_userdata, custom_userdata)

    # Always keep guest-agent for Linux cloud images (needed for live stats / password reset)
    if not is_iso_install:
        _ensure_qemu_guest_agent(cloud_init_userdata)

    cloud_init_metadata = {
        "instance-id": str(uuid.uuid4()),
        "local-hostname": ci_identity.hostname,
    }
    if is_iso_install:
        cloud_init_metadata["_install_options"] = {
            "os_profile": body.os_profile,
            "virtio_iso_path": virtio_iso_path,
        }

    # --- Network: Always allocate static IP from pool ---
    from app.services.network_service import (
        assert_pool_usable_for_tenant,
        pick_ip_for_tenant_pool,
        pool_visible_to_tenant,
    )

    pool = (
        await db.execute(
            select(IPPool).where(
                IPPool.id == body.ip_pool_id,
                IPPool.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if not pool:
        raise ValueError("Network/IP pool not found or inactive")

    await assert_pool_usable_for_tenant(db, pool, tenant_id)

    # Allocate chosen public IP, or next free (reserved public / private in tenant pool)
    try:
        alloc = await pick_ip_for_tenant_pool(
            db,
            pool,
            tenant_id,
            allocation_id=body.ip_allocation_id,
        )
    except ValueError:
        raise

    if not alloc:
        raise ValueError("No free IPs available in pool")

    # Mark IP as allocated
    alloc.status = AllocationStatus.ALLOCATED
    alloc.tenant_id = tenant_id
    db.add(alloc)
    allocated_ip = alloc

    # Build static network config for Cloud-Init
    cidr_prefix = "/" + pool.network.split("/")[1] if "/" in pool.network else "/24"
    ip_str = str(alloc.ip_address) if not hasattr(alloc.ip_address, 'ip') else str(alloc.ip_address.ip)
    ipv4_cidr = ip_str + cidr_prefix

    dns_servers = [pool.dns_primary]
    if pool.dns_secondary:
        dns_servers.append(pool.dns_secondary)

    # Generate MAC address early so we can use it in Cloud-Init network config
    mac_address = generate_mac_address()

    eth_config: dict = {
        "match": {"macaddress": mac_address},
        "addresses": [ipv4_cidr],
        "dhcp4": False,
        "gateway4": pool.gateway,
        "nameservers": {"addresses": dns_servers},
    }
    if pool.pool_type == PoolType.EXTERNAL:
        eth_config["mtu"] = settings.EXTERNAL_VM_MTU

    # Optional IPv6 from manual override
    if body.cloud_init_network:
        net = body.cloud_init_network
        if net.ipv6_address:
            eth_config["addresses"].append(net.ipv6_address)
        if net.ipv6_gateway:
            eth_config["gateway6"] = net.ipv6_gateway

    cloud_init_network = {
        "version": 2,
        "ethernets": {
            "nic0": eth_config,  # ID beliebig – match by MAC findet das richtige Interface
        },
    }

    # Generate unique domain name
    domain_name = f"vm-{uuid.uuid4().hex[:12]}"

    # Bridge comes from the pool
    vm_bridge = pool.bridge

    # Optional additional internal interfaces that should be attached at create time.
    secondary_interface_configs: list[dict] = []
    if body.secondary_pool_ids:
        unique_secondary_pool_ids = list(dict.fromkeys(body.secondary_pool_ids))
        if len(unique_secondary_pool_ids) > 4:
            raise ValueError("Maximum 4 additional internal interfaces allowed")
        if body.ip_pool_id in unique_secondary_pool_ids:
            raise ValueError("Primary network cannot also be selected as secondary interface")

        secondary_pools = (
            await db.execute(
                select(IPPool).where(
                    IPPool.id.in_(unique_secondary_pool_ids),
                    IPPool.is_active.is_(True),
                )
            )
        ).scalars().all()
        secondary_pool_map = {p.id: p for p in secondary_pools}

        missing_pool_ids = [
            str(pool_id) for pool_id in unique_secondary_pool_ids if pool_id not in secondary_pool_map
        ]
        if missing_pool_ids:
            raise ValueError(f"Secondary network pool(s) not found or inactive: {', '.join(missing_pool_ids)}")

        for secondary_pool_id in unique_secondary_pool_ids:
            secondary_pool = secondary_pool_map[secondary_pool_id]
            if secondary_pool.pool_type != PoolType.INTERNAL:
                raise ValueError("Additional interfaces are only supported for internal networks")
            if not await pool_visible_to_tenant(db, secondary_pool, tenant_id):
                raise ValueError(
                    f"Internal network '{secondary_pool.name}' is not available for this tenant"
                )
            if not secondary_pool.libvirt_network_name:
                raise ValueError(f"Internal network '{secondary_pool.name}' has no libvirt network")

            secondary_interface_configs.append(
                {
                    "mac_address": generate_mac_address(),
                    "network_name": secondary_pool.libvirt_network_name,
                    "pool_id": str(secondary_pool.id),
                    "pool_name": secondary_pool.name,
                    "subnet": secondary_pool.network,
                    "dhcp_enabled": bool(secondary_pool.internal_dhcp_enabled),
                }
            )

    # Create VM record
    vm = VirtualMachine(
        project_id=body.project_id,
        tenant_id=tenant_id,
        created_by=user.id,
        name=body.name,
        hostname=ci_identity.hostname,
        description=body.description,
        status=VMStatus.CREATING,
        vcpus=body.vcpus,
        ram_mb=body.ram_mb,
        disk_size_gb=body.disk_size_gb,
        disk_format=body.disk_format,
        storage_pool_id=storage_pool_id,
        template_id=body.template_id,
        mac_address=mac_address,
        bridge=vm_bridge,
        cloud_init_userdata=cloud_init_userdata,
        cloud_init_metadata=cloud_init_metadata,
        cloud_init_network=cloud_init_network,
        secondary_interfaces=secondary_interface_configs,
        libvirt_domain_name=domain_name,
    )
    db.add(vm)
    await db.flush()

    # Update storage pool usage
    if storage_pool_id:
        pool_obj = await db.get(StoragePool, storage_pool_id)
        if pool_obj:
            pool_obj.used_size_gb = (pool_obj.used_size_gb or 0) + body.disk_size_gb
            db.add(pool_obj)

    # Link allocated IP to the VM
    if allocated_ip:
        allocated_ip.vm_id = vm.id
        db.add(allocated_ip)
        await db.flush()

    # Create task
    task = Task(
        task_type=TaskType.VM_CREATE,
        status=TaskStatus.QUEUED,
        user_id=user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
    )
    db.add(task)
    await db.flush()

    return vm, task


async def clone_vm(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    user: User,
    source_vm: VirtualMachine,
    body: VMCloneRequest,
) -> tuple[VirtualMachine, Task]:
    """Clone an existing VM with new Cloud-Init identity and network allocation."""

    ok_user, msg_user = await check_user_vm_limit(db, tenant_id, user.id)
    if not ok_user:
        raise ValueError(msg_user)

    # Determine IP pool (from request or from source VM's allocation)
    pool_id = body.ip_pool_id
    if not pool_id:
        # Find pool from source VM's allocation
        src_alloc = (
            await db.execute(
                select(IPAllocation).where(
                    IPAllocation.vm_id == source_vm.id,
                    IPAllocation.status == AllocationStatus.ALLOCATED,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if src_alloc:
            pool_id = src_alloc.pool_id
        else:
            raise ValueError("Quell-VM hat keine IP-Zuordnung und kein Pool angegeben")

    from app.services.network_service import assert_pool_usable_for_tenant, pick_ip_for_tenant_pool

    pool = (
        await db.execute(
            select(IPPool).where(
                IPPool.id == pool_id,
                IPPool.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if not pool:
        raise ValueError("Network/IP pool not found or inactive")

    await assert_pool_usable_for_tenant(db, pool, tenant_id)

    alloc = await pick_ip_for_tenant_pool(db, pool, tenant_id)

    if not alloc:
        raise ValueError("No free IPs available in pool")

    alloc.status = AllocationStatus.ALLOCATED
    alloc.tenant_id = tenant_id
    db.add(alloc)

    # Build Cloud-Init
    ci_identity = body.cloud_init_identity
    ssh_defaults = _default_ssh_hardening_cloud_init()
    cloud_init_userdata = {
        "hostname": ci_identity.hostname,
        "fqdn": ci_identity.fqdn or f"{ci_identity.hostname}.local",
        "manage_etc_hosts": True,
        "users": [{
            "name": ci_identity.default_user,
            "sudo": "ALL=(ALL) NOPASSWD:ALL",
            "groups": "sudo",
            "shell": "/bin/bash",
            "lock_passwd": ci_identity.disable_password_login,
        }],
        "packages": list(ssh_defaults["packages"]),
        "write_files": list(ssh_defaults["write_files"]),
        "runcmd": list(ssh_defaults["runcmd"]),
    }
    if ci_identity.password:
        cloud_init_userdata["users"][0]["plain_text_passwd"] = ci_identity.password
        cloud_init_userdata["chpasswd"] = {"expire": False}
    if ci_identity.ssh_authorized_keys:
        cloud_init_userdata["users"][0]["ssh_authorized_keys"] = ci_identity.ssh_authorized_keys

    _ensure_qemu_guest_agent(cloud_init_userdata)

    cloud_init_metadata = {
        "instance-id": str(uuid.uuid4()),
        "local-hostname": ci_identity.hostname,
    }

    # Network config
    mac_address = generate_mac_address()
    cidr_prefix = "/" + pool.network.split("/")[1] if "/" in pool.network else "/24"
    ip_str = str(alloc.ip_address) if not hasattr(alloc.ip_address, 'ip') else str(alloc.ip_address.ip)
    ipv4_cidr = ip_str + cidr_prefix

    dns_servers = [pool.dns_primary]
    if pool.dns_secondary:
        dns_servers.append(pool.dns_secondary)

    cloud_init_network = {
        "version": 2,
        "ethernets": {
            "nic0": {
                "match": {"macaddress": mac_address},
                "addresses": [ipv4_cidr],
                "dhcp4": False,
                "gateway4": pool.gateway,
                "nameservers": {"addresses": dns_servers},
            },
        },
    }
    if pool.pool_type == PoolType.EXTERNAL:
        cloud_init_network["ethernets"]["nic0"]["mtu"] = settings.EXTERNAL_VM_MTU

    domain_name = f"vm-{uuid.uuid4().hex[:12]}"
    vm_bridge = pool.bridge

    # Create cloned VM record
    vm = VirtualMachine(
        project_id=body.project_id,
        tenant_id=tenant_id,
        created_by=user.id,
        name=body.name,
        hostname=ci_identity.hostname,
        description=f"Klon von {source_vm.name}",
        status=VMStatus.CREATING,
        vcpus=source_vm.vcpus,
        ram_mb=source_vm.ram_mb,
        disk_size_gb=source_vm.disk_size_gb,
        disk_format=source_vm.disk_format,
        storage_pool_id=source_vm.storage_pool_id,
        template_id=source_vm.template_id,
        mac_address=mac_address,
        bridge=vm_bridge,
        cloud_init_userdata=cloud_init_userdata,
        cloud_init_metadata=cloud_init_metadata,
        cloud_init_network=cloud_init_network,
        libvirt_domain_name=domain_name,
    )
    db.add(vm)
    await db.flush()

    # Link IP
    alloc.vm_id = vm.id
    db.add(alloc)
    await db.flush()

    # Create task
    task = Task(
        task_type=TaskType.VM_CLONE,
        status=TaskStatus.QUEUED,
        user_id=user.id,
        tenant_id=tenant_id,
        resource_type="virtual_machine",
        resource_id=vm.id,
        result={"source_vm_id": str(source_vm.id), "source_domain_name": source_vm.libvirt_domain_name},
    )
    db.add(task)
    await db.flush()

    return vm, task
