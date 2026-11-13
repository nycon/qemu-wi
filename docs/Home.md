# QEMU Interface – Documentation

A complete web-based management platform for QEMU/KVM virtual machines with multi-tenant support, built with FastAPI, React, and libvirt.

## Table of Contents

| Document | Description |
|---|---|
| [Architecture](Architecture.md) | System architecture, components, data flow |
| [Installation](Installation.md) | Step-by-step installation guide |
| [Configuration](Configuration.md) | All configuration files and environment variables |
| [Backups](Backups.md) | Live VM backups, FTP/S3 storage, DB dumps, schedules |
| [Cloud Images](Cloud-Images.md) | Recommended Ubuntu/Debian download URLs and how to import them |
| [Hetzner Dedicated Bridging](Hetzner-Dedicated-Bridging.md) | Step-by-step `br0` setup with vSwitch + additional subnet |
| [CLI](CLI.md) | qemu-cli installation, contexts, commands, output formats |
| [API Reference](API-Reference.md) | Backend REST API endpoints |
| [Host Agent](Host-Agent.md) | Host Agent API and functionality |
| [Security](Security.md) | Firewall, egress filtering, authentication, hardening |
| [Troubleshooting](Troubleshooting.md) | Common errors and solutions |
| [Development](Development.md) | Development setup, code structure, contributing |

Docs live in this repo under `docs/` (MkDocs). GitHub Actions / Pages deploy is disabled.

## Quick Start

Clone and run:

```bash
git clone https://github.com/nycon/qemu-wi.git /opt/qemu-interface
cd /opt/qemu-interface
sudo bash install.sh
```

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript, TailwindCSS, Vite |
| Backend API | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic |
| Task Queue | Celery + Redis |
| Database | PostgreSQL 16 |
| Host Agent | Python 3.12, FastAPI, libvirt-python |
| Virtualization | QEMU/KVM via libvirt |
| Reverse Proxy | Nginx 1.27 |
| Containerization | Docker Compose |

## Feature Overview

- **VM Management** – Create, start, stop, reboot, delete, resize, clone, export/import
- **VNC Console** – Browser-based console via noVNC WebSocket proxy
- **Snapshots & Backups** – Snapshots; **live** full-disk backups (running or stopped); restore when stopped; schedules + retention (local wall-clock times); Local/FTP/S3 storage; superadmin inventory delete; DB dumps ([Backups](Backups.md))
- **Cloud-Init** – Automatic VM configuration (users, SSH keys, packages, network); **qemu-guest-agent** always installed on Linux cloud images; ISO install also supported; profile SSH keys via dropdown on create
- **User Profile** – TopBar `/profile`: name, email, password, **2FA (TOTP)**, **API keys** (role-scoped), **Fernet-encrypted SSH public keys**
- **Settings** – Tenant notification email (superadmin: all tenants), per-user notification toggles, SMTP/SSL/diagnostics (account security is on User Profile, not duplicated here)
- **Multi-Tenant RBAC** – Tenants (optional tenant admin on create/edit), optional projects (folders; most tenants only need auto-created Default), roles (superadmin, admin, user, read-only)
- **Networking** – Tenant self-service NAT/Internal (optional auto CIDR, editable DHCP; no auto default NAT); superadmin assigns concrete public IPs; Admin **Public IPs** = external pools only; VM create picks one public IP without showing the pool CIDR; port forwarding (**NAT only**)
- **Notifications** – Tenant notification email + per-user toggles; SMTP **From** is the platform sender (not the tenant inbox)
- **Storage** – Multiple storage pools (tenant-assignable), cloud image management, disk hotplug ([Cloud Images guide](Cloud-Images.md))
- **Reverse Proxy** – Nginx-based reverse proxy with automatic SSL via Let's Encrypt
- **Load Balancer** – TCP/HTTP load balancing across VMs (Round Robin, Least Conn, IP Hash); one public VIP + NAT backends
- **Cluster** – Multi-node management from a single dashboard, automatic health checks
- **Security** – Egress filtering (anti-portscan), 2FA (TOTP), httpOnly JWT cookies, API keys, Host Agent lockdown, audit logging; UI hides MAC/bridge/VNC/raw netplan from non-superadmins
- **VM Templates** – Predefined configurations for quick VM deployment
- **SSL/TLS** – Automatic Let's Encrypt or custom certificate support
- **Monitoring** – Real-time VM stats (CPU, RAM, disk, network I/O) via qemu-guest-agent
- **Task System** – Async task execution with progress tracking
- **CLI** – [`qemu-cli`](CLI.md) (≥ 1.1.0) for VMs, tenant networks, public IPs, and admin IP assignment
