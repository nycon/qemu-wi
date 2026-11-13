# Cloud Images

QEMU Interface is **cloud-init first**: Linux VMs are normally created from **cloud images** (pre-built disk images with cloud-init support), not from ISO installers.

You register images once in the panel (**Admin → Cloud Images** / Storage). After the download is `ready`, they appear in the VM create wizard.

## What you can download

Use **official amd64 / x86_64** cloud images on typical KVM hosts. Avoid `arm64` / `aarch64` filenames on x86_64 servers.

| Distro | Recommended image | Direct download URL |
|---|---|---|
| **Ubuntu 24.04 LTS** (Noble) | `noble-server-cloudimg-amd64.img` | https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img |
| **Ubuntu 22.04 LTS** (Jammy) | `jammy-server-cloudimg-amd64.img` | https://cloud-images.ubuntu.com/releases/22.04/release/jammy-server-cloudimg-amd64.img |
| **Debian 12** (Bookworm) | `debian-12-generic-amd64.qcow2` | https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2 |
| **Debian 13** (Trixie, when available) | check “generic amd64” | https://cloud.debian.org/images/cloud/ |

Catalog / overview pages (browse other releases):

- Ubuntu: https://cloud-images.ubuntu.com/
- Debian: https://cloud.debian.org/images/cloud/
- Fedora Cloud: https://fedoraproject.org/cloud/download
- AlmaLinux: https://wiki.almalinux.org/cloud/Generic-cloud.html
- Rocky Linux: https://rockylinux.org/download (Cloud images)

> Tip: Prefer URLs that contain `amd64` or `x86_64` in the filename. The panel rejects clear ARM images on x86 hosts.

## How to add an image in the panel

1. Open **Cloud Images** (admin).
2. Choose **Add / Import by URL** (or upload a local `.img` / `.qcow2`).
3. Fill in roughly:
   - **Name**: e.g. `Ubuntu 24.04 LTS` or `Debian 12 Bookworm`
   - **OS type**: `linux`
   - **OS version**: e.g. `24.04` or `12`
   - **Architecture**: `x86_64`
   - **Format**: `qcow2` (Ubuntu `.img` cloud images are usually qcow2 under the hood)
   - **Download URL**: one of the links above
4. Wait until **download status** is `ready` (progress is shown; Celery worker downloads in the background).
5. Create a VM and select that image.

### Via API

```bash
# Login, then:
curl -X POST https://PANEL/api/v1/cloud-images \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Debian 12 Bookworm",
    "os_type": "linux",
    "os_version": "12",
    "architecture": "x86_64",
    "format": "qcow2",
    "min_vcpus": 1,
    "min_ram_mb": 512,
    "min_disk_gb": 10,
    "download_url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
  }'
```

If a download fails, use **Retry download** in the UI or:

`POST /api/v1/cloud-images/{id}/retry-download`

## Where files are stored

Downloaded images land under:

```text
/var/lib/qemu-interface/images/
```

Example names:

```text
linux-24.04-x86_64-<id>.img
linux-12-x86_64-<id>.qcow2
```

VM disks (clones) are created in the storage pool, usually:

```text
/var/lib/qemu-interface/storage/<vm-id>/disk.qcow2
```

## Guest defaults after create

New **Linux cloud-image** VMs always get guest-side defaults via cloud-init, including:

- `qemu-guest-agent` (installed + `systemctl enable --now`) — required for live RAM/CPU stats and guest password reset; do **not** add it again under Packages
- SSH keepalives (idle sessions stay usable without client config)
- Soft fail2ban for SSH
- Optional gateway keepalive (helps on Hetzner bridged / vSwitch setups)

You still pass your own SSH keys / password / **extra** packages in the create wizard. ISO/Windows installs do not use this cloud-init package path.

## ISO installs

ISO install is supported for custom OSes (e.g. Windows). For Linux servers, **cloud images are preferred**: faster create, no interactive installer, networking and users via cloud-init.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Download stuck / failed | Worker logs: `docker compose logs worker`; retry download |
| “Could not detect architecture” | Use an `amd64` URL; current builds fall back to the declared arch for known amd64 URLs |
| Image `ready` but VM won’t boot | Confirm host is x86_64 KVM; check Host Agent / libvirt logs |
| Wrong image listed | Ensure `is_active` and download_status `ready` |

Also see [Troubleshooting](Troubleshooting.md) and [API Reference – Cloud Images](API-Reference.md).
