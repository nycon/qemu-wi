# Host Agent

The Host Agent runs directly on the bare-metal host (not in Docker) and executes privileged operations via libvirt, iptables, and direct disk access.

## Overview

- **Port**: 8443 (HTTPS with self-signed certificate)
- **Auth**: Bearer token (`AGENT_TOKEN` from secrets)
- **Service**: `qemu-interface-agent` (systemd)
- **Location**: `/opt/qemu-interface/host-agent/`
- **Python venv**: `/opt/qemu-interface/host-agent/venv/`

## Management

```bash
# Status
systemctl status qemu-interface-agent

# Restart
systemctl restart qemu-interface-agent

# Stop / Start
systemctl stop qemu-interface-agent
systemctl start qemu-interface-agent

# Logs
journalctl -u qemu-interface-agent -f
journalctl -u qemu-interface-agent -n 100 --no-pager
```

**Warning**: Never use `nohup` to start the agent manually. Always use systemd.

## API Endpoints

Base URL: `https://localhost:8443`

### Health
```
GET /health → { "status": "ok", "cpu_percent": ..., "memory_percent": ..., "disk_percent": ... }
```

### VM Provisioning
```
POST /api/v1/vms/provision
Body: {
  "vm_id": "uuid",
  "domain_name": "vm-abc123",
  "vcpus": 2,
  "ram_mb": 2048,
  "template_path": "/path/to/base.img",
  "disk_size_gb": 20,
  "cloud_init_userdata": {...},
  "cloud_init_metadata": {...},
  "cloud_init_network": {...}
}
```

### VM Lifecycle
```
POST   /api/v1/vms/{domain}/start
POST   /api/v1/vms/{domain}/stop
POST   /api/v1/vms/{domain}/force-stop
POST   /api/v1/vms/{domain}/reboot
DELETE /api/v1/vms/{domain}?vm_id=...
GET    /api/v1/vms/{domain}/status
GET    /api/v1/vms
```

### VM Operations
```
POST /api/v1/vms/clone
POST /api/v1/vms/{domain}/resize
POST /api/v1/vms/{domain}/add-nic
POST /api/v1/vms/{domain}/remove-nic
POST /api/v1/vms/regenerate-seed
GET  /api/v1/vms/{domain}/stats
```

### Snapshots
```
GET    /api/v1/vms/{domain}/snapshots
POST   /api/v1/vms/{domain}/snapshots
POST   /api/v1/vms/{domain}/snapshots/{name}/revert
DELETE /api/v1/vms/{domain}/snapshots/{name}
```

### Disks
```
POST   /api/v1/disks/create
POST   /api/v1/disks/attach
POST   /api/v1/disks/detach
POST   /api/v1/disks/resize
DELETE /api/v1/disks/delete
```

### Networks
```
GET    /api/v1/networks
POST   /api/v1/networks/create-nat
POST   /api/v1/networks/create-internal
DELETE /api/v1/networks/{name}
POST   /api/v1/networks/port-forward/add
POST   /api/v1/networks/port-forward/remove
POST   /api/v1/networks/port-forward/restore
```

### Security
```
GET    /api/v1/security/egress-rules
PUT    /api/v1/security/egress-rules
POST   /api/v1/security/egress-rules/add-port
DELETE /api/v1/security/egress-rules/remove-port
GET    /api/v1/security/egress-log
```

### Storage
```
POST /api/v1/storage/import-image
```

### SSL
```
GET  /api/v1/ssl/status
POST /api/v1/ssl/setup
POST /api/v1/ssl/test
POST /api/v1/ssl/regenerate-config
POST /api/v1/ssl/disable
```

### Backups
```
POST   /api/v1/backups/create
POST   /api/v1/backups/restore
DELETE /api/v1/backups/delete
```

**Create** supports running and stopped domains:

- **Running (live):** temporary external snapshot → copy base disk → `blockcommit` + pivot; guest stays up
- **Stopped (cold):** direct qcow2 copy/convert into the backup path

**Restore** expects a shut-off domain. Panel-level FTP/S3 offload and inventory are handled by the backend after the agent write; see [Backups](Backups.md).

### Export / Import
```
POST /api/v1/vms/export
GET  /api/v1/vms/export/{vm_id}/file
POST /api/v1/vms/import/prepare
PUT  /api/v1/vms/import/{vm_id}/disk
POST /api/v1/vms/import/{vm_id}/register
```

### VNC WebSocket Proxy
```
WebSocket /websockify?token=<jwt>
```

Proxies WebSocket connections to the VM's VNC port. Token is a short-lived JWT from the backend's console-token endpoint.

## Startup Behavior

On startup, the host agent:

1. Connects to libvirt
2. Applies egress filtering rules (`_ensure_egress_filtering()`)
3. Starts listening on port 8443 with TLS

## Services Architecture

```python
# Three singleton services
libvirt_svc = LibvirtService()   # VM lifecycle via libvirt
storage_svc = StorageService()   # Disk image management
cloudinit_svc = CloudInitService() # Cloud-init seed generation
```

### LibvirtService (`services/libvirt_service.py`)
- Manages libvirt connection
- VM define/start/stop/delete
- Snapshot management
- VNC port detection

### StorageService (`services/storage_service.py`)
- Clone base images (qcow2 backing file)
- Resize disks
- Delete storage

### CloudInitService (`services/cloudinit_service.py`)
- Generate cloud-init seed ISO
- User-data, meta-data, network-config
