# Security

## Overview

QEMU Interface implements multiple layers of security:

1. **Authentication** – JWT tokens, 2FA (TOTP), API keys
2. **Authorization** – Role-based access control (RBAC) with tenants
3. **Network Security** – Egress filtering, iptables, TLS everywhere
4. **Audit Logging** – All actions are logged with actor, timestamp, details
5. **VM Isolation** – KVM hardware virtualization, network isolation
6. **Data at rest** – User SSH public keys Fernet-encrypted in the database

## Authentication

### JWT Tokens
- **Access token**: 15 minutes expiry
- **Refresh token**: 7 days expiry
- Tokens are signed with `JWT_SECRET_KEY` (auto-generated, 64 chars)

### User Profile (`/profile`)
Account security lives here (not under Settings):

- Display name, email, password change
- **2FA** setup / disable (all roles)
- **API keys** create / revoke (all roles)
- Per-user **SSH public keys** (max 20): Fernet-encrypted (`users.ssh_public_keys_enc`), decrypted only for the owner
- On VM create, select a saved key from a dropdown (raw key is not shown) or paste manually

**Settings** keeps the tenant notification email (per organisation; superadmin can edit all tenants), per-user notification toggles, SMTP (superadmin), SSL/domain, and diagnostics, with a short link to User Profile.

### Two-Factor Authentication (2FA)
- TOTP-based (Google Authenticator, Authy, etc.)
- Enable/disable only via **User Profile** (`/profile`)
- Required on login when enabled

### API Keys
- Long-lived tokens for API automation (`X-API-Key: qi_…`)
- Managed only via **User Profile** (`/profile`)
- Inherit the creating user's role and permissions
- **Superadmin**: may create a **global** key (all tenants) or a tenant-scoped key
- **Tenant admin / user**: key **must** be scoped to a tenant they belong to; API calls are limited to that tenant and their role
- Tenant-scoped keys cannot call global superadmin-only endpoints even if the owner is a superadmin

### Password Requirements (VM Passwords)
When creating VMs with password authentication:
- Minimum 8 characters
- At least one uppercase letter (A-Z)
- At least one lowercase letter (a-z)
- At least one digit (0-9)
- At least one special character (!@#$%^&* etc.)
- Password generator available in the UI

## Authorization (RBAC)

### Roles
| Role | Permissions |
|---|---|
| **Superadmin** | Full access to everything, system settings, user management |
| **Tenant Admin** | Manage VMs, projects, users within their tenant (assignable on tenant create via `admin_user_id`, or later under Users) |
| **User** | Create and manage own VMs within assigned projects |
| **Read-only** | View VMs and resources, no modifications |

### UI least privilege (superadmin-only details)
The following are **hidden from non-superadmins** in the VM UI (operators still get IP / gateway / DNS):

- MAC address and bridge name
- VNC port
- Raw cloud-init / netplan JSON (collapsed “Show raw config” for superadmins only)

### Multi-Tenant Isolation
- Each tenant has isolated VMs, projects, and resources
- **Projects** are optional folders inside a tenant. A `Default` project is created with every tenant. The UI hides project pickers/filters while a tenant has only one project; create extra projects only to split teams or environments.
- Users can belong to multiple tenants with different roles
- Superadmins can access all tenants

### Port forwarding
- DNAT port-forward rules apply to **NAT** networks only
- Public/external VMs show an explanation instead of the rule editor

## Network Security

### VM Egress Filtering (Anti-Portscan)

Prevents VMs from performing port scans or connecting to unauthorized services.

**How it works:**
- Custom `VM_EGRESS` iptables chain with whitelist approach
- Applied to all traffic from private subnets (10.0.0.0/8, 192.168.0.0/16, 172.16.0.0/12) going to the public internet
- VM-to-VM traffic on internal networks is always allowed

**Default allowed ports:**

| Port | Protocol | Service |
|---|---|---|
| 53 | TCP/UDP | DNS |
| 80 | TCP | HTTP |
| 443 | TCP | HTTPS |
| 22 | TCP | SSH |
| 25, 465, 587 | TCP | SMTP |
| 143, 993 | TCP | IMAP |
| 110, 995 | TCP | POP3 |
| 123 | UDP | NTP |
| 21, 990 | TCP | FTP |
| 3306 | TCP | MySQL |
| 5432 | TCP | PostgreSQL |
| 6379 | TCP | Redis |
| 8080, 8443 | TCP | HTTP/S Alt |
| ICMP | - | Ping |

**Blocked (examples):**
- Port 3389 (RDP) – prevents RDP scanning
- Port 445 (SMB) – prevents SMB/NetBIOS attacks
- Port 23 (Telnet) – prevents Telnet scanning
- All other unlisted ports

**Rate Limiting:**
- Maximum 20 new outgoing connections per second per VM
- Burst allowance: 40
- Prevents automated scanning even on allowed ports

**Logging:**
- Blocked attempts are logged to kernel log with prefix `VM_EGRESS_BLOCKED:`
- View via: `journalctl -k | grep VM_EGRESS_BLOCKED`
- Or via admin panel: **Firewall / Egress > Blocked Traffic Log**

**Management:**
- Admin panel: **Firewall / Egress** page
- API: `GET/PUT /system/security/egress-rules`
- Config file: `/etc/qemu-interface/egress-rules.json`
- Applied automatically on Host Agent startup

### TLS/SSL

| Connection | TLS |
|---|---|
| Browser → Nginx | Let's Encrypt or custom cert (port 443) |
| Nginx → Backend | Internal Docker network (no TLS needed) |
| Backend → Host Agent | Self-signed cert on port 8443; verified via install CA (`/etc/qemu-interface/agent-cert.pem` → container `/app/agent-ca.pem`) |
| Host Agent TLS cert | Auto-generated at install (`/etc/qemu-interface/agent-*.pem`) |

### Host Firewall

The installer configures:
- **UFW** or **firewalld** (whichever is present)
- Opens **publicly:** 80, 443 (and typically SSH)
- Does **not** open **8443** to the world (localhost + Docker ranges only)
- Applies persistent iptables chain `QI_AGENT_INPUT` via `scripts/lock-agent-port.sh` / `qemu-interface-agent-firewall.service` (works even if UFW is inactive)
- VNC is reached via Nginx reverse proxy – do not publish 6080–6180

## Audit Logging

All significant actions are logged:

| Action | Details Recorded |
|---|---|
| Login/logout | Success/failure, IP address |
| VM operations | Create, delete, start, stop, resize |
| User management | Create, update, deactivate |
| Security changes | Egress rules updated, SSL changes |
| Backup operations | Create, restore, delete |

View audit logs:
- Admin panel: **Audit Logs** page
- API: `GET /audit-logs`
- Export: CSV or JSON via **Audit Logs > Export**

## Hardening Checklist

For production deployments:

- [ ] Install with `sudo bash install.sh` (do **not** rely on raw `docker-compose.yml` defaults on the internet)
- [ ] Set up SSL/TLS via Let's Encrypt or custom certificate (avoid plain HTTP on the public internet)
- [ ] Save the generated admin password from `/etc/qemu-interface/secrets` (or the installer summary)
- [ ] Enable 2FA for admin / superadmin accounts
- [ ] Confirm 8443 is not public: `sudo ufw status` / `ss -tlnp | grep 8443`
- [ ] Restrict CORS / allowed hosts to your panel domain when possible
- [ ] Verify egress filtering is active: `iptables -L VM_EGRESS -n`
- [ ] Use SSH key authentication (no passwords) for VMs
- [ ] Set `PermitRootLogin prohibit-password` in VM SSH configs
- [ ] Review audit logs regularly
- [ ] Keep system updated: `apt update && apt upgrade`
- [ ] Monitor blocked egress attempts for compromised VMs
- [ ] Use internal networks for VM-to-VM communication (no public exposure)

## Incident Response

If a VM is compromised:

1. **Stop the VM**: `virsh destroy <domain-name>`
2. **Check egress log**: `journalctl -k | grep VM_EGRESS_BLOCKED`
3. **Identify the VM**: Check which domain was active during the incident
4. **Audit trail**: Check audit logs for who created/accessed the VM
5. **Forensics**: Mount disk image read-only: `guestmount -a disk.qcow2 -i --ro /mnt/forensic`
6. **Decision**: Delete and recreate VM, or clean up manually
7. **Report**: If required by hosting provider (e.g., Hetzner abuse report)

## Password Reset

### Admin User
```bash
cd /opt/qemu-interface
docker compose exec -T -e ADMIN_PASSWORD="NewSecurePass123!" backend python -m scripts.seed
```

### Any User (via Database)
```bash
docker compose exec -T backend python -c "
import asyncio
from app.core.security import hash_password
from app.core.database import async_engine
from sqlalchemy import text
async def reset():
    pw = hash_password('NewPassword123!')
    async with async_engine.begin() as conn:
        await conn.execute(text('UPDATE users SET hashed_password = :pw WHERE email = :e'), {'pw': pw, 'e': 'user@example.com'})
asyncio.run(reset())
"
```
