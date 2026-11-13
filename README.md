# QEMU Interface – Virtualization Panel

A modern, open-source virtualization web panel for managing **QEMU/KVM Virtual Machines** on bare-metal servers. The system is fully **Cloud-Init-centric** and exclusively uses Cloud Images as the base for VMs.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Nginx Reverse Proxy                   │
│                (TLS, Rate Limiting, CORS)                │
├──────────────────────┬──────────────────────────────────┤
│   Frontend (React)   │        Backend (FastAPI)          │
│   - Dashboard        │   - Auth API (JWT + API-Keys)    │
│   - VM Management    │   - Tenant/Project API           │
│   - VM Resize        │   - VM API + Snapshots           │
│   - VM Snapshots     │   - IPAM + Port-Forwarding       │
│   - VM Cloning       │   - Storage + Image-Upload       │
│   - VM Backups       │   - Task API                     │
│   - Scheduled Bkps   │   - Audit API + Export           │
│   - Multi-NIC        │   - User/Tenant Management       │
│   - Console (VNC)    │   - API-Key Management           │
│   - Admin Portal     │   - 2FA (TOTP)                   │
│   - User Profile     │   - Backup & Restore             │
│   - 2FA / API-Keys   │   - Scheduled Backups + Celery   │
│   - SSH Keys (enc.)  │   - Encrypted user SSH keys      │
│   - Settings         │   - Email Notifications (SMTP)   │
│   - Email Alerts     │   - Reverse Proxy Management     │
│   - API Docs         │   - VM Templates API             │
│   - VM Templates     │   - Disk Management API          │
│   - Import/Export    │   - VM Import/Export API         │
│   - Disk Mgmt        │   - Load Balancer API            │
│   - Load Balancer    │   - Cluster Management API       │
│   - Cluster          │   - Egress Filtering API         │
│   - Firewall/Egress  │   - IP Blacklist / Fail2Ban      │
│   - IP Blacklist     │                                  │
├──────────────────────┼──────────────────────────────────┤
│   PostgreSQL         │   Redis + Celery Worker + Beat   │
│   (Database)         │   (Task Queue + Scheduler)       │
├──────────────────────┴──────────────────────────────────┤
│                  Host Agent (FastAPI)                    │
│   - libvirt/QEMU Interaction                            │
│   - Cloud-Init Seed Generation                          │
│   - Disk/Storage Operations                             │
│   - VM Snapshots, Cloning, Resize                       │
│   - VM Backup & Restore (full disk)                     │
│   - Reverse Proxy (Nginx vhost management)              │
│   - VNC Console (JWT-authenticated)                     │
│   - Live Metrics (via qemu-guest-agent)                 │
│   - Internal Networks (isolated, DHCP)                  │
│   - Multi-NIC Management                                │
│   - VM Egress Filtering (iptables whitelist)            │
│   - IP Blacklist (manual + Fail2Ban)                    │
│   - Load Balancer (Nginx upstream management)           │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Component       | Technology                         |
|-----------------|------------------------------------|
| Backend         | FastAPI (Python 3.12)              |
| Database        | PostgreSQL 16                      |
| Job Queue       | Redis 7 + Celery + Celery Beat     |
| Frontend        | React 19 + TypeScript + Tailwind   |
| State Mgmt      | Zustand                            |
| Virtualization  | QEMU/KVM via libvirt               |
| Cloud-Init      | NoCloud Datasource                 |
| Console         | noVNC / WebSocket                  |
| Reverse Proxy   | Nginx                              |
| Container       | Docker + Docker Compose            |

## Features

### VM Management
- **Cloud-Init First**: Prefer Ubuntu/Debian cloud images (amd64 on x86_64 hosts). ISO install is also supported for custom OS installs.
- **VM Creation**: Choose from Cloud Images with configurable hardware profiles
- **VM Lifecycle**: Start, Stop, Reboot, Force-Stop
- **VM Resize**: Change vCPUs, RAM and Disk on stopped VMs with free-form values (no RAM presets/slider); shows remaining tenant quota; disk can only grow
- **VM Snapshots**: Create, Restore, and Delete snapshots
- **VM Cloning**: Clone existing VMs with new identity and IP
- **VM Backup & Restore**: Full disk backups (compressed qcow2). **Create works while the VM is running** (live external snapshot + blockcommit) or stopped; **restore requires a stopped VM** with safety rollback
- **Backup storage backends**: Local host storage, optional **FTP/FTPS** or **S3-compatible** offload (with optional keep-local copy)
- **Admin backup inventory**: Superadmin list of all VM backups with Local / FTP / S3 filters; **delete from inventory** removes the same record as the VM UI (**Admin → Backups**)
- **Database backups**: Superadmin PostgreSQL dump create / download / import (**Admin → Backups**)
- **Scheduled Backups**: Automatic daily/weekly/monthly backups with retention (live backups for running VMs); schedule times use **local wall-clock** in the UI (stored as UTC)
- **VM Import/Export**: Export VMs as standalone compressed qcow2, import existing disk images with upload
- **Disk Management**: Create additional data disks, hot-attach/detach on running VMs, resize (grow only), delete
- **HTML5 Console**: Browser-based VNC access to VMs
- **Live Metrics**: Real-time CPU, RAM, Disk I/O, and Network per VM (via qemu-guest-agent)

### Networking
- **Tenant Networks** (`/networks`): Tenants create **NAT** and **Internal** networks themselves (optional advanced CIDR/DHCP); no auto-created default NAT — until then only assigned public IPs are visible
- **Public IPs**: Superadmin assigns **concrete reserved public IPs** per tenant (Admin → Tenants); tenants pick one IP per VM in the create dropdown (no public CIDR/gateway leaked)
- **Admin Public IP Pools** (`/admin/ip-pools`): Platform-wide **external** pools only (host bridge + CIDR); NAT/Internal are not created here
- **NAT Networks**: Private IPs with SNAT; DHCP on/off and range editable (tenant edit + create)
- **Internal Networks**: Isolated VM-to-VM communication without internet access (with DHCP)
- **Multi-NIC**: Add/remove secondary network interfaces per VM
- **Port-Forwarding**: Forward incoming traffic to **NAT** VMs only (hidden / explained for public/external VMs)
- **Reverse Proxy**: Domain-based routing to VMs (multiple websites on one IP), automatic SSL via Let's Encrypt
- **Load Balancer**: TCP/HTTP load balancing across multiple VMs with Round Robin, Least Connections, and IP Hash algorithms
- **Cluster**: Multi-node management – register remote QEMU Interface instances and manage all VMs from a single dashboard
- **Change VM IP**: Reassign IP addresses on stopped VMs
- **IPAM**: Pool-based allocation; public pools managed by superadmin, private nets by tenants

### Cloud-Init
- **Keyboard Layout**: Select on creation (DE, CH, AT, US, FR, etc.)
- **Locale & Timezone**: Automatic or manual configuration
- **SSH-Keys & Password**: Flexible authentication; pick **saved profile keys** via dropdown on VM create (or paste manually)
- **qemu-guest-agent**: Installed and enabled automatically on every Linux cloud-image VM (needed for live stats); not required in the Packages field
- **Custom Packages**: Extra packages only (e.g. `htop, curl, nginx`)
- **Custom Scripts**: Run commands after boot (runcmd)
- **SSH Keepalive (guest)**: New VMs get `ClientAliveInterval` + fail2ban via cloud-init so idle SSH stays up without client config. Use Panel VNC if SSH is unreachable.
- **Netplan privacy**: Raw cloud-init network JSON is collapsed and **superadmin-only**; everyone sees IP / gateway / DNS in the Network card

### Multi-Tenant & RBAC
- **Multi-Tenant**: Full tenant isolation
- **Roles**: Super-Admin, Tenant-Admin, User, Read-Only
- **Tenant Admin on create/edit**: Superadmin can assign or change the tenant admin under **Admin → Tenants** (`admin_user_id`)
- **Tenant Assignment**: Assign directly on user creation
- **Project Assignment (Per User)**: Assign one or multiple projects directly to specific users (labels show `Tenant / Project`)
- **VM Assignment (Per User)**: Assign one or multiple existing tenant VMs directly to specific users
- **Scoped Visibility**: Non-admin users see assigned projects (and their VMs/tasks), plus explicit VM assignments as override
- **Quota Management**: CPU, RAM, Storage, IP limits per tenant
- **Projects**: Optional folders for VMs inside a tenant; every tenant gets auto-created **Default**. The UI hides the project picker/filter while only one project exists

### User Management & Security
- **User Profile** (TopBar → User Profile `/profile`): Display name, email, password, **2FA**, **API keys**, encrypted SSH public keys — single place for account security
- **Settings**: Tenant notification email, per-user notification toggles, SMTP/SSL/diagnostics (superadmin) — not a second copy of profile/2FA/API keys
- **Encrypted SSH public keys**: Per-user keys stored Fernet-encrypted at rest; pick via dropdown on VM create
- **Two-Factor Authentication (2FA)**: TOTP with QR setup (Google Authenticator, Authy, 1Password) on the profile page
- **API keys**: Create / revoke with optional expiry; inherit the owner's role. Superadmin may create **global** or tenant-scoped keys; tenant admins/users must scope a key to their tenant
- **UI least privilege**: MAC address, bridge, VNC port, and raw netplan only for **superadmins**
- **Linux Distro** on VM detail: shown from the linked cloud image (instead of raw VM UUID)
- **Create Users**: With tenant and role assignment
- **Disable/Re-enable**: Temporarily lock user accounts
- **Permanent Delete**: Permanently remove users (with VM check)
- **Strong VM Passwords**: Enforced minimum 8 chars, uppercase, lowercase, digit, special character with one-click generator

### VM Egress Filtering (Anti-Portscan)
- **Whitelist Approach**: VMs can only connect outbound to allowed ports (HTTP, HTTPS, DNS, SSH, SMTP, etc.)
- **Blocked by Default**: RDP (3389), SMB (445), Telnet (23), and all other unlisted ports
- **Rate Limiting**: Max 20 new outgoing connections/sec per VM (prevents automated scanning)
- **Logging**: Blocked attempts logged to kernel log (`VM_EGRESS_BLOCKED:`)
- **Admin UI**: Manage allowed ports, rate limits, and view blocked traffic at **Admin > Firewall / Egress**
- **Persistent**: Rules survive reboots, auto-applied on Host Agent startup

### IP Blacklist / Fail2Ban
- **Fail2Ban (Automatic)**: Monitors SSH and API login attempts; auto-bans after configurable failed tries
- **Manual Bans**: Ban/unban IPs via Admin UI or API
- **Fail2Ban Jails**: SSH (sshd), API login (qemu-interface-api)
- **Live Configuration**: Change ban duration, detection window, and max retries per jail via web UI (applied instantly)
- **Duration Presets**: Quick-select buttons (5 min to permanent) or custom seconds input
- **IP Check**: Verify if a specific IP is currently banned
- **Fail2Ban Stats**: View currently banned, total banned, and failed attempts per jail
- **Fail2Ban Log**: Real-time log viewer in admin panel
- **Persistent Blacklist**: Manual bans stored in `/etc/qemu-interface/ip-blacklist.json`
- **Admin UI**: Full management at **Admin > IP Blacklist**

### Email Notifications
- **SMTP Configuration**: Configure via UI (Settings) or `.env` file — **From** address is the platform sender for all mails
- **Tenant notification email**: Per-organisation inbox for VM/backup/quota alerts (Settings; superadmin can edit all tenants under Settings and Admin → Tenants)
- **Per-User Preferences**: Toggle each notification type on/off (user inbox / prefs)
- **Notification Events**:
  - VM provisioning errors
  - VM successfully created
  - Backup completed / failed
  - Quota warnings (approaching limits)
  - Login from new IP address
  - 2FA enabled / disabled
- **Test Email**: Verify SMTP configuration with one click
- **HTML Templates**: Professional, styled email notifications

### Scheduled Backups
- **Frequencies**: Daily, Weekly (choose day), Monthly (choose date)
- **Time Selection**: Choose backup time in your **local timezone** (stored as UTC; audit log displays local time)
- **Retention Policy**: Automatic cleanup – keep N latest backups, delete older ones
- **Live-capable**: Running VMs are backed up live (no stop required for create/schedules)
- **Pause/Resume**: Temporarily disable schedules without deleting
- **Status Tracking**: Last run status, next run time, trigger history
- **Celery Beat**: Background scheduler checks every 60 seconds for due backups
- Details: [`docs/Backups.md`](docs/Backups.md)

### VM Templates / Presets
- **Quick Start**: Pre-configured VM templates for one-click setup (Web Server, Database, Dev Box, etc.)
- **Hardware Presets**: Saved vCPU, RAM, Disk configurations
- **Cloud-Init Defaults**: Pre-configured packages, commands, SSH keys, timezone, keyboard layout
- **Cloud Image Binding**: Optionally bind a template to a specific cloud image
- **Global vs Tenant**: Superadmins can create global templates; users create tenant-scoped ones
- **Template Management**: Full CRUD via Admin > VM Templates

### Load Balancer
- **Protocols**: HTTP, HTTPS, and TCP load balancing
- **Algorithms**: Round Robin, Least Connections, IP Hash
- **Targets**: Add/remove VMs as backend targets with configurable port and weight
- **Health Checks**: Configurable health check path and interval
- **Enable/Disable**: Toggle load balancers on/off without deleting
- **Nginx-Based**: Generates Nginx upstream + server blocks on the host, auto-reload
- **Admin UI**: Full management at **Admin > Load Balancer**

### Cluster (Multi-Node)
- **Multi-Node Management**: Register remote QEMU Interface instances and manage all from one dashboard
- **Zero Config on Remote Nodes**: Remote nodes only need an API key – no plugins, no code changes
- **Aggregated Overview**: Total VMs, vCPUs, RAM, Storage across all nodes at a glance
- **Node Health Monitoring**: Automatic health checks every 2 minutes via Celery Beat
- **Live Sync**: Manual or automatic sync of node status and resources
- **Cross-Node VM List**: See all VMs across all nodes in a single filterable table
- **Resource Bars**: Per-node CPU, RAM, and Storage utilization with visual progress bars
- **Independent Nodes**: Each node runs standalone – cluster master failure does not affect nodes
- **Admin UI**: Full management at **Admin > Cluster**

### VM Import/Export
- **Export**: Download stopped VMs as compressed standalone qcow2 (no backing file dependencies)
- **Import**: Upload existing qcow2/img disk images to create VMs
- **Import Flow**: Configure hardware → upload disk → auto-register in libvirt

### Disk Management
- **Additional Disks**: Create and attach extra data disks to VMs (for databases, logs, etc.)
- **Hot-Attach/Detach**: Add or remove disks from running VMs without restart
- **Resize**: Grow disks online (expand only, no shrink)
- **Auto-Device**: Automatically assigns next available device name (vdb, vdc, ...)
- **Using a new disk inside the VM**:

```bash
lsblk
mkfs.ext4 /dev/vdb
mkdir -p /mnt/data
mount /dev/vdb /mnt/data
echo '/dev/vdb /mnt/data ext4 defaults 0 2' >> /etc/fstab
```

### Storage & Images
- **Cloud Images**: Manage and download (URL-based or file upload)
- **Recommended images**: Ubuntu 24.04 / 22.04 and Debian 12 – see [`docs/Cloud-Images.md`](docs/Cloud-Images.md) for direct download links
- **Storage Pools**: Configurable storage locations for VM disks; Superadmins can assign pools to a tenant or leave them shared
- **Live Disk Usage**: Pool view shows actual VM storage vs. filesystem capacity with dual progress bar
- **Image Upload**: Upload local Cloud Images

### UI / UX
- **Dark Mode**: Toggle between Light and Dark theme (persisted in browser)
- **Responsive Design**: Works on Desktop and Tablet
- **English UI**: Fully translated interface
- **Dashboard Charts**: Donut chart for VM status, bar charts for resource usage
- **User-Scoped Dashboard**: Non-admin users see only resources from assigned projects and explicitly assigned VMs
- **API Documentation**: Interactive API docs page with endpoint explorer

### System
- **Task System**: Asynchronous VM operations with status tracking
- **Audit Trail**: Immutable log of all actions with CSV/JSON export
- **API-First**: Full REST API with OpenAPI documentation
- **API-Keys**: Programmatic access via `X-API-Key` header (Terraform, CI/CD, Scripts)
- **Auto-Migration**: Database schema is automatically updated

## Quick Start

> **Production:** always use `install.sh`. It generates strong secrets, hardens the firewall (8443 not public), and seeds an admin password.  
> **Do not** put a bare `docker compose up` with repository defaults on the public internet.

The included `install.sh` script installs the **complete panel** on a bare-metal server.

```bash
git clone https://github.com/nycon/qemu-wi.git /opt/qemu-interface
cd /opt/qemu-interface
sudo bash install.sh
```

The script runs through 14 phases:

| Phase | Description |
|-------|-------------|
| 0 | Check prerequisites (OS, KVM, RAM, Disk) |
| 1 | Install system packages (qemu-kvm, libvirt, cloud-image-utils, rsync) |
| 2 | Install Docker + Docker Compose |
| 3 | Configure libvirt |
| 4 | Create directories (storage, images, backups, exports, proxy-configs) |
| 5 | Deploy source code |
| 6 | Generate configuration + secrets (reuses existing secrets on re-run) |
| 6b | Generate Nginx config (auto-detects SSL certificates) |
| 7 | Create TLS certificates for Host Agent (with SANs for Docker) |
| 8 | Install Host Agent (Python venv + systemd service) |
| 9 | Build and start Docker containers |
| 10 | Initialize database (Alembic migrations + Admin seed) |
| 11 | Start Host Agent (via systemd) |
| 12 | Check networking + optional bridge setup |
| 13 | Firewall + VM Egress Filtering + Fail2Ban setup |
| 14 | Status check + summary |

### Prerequisites

- Linux bare-metal server (Ubuntu 22.04+, Debian 12+, Rocky 9+, Fedora, CentOS)
- Minimum 4 GB RAM, 20 GB Disk
- KVM support (check: `ls /dev/kvm`)
- Root access

### Networking (Auto-Detect)

The installer automatically detects your network setup and offers the best configuration:

**Normal Subnet (/24, /16, etc.) – Home server, Datacenter, Netcup, Contabo:**
- Installer offers to create a **bridge (br0)** automatically
- Supports **Netplan** (Ubuntu/Debian), **ifupdown** (legacy Debian), **NetworkManager** (Fedora/CentOS/RHEL)
- Creates backup of existing network config before changes
- VMs can get their own IPs directly on the network (External mode)
- NAT mode also available

**Point-to-Point /32 – Hetzner, OVH, SoYouStart:**
- No bridge possible (would break connectivity)
- **NAT networks** work without issues (VMs get private IPs)
- VMs accessible from outside via **port-forwarding**
- For direct public IPs: Works on most providers and home networks with a standard subnet (Note: Hetzner /32 point-to-point routing does not support br0 – use NAT mode instead)

### After Installation

- **Web-UI**: `http://<SERVER-IP>` (or HTTPS if SSL was configured)
- **API Docs**: disabled by default; set `EXPOSE_API_DOCS=true` (or `DEBUG=true`) in `backend/.env` / compose override
- **Login**: `admin` / (password shown at end of installation)

### Update

After `git pull`, re-run the same installer — it is idempotent and keeps secrets, DB, and VMs:

```bash
cd /opt/qemu-interface
git pull origin main
sudo bash install.sh
```

Shorter rebuild/restart only (skips full phase walk):

```bash
sudo bash install.sh --update
```

SSL vhosts live under `/etc/qemu-interface/nginx-platform.conf` (not in git).
`deploy/nginx/nginx.conf` stays pull-safe.

### Manual Installation

Manual steps are for **development / lab** only. For any internet-facing host, use `install.sh`.

If you prefer to run each step manually:

```bash
# 1. Clone repo
git clone https://github.com/nycon/qemu-wi.git /opt/qemu-interface
cd /opt/qemu-interface

# 2. Configure environment (required: strong SECRET_KEY, JWT_SECRET_KEY, HOST_AGENT_TOKEN)
cp backend/.env.example backend/.env
# Edit secrets in .env – never use change-me / qemu defaults on a public host!

# 3. Start Docker services
docker compose up -d

# 4. Initialize database + admin user
docker compose exec backend python -m scripts.seed

# 5. Install Host Agent
cd host-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp systemd/qemu-interface-agent.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now qemu-interface-agent
```

### Celery Beat (Scheduled Backups)

To enable scheduled backups, the Celery worker must run with the `-B` (Beat) flag:

```bash
celery -A app.workers.celery_app worker -B -l info -Q vm_operations,storage_operations
```

If using the systemd service, ensure `-B` is in the `ExecStart` command.

### SSL/TLS Setup

The installer automatically detects existing SSL certificates. On first install, the panel runs in HTTP-only mode.

```bash
# Create Let's Encrypt certificate (port 80 must be reachable)
certbot certonly --webroot -w /var/lib/letsencrypt -d your-domain.com

# Re-run installer – auto-detects certificate and enables HTTPS
cd /opt/qemu-interface && sudo bash install.sh
```

The installer checks these sources (in order):
1. **Let's Encrypt**: `/etc/letsencrypt/live/*/fullchain.pem`
2. **Custom Certificates**: `deploy/nginx/certs/*.pem` + `*.key`

If no certificate is found, the panel remains accessible via HTTP.

### Email Notifications (SMTP)

Configure via `.env` or the Settings UI:

```env
SMTP_ENABLED=true
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@example.com
SMTP_PASSWORD=your-smtp-password
SMTP_FROM_EMAIL=noreply@example.com
SMTP_FROM_NAME=QEMU Interface
SMTP_USE_TLS=true
SMTP_ALLOW_INVALID_CERT=false
```

Alternatively, configure at runtime via **Settings > SMTP Configuration** (superadmin only).

Notes:
- Use the SMTP server hostname that matches the TLS certificate SAN/CN.
- `SMTP_PORT=587` uses STARTTLS, `SMTP_PORT=465` uses implicit SSL.
- For provider setups with mismatched certificates (some Plesk deployments), you can temporarily enable **Allow invalid TLS certificate (insecure)** in the UI.

### Managing the Host Agent

The Host Agent runs as a **systemd service** on the KVM host (not inside Docker):

```bash
# Check status
sudo systemctl status qemu-interface-agent

# Restart
sudo systemctl restart qemu-interface-agent

# Stop / Start
sudo systemctl stop qemu-interface-agent
sudo systemctl start qemu-interface-agent

# View logs (live)
sudo journalctl -u qemu-interface-agent -f

# Last 100 log lines
sudo journalctl -u qemu-interface-agent -n 100 --no-pager
```

> **Important:** Always manage the Host Agent via `systemctl`. Do not start it manually with `nohup` or `&`. The systemd service automatically restarts the agent on server reboot and monitors the process.

### Viewing Logs

```bash
# Docker container logs
docker compose logs -f backend
docker compose logs -f worker
docker compose logs -f frontend
docker compose logs -f nginx

# Host Agent logs
sudo journalctl -u qemu-interface-agent -f

# Installer log
cat /var/log/qemu-interface-install.log
```

## CLI Tool

The official CLI lives in a separate repository: [`nycon/qemu-cli`](https://github.com/nycon/qemu-cli) (**≥ 1.1.0** for networks / public IPs). API keys: **User Profile → API Keys**.

```bash
# Prebuilt: https://github.com/nycon/qemu-cli/releases
git clone https://github.com/nycon/qemu-cli.git
cd qemu-cli && make install

qemu-cli context create production \
  --url https://panel.example.com \
  --token qi_your_api_key

qemu-cli tenant use Default
qemu-cli network create web-dmz --type nat
qemu-cli tenant public-ips
qemu-cli vm create web1 --image "Ubuntu 24.04" --project <uuid> --public-ip 159.69.172.28 --random-password
qemu-cli vm list
```

Details: [`docs/CLI.md`](docs/CLI.md) and the [qemu-cli README](https://github.com/nycon/qemu-cli#readme).

## Project Structure

```
qemu-interface/
├── backend/                    # FastAPI Control Plane
│   ├── app/
│   │   ├── api/v1/endpoints/  # API Endpoints
│   │   │   ├── auth.py        # Login, Token-Refresh, Logout, 2FA
│   │   │   ├── api_keys.py   # API-Key Management
│   │   │   ├── vms.py         # VM CRUD, Resize, Clone, NICs, Backups, Disks, Import/Export
│   │   │   ├── vm_templates.py # VM Templates CRUD
│   │   │   ├── network.py     # IPAM, Port-Forwarding
│   │   │   ├── storage.py     # Storage Pools, Cloud Images
│   │   │   ├── tenants.py     # Tenant/Project Management
│   │   │   ├── users.py       # User Management
│   │   │   ├── reverse_proxy.py # Reverse Proxy Rules + SSL
│   │   │   ├── load_balancer.py # Load Balancer CRUD + Target Management
│   │   │   ├── cluster.py      # Cluster Node CRUD + Aggregation
│   │   │   ├── notifications.py # Email Preferences + SMTP Config
│   │   │   ├── tasks.py       # Task Status
│   │   │   └── audit.py       # Audit Logs + Export
│   │   ├── core/              # Config, DB, Security, Dependencies, Agent TLS
│   │   ├── models/            # SQLAlchemy Models
│   │   ├── schemas/           # Pydantic Schemas
│   │   ├── services/          # Business Logic (VM, Quota, Email, Audit)
│   │   └── workers/           # Celery Tasks (Provisioning, Scheduler)
│   │       ├── vm_tasks.py    # VM lifecycle + backup tasks
│   │       ├── backup_scheduler.py # Scheduled backup + retention cleanup
│   │       └── celery_app.py  # Celery + Beat configuration
│   ├── scripts/               # Seed & Utility Scripts
│   └── tests/
├── frontend/                   # React/TypeScript Frontend
│   └── src/
│       ├── components/        # UI Components (Layout, Sidebar, etc.)
│       ├── pages/             # Pages
│       │   ├── DashboardPage  # Overview + Charts
│       │   ├── VMListPage     # VM List
│       │   ├── VMDetailPage   # VM Details, Metrics, Snapshots, Backups, Schedules
│       │   ├── VMCreatePage   # Create VM (incl. Templates, Keyboard/Locale)
│       │   ├── VMImportPage   # Import VM from disk image
│       │   ├── ConsolePage    # noVNC Console
│       │   ├── ProfilePage    # Account, password, 2FA, API keys, SSH keys
│       │   ├── SettingsPage   # Notifications, SMTP, SSL/domain, diagnostics
│       │   └── admin/         # Admin Pages (Users, Tenants, Public IPs, Backups, Reverse Proxy, Load Balancer, Cluster, VM Templates, Firewall, Blacklist, API Docs)
│       ├── services/          # API Client (Axios)
│       ├── store/             # Zustand State Management
│       └── types/             # TypeScript Types
├── host-agent/                 # Host Agent (runs on KVM host)
│   ├── agent/
│   │   └── main.py           # FastAPI Agent App
│   └── services/              # libvirt, Cloud-Init, Storage
├── deploy/
│   └── nginx/                 # Nginx Configuration
├── docs/                       # Project Documentation
├── install.sh                  # Automated Installation Script
└── docker-compose.yml
```

## API Overview

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/auth/login` | User login |
| `POST /api/v1/auth/refresh` | Refresh access token |
| `POST /api/v1/auth/logout` | Revoke refresh token |
| `POST /api/v1/auth/change-password` | Change password |
| `POST /api/v1/auth/verify-2fa` | Verify 2FA code during login |
| `POST /api/v1/auth/2fa/setup` | Generate 2FA secret + QR code |
| `POST /api/v1/auth/2fa/enable` | Enable 2FA (verify code) |
| `POST /api/v1/auth/2fa/disable` | Disable 2FA (requires password) |
| `GET /api/v1/api-keys` | List API keys (own keys) |
| `POST /api/v1/api-keys` | Create API key (`tenant_id` required unless superadmin global) |
| `DELETE /api/v1/api-keys/{id}` | Revoke API key |
| `GET /api/v1/tenants` | List tenants |
| `POST /api/v1/tenants` | Create tenant (optional `admin_user_id`) |
| `PATCH /api/v1/tenants/{id}` | Update tenant (optional `admin_user_id`) |
| `GET /api/v1/tenants/{id}/users` | List tenant members |
| `POST /api/v1/tenants/{id}/vms` | Create VM |
| `PATCH /api/v1/tenants/{id}/vms/{id}` | Update / Resize VM |
| `POST /api/v1/tenants/{id}/vms/{id}/action` | VM action (start/stop/reboot) |
| `POST /api/v1/tenants/{id}/vms/{id}/console` | Create console token |
| `POST /api/v1/tenants/{id}/vms/{id}/clone` | Clone VM |
| `GET /api/v1/tenants/{id}/vms/{id}/snapshots` | List snapshots |
| `POST /api/v1/tenants/{id}/vms/{id}/snapshots` | Create snapshot |
| `POST /api/v1/tenants/{id}/vms/{id}/secondary-nics` | Add secondary NIC |
| `DELETE /api/v1/tenants/{id}/vms/{id}/secondary-nics/{mac}` | Remove secondary NIC |
| `GET /api/v1/tenants/{id}/vms/{id}/backups` | List backups |
| `POST /api/v1/tenants/{id}/vms/{id}/backups` | Create backup (live or cold) |
| `POST /api/v1/tenants/{id}/vms/{id}/backups/{id}/restore` | Restore from backup (VM stopped) |
| `DELETE /api/v1/tenants/{id}/vms/{id}/backups/{id}` | Delete backup |
| `GET /api/v1/tenants/{id}/vms/{id}/backup-schedules` | List backup schedules |
| `POST /api/v1/tenants/{id}/vms/{id}/backup-schedules` | Create backup schedule |
| `PATCH /api/v1/tenants/{id}/vms/{id}/backup-schedules/{id}` | Update schedule |
| `DELETE /api/v1/tenants/{id}/vms/{id}/backup-schedules/{id}` | Delete schedule |
| `GET /api/v1/system/vm-backups` | Superadmin: all VM backups (filter Local/FTP/S3) |
| `GET /api/v1/system/vm-backups/summary` | Superadmin: backup counts by backend |
| `GET /api/v1/system/backup-storage` | Superadmin: Local/FTP/S3 storage config |
| `PUT /api/v1/system/backup-storage` | Superadmin: update backup storage backend |
| `POST /api/v1/system/backup-storage/test` | Superadmin: test FTP/S3 connectivity |
| `GET /api/v1/system/db-backups` | Superadmin: list database dumps |
| `POST /api/v1/system/db-backups` | Superadmin: create database dump |
| `GET /api/v1/system/db-backups/{id}/download` | Superadmin: download dump |
| `POST /api/v1/system/db-backups/import` | Superadmin: import dump |
| `DELETE /api/v1/system/db-backups/{id}` | Superadmin: delete dump |
| `POST /api/v1/tenants/{id}/vms/{id}/change-ip` | Change VM IP address |
| `GET /api/v1/tenants/{id}/vms/{id}/disks` | List additional disks |
| `POST /api/v1/tenants/{id}/vms/{id}/disks` | Create & attach new disk |
| `POST /api/v1/tenants/{id}/vms/{id}/disks/{id}/detach` | Detach disk (hot-detach) |
| `POST /api/v1/tenants/{id}/vms/{id}/disks/{id}/attach` | Re-attach disk (hot-attach) |
| `PATCH /api/v1/tenants/{id}/vms/{id}/disks/{id}/resize` | Resize disk (grow only) |
| `DELETE /api/v1/tenants/{id}/vms/{id}/disks/{id}` | Delete disk |
| `POST /api/v1/tenants/{id}/vms/{id}/export` | Export VM as qcow2 |
| `GET /api/v1/tenants/{id}/vms/{id}/export/download` | Download exported VM |
| `GET /api/v1/tenants/{id}/vms/{id}/export/status` | Check export status |
| `POST /api/v1/tenants/{id}/vms/import` | Start VM import |
| `POST /api/v1/tenants/{id}/vms/{id}/import/finalize` | Finalize VM import |
| `GET /api/v1/reverse-proxy` | List reverse proxy rules |
| `POST /api/v1/reverse-proxy` | Create proxy rule (domain → VM) |
| `PATCH /api/v1/reverse-proxy/{id}` | Update proxy rule |
| `DELETE /api/v1/reverse-proxy/{id}` | Delete proxy rule |
| `POST /api/v1/reverse-proxy/{id}/ssl` | Request SSL certificate |
| `GET /api/v1/notifications/preferences` | Get notification preferences |
| `PATCH /api/v1/notifications/preferences` | Update notification preferences |
| `GET /api/v1/notifications/smtp` | Get SMTP config (admin) |
| `PATCH /api/v1/notifications/smtp` | Update SMTP config (admin) |
| `POST /api/v1/notifications/smtp/test` | Send test email (admin) |
| `GET /api/v1/vm-templates` | List VM templates |
| `POST /api/v1/vm-templates` | Create VM template |
| `PATCH /api/v1/vm-templates/{id}` | Update VM template |
| `DELETE /api/v1/vm-templates/{id}` | Delete VM template |
| `GET /api/v1/ip-pools` | List IP pools (tenant scope hides public CIDR; admin creates external only) |
| `GET /api/v1/ip-pools/public/available` | Free public IPs assignable to tenants (superadmin) |
| `GET/POST /api/v1/tenants/{id}/networks` | Tenant NAT / Internal networks |
| `PATCH/DELETE /api/v1/tenants/{id}/networks/{pool_id}` | Edit / delete tenant network |
| `GET /api/v1/tenants/{id}/public-ips` | Public IPs assigned to this tenant |
| `PATCH /api/v1/tenants/{id}/notification-email` | Set tenant notification inbox |
| `POST /api/v1/port-forwards` | Create port-forwarding rule |
| `GET /api/v1/storage-pools` | Manage storage pools |
| `GET /api/v1/cloud-images` | Manage cloud images |
| `POST /api/v1/cloud-images/upload` | Upload cloud image |
| `GET /api/v1/tasks` | List tasks |
| `GET /api/v1/audit-logs` | View audit logs |
| `GET /api/v1/audit-logs/export` | Export audit logs (CSV/JSON) |
| `GET /api/v1/users` | Manage users |
| `GET /api/v1/users/assignable-projects` | List projects assignable to users |
| `GET /api/v1/users/project-assignments` | List current user-project assignments |
| `PUT /api/v1/users/{id}/assigned-projects` | Replace project assignments for a user |
| `GET /api/v1/system/security/egress-rules` | Get VM egress filter rules |
| `PUT /api/v1/system/security/egress-rules` | Update egress filter rules |
| `POST /api/v1/system/security/egress-rules/add-port` | Add allowed egress port |
| `DELETE /api/v1/system/security/egress-rules/remove-port` | Remove egress port |
| `GET /api/v1/system/security/egress-log` | View blocked egress attempts |
| `GET /api/v1/system/security/blacklist` | List all banned IPs |
| `POST /api/v1/system/security/blacklist/ban` | Ban an IP address |
| `POST /api/v1/system/security/blacklist/unban` | Unban an IP address |
| `GET /api/v1/system/security/blacklist/check/{ip}` | Check if IP is banned |
| `GET /api/v1/system/security/fail2ban/log` | View Fail2Ban log |
| `GET /api/v1/system/security/fail2ban/config` | Get Fail2Ban jail configuration |
| `PUT /api/v1/system/security/fail2ban/config` | Update jail settings (bantime, findtime, maxretry) |
| `GET /api/v1/system/generate-password` | Generate secure VM password |

Full API documentation: `/api/docs` (Swagger UI) or in-app at **Admin > API Docs**

## Network Concepts

### Who manages what
| Resource | Who | Notes |
|----------|-----|-------|
| Public / External pools | Superadmin | Admin → **Public IPs**; assign concrete IPs under **Tenants** |
| Assigned public IPs | Tenant | Visible under **Networks**; pick one per VM on create |
| NAT / Internal | Tenant admin | Self-service under **Networks** (create/edit/delete); optional auto CIDR |

### NAT Network
```
Internet ←→ Host (iptables SNAT) ←→ virbr0 (10.x.0.0/24) ←→ VM
                                     Port-forwarding for incoming traffic
```
Created by the tenant when needed (not auto-created).

### Internal Network (VM-to-VM)
```
VM A (Webserver) ←→ Internal Network (10.y.0.0/24, DHCP) ←→ VM B (MySQL)
        ↕
   NAT Network (for internet access)
```
VMs can have multiple NICs: one for external access (NAT or public), one for internal communication.

### External Network (Public IPs)
```
Internet ←→ Host Bridge (br0) ←→ VM (direct public IP)
```
Requires host bridge (`br0`). The installer can create this automatically on normal subnets.
Not available on /32 point-to-point setups (Hetzner/OVH). Tenants only see their assigned host addresses, not the pool CIDR.

## Security

- **JWT Authentication** with Refresh Tokens (single-use rotation)
- **Two-Factor Authentication (2FA)**: TOTP-based (RFC 6238), QR code setup on **User Profile**, compatible with all authenticator apps
- **API-Keys** for programmatic access (bcrypt-hashed, plaintext shown only on creation)
- **Refresh Token Revocation** via Redis blacklist
- **Logout Endpoint** for immediate token revocation
- **Email Alerts**: Notifications for login from new IPs, 2FA changes, VM errors
- Role-based access control (RBAC: Super-Admin, Tenant-Admin, User, Read-Only)
- No direct shell commands from the web layer
- Host Agent with token-based allowlist
- **VNC Console with JWT Token** (signed, short-lived, instead of raw VM ID)
- **TLS-verified Host Agent communication**: Self-signed cert with SANs (localhost, host.docker.internal, FQDN), verified by backend/worker
- **Backend/Frontend only on localhost**: Ports 8000/3000 bound to 127.0.0.1, only reachable via Nginx reverse proxy
- **PostgreSQL + Redis not exposed externally** (Docker-internal only)
- Rate Limiting via Nginx (API: 30r/s, Auth: 5r/s)
- Security Headers (X-Frame-Options, X-Content-Type-Options, XSS-Protection, Referrer-Policy)
- Public registration disabled by default (`ALLOW_PUBLIC_REGISTRATION=false`)
- Host Agent port 8443 not opened publicly by the installer (Docker/localhost only)
- Password hashing with bcrypt
- Audit trail for all actions
- **VM Egress Filtering**: iptables-based whitelist preventing VMs from performing port scans or unauthorized outbound traffic
- **Fail2Ban Integration**: Automatic IP banning after failed SSH/API login attempts
- **IP Blacklist**: Manual and automatic IP blocking with persistent storage
- **Strong VM Passwords**: Enforced complexity requirements with secure password generator

### API-Key Usage

```bash
# Create API key: User Profile → API Keys → New API Key
# Superadmin global key: all tenants. Tenant-scoped key: only that tenant + owner's role.
# Usage in scripts/integrations:
curl -H "X-API-Key: qi_abc123..." https://panel.example.com/api/v1/tenants

# Example: List VMs
curl -H "X-API-Key: qi_abc123..." \
  https://panel.example.com/api/v1/tenants/{tenant_id}/vms
```

## Documentation

- Source markdown: [`docs/`](docs/Home.md) (also buildable locally with `mkdocs serve`)
- **Docs**: Source lives under `docs/` (MkDocs). GitHub Actions are disabled for this repository.

| Doc | Description |
|-----|-------------|
| [Home](docs/Home.md) | Overview and feature list |
| [Architecture](docs/Architecture.md) | System diagram, components, data flow |
| [Installation](docs/Installation.md) | Detailed installation guide |
| [Configuration](docs/Configuration.md) | Config files, env vars, ports |
| [Backups](docs/Backups.md) | Live VM backups, FTP/S3, DB dumps, schedules |
| [Cloud Images](docs/Cloud-Images.md) | Ubuntu/Debian download URLs and import steps |
| [Hetzner Dedicated Bridging](docs/Hetzner-Dedicated-Bridging.md) | Public bridging / vSwitch setup |
| [API Reference](docs/API-Reference.md) | REST API documentation |
| [Host Agent](docs/Host-Agent.md) | Agent API and management |
| [Security](docs/Security.md) | Firewall, egress filtering, hardening |
| [Troubleshooting](docs/Troubleshooting.md) | Common errors and solutions |
| [Development](docs/Development.md) | Local setup, code structure, patterns |
| [CLI](docs/CLI.md) | `qemu-cli` usage |

## Security reports

See [`SECURITY.md`](SECURITY.md) for private vulnerability reporting. Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT License – Open Source
