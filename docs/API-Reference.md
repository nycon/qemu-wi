# API Reference

Base URL: `https://<your-domain>/api/v1`

All endpoints require authentication via JWT Bearer token unless noted otherwise.

Interactive API docs available at: `https://<your-domain>/api/docs` (Swagger UI)

## Authentication

### Login
```
POST /auth/login
Body: { "username": "admin", "password": "..." }
Response: { "access_token": "...", "refresh_token": "...", "token_type": "bearer" }
```

### Refresh Token
```
POST /auth/refresh
Body: { "refresh_token": "..." }
Response: { "access_token": "...", "refresh_token": "..." }
```

### Register
```
POST /auth/register
Body: { "email": "...", "username": "...", "password": "...", "full_name": "..." }
```

### Change Password
```
POST /auth/change-password
Body: { "current_password": "...", "new_password": "..." }
```

### Current user profile
```
GET   /auth/me                 → Profile + decrypted SSH public keys (owner only)
PATCH /auth/me                 → Update full_name / email
GET   /auth/me/ssh-keys        → List SSH keys
POST  /auth/me/ssh-keys        → Add key (stored Fernet-encrypted at rest)
DELETE /auth/me/ssh-keys/{id}  → Remove key
```

SSH public keys are encrypted in `users.ssh_public_keys_enc` (Fernet, key derived from `SECRET_KEY`). Max 20 keys per user.

### Two-Factor Authentication
UI: **User Profile** (`/profile`) — not Settings.

```
POST  /auth/2fa/setup      → Returns QR code / TOTP secret
POST  /auth/2fa/enable      → Body: { "code": "123456" }
POST  /auth/2fa/disable     → Body: { "code": "123456" }
GET   /auth/2fa/status       → { "enabled": true/false }
POST  /auth/verify-2fa      → Body: { "code": "123456", "temp_token": "..." }
```

## Virtual Machines

All VM endpoints: `/tenants/{tenant_id}/vms`

### CRUD
```
GET    /tenants/{tid}/vms                    → List VMs (paginated)
POST   /tenants/{tid}/vms                    → Create VM
GET    /tenants/{tid}/vms/{vm_id}            → Get VM details
PATCH  /tenants/{tid}/vms/{vm_id}            → Update VM
DELETE /tenants/{tid}/vms/{vm_id}            → Delete VM
```

### VM Create Payload
```json
{
  "name": "my-vm",
  "project_id": "uuid",
  "template_id": "uuid",
  "vcpus": 2,
  "ram_mb": 2048,
  "disk_size_gb": 20,
  "cloud_init_identity": {
    "hostname": "my-vm",
    "default_user": "cloud",
    "password": "SecurePass123!",
    "ssh_authorized_keys": ["ssh-ed25519 AAAA..."],
    "disable_password_login": false
  },
  "cloud_init_userdata": {
    "packages": ["nginx", "htop"],
    "timezone": "Europe/Berlin",
    "keyboard_layout": "de"
  }
}
```

### Lifecycle Actions
```
POST /tenants/{tid}/vms/{id}/action
Body: { "action": "start" | "stop" | "reboot" | "force_stop" }
```

### Console
```
GET /tenants/{tid}/vms/{id}/console-token → { "token": "jwt...", "ws_url": "wss://..." }
```

### Snapshots
```
GET    /tenants/{tid}/vms/{id}/snapshots                    → List
POST   /tenants/{tid}/vms/{id}/snapshots                    → Create
POST   /tenants/{tid}/vms/{id}/snapshots/{name}/revert      → Revert
DELETE /tenants/{tid}/vms/{id}/snapshots/{name}              → Delete
```

### Disks
```
GET    /tenants/{tid}/vms/{id}/disks           → List disks
POST   /tenants/{tid}/vms/{id}/disks           → Create disk
POST   /tenants/{tid}/vms/{id}/disks/{did}/attach   → Attach
POST   /tenants/{tid}/vms/{id}/disks/{did}/detach   → Detach
POST   /tenants/{tid}/vms/{id}/disks/{did}/resize   → Resize
DELETE /tenants/{tid}/vms/{id}/disks/{did}           → Delete
```

### Backups
```
GET    /tenants/{tid}/vms/{id}/backups           → List backups
POST   /tenants/{tid}/vms/{id}/backups           → Create backup (live if running, cold if stopped)
POST   /tenants/{tid}/vms/{id}/backups/{bid}/restore → Restore (VM must be stopped)
DELETE /tenants/{tid}/vms/{id}/backups/{bid}      → Delete
```

Create works on running VMs (live external snapshot + blockcommit). Restore requires a stopped VM.
Details: [Backups](Backups.md).

### Backup Schedules
```
GET    /tenants/{tid}/vms/{id}/backup-schedules           → List
POST   /tenants/{tid}/vms/{id}/backup-schedules           → Create
PATCH  /tenants/{tid}/vms/{id}/backup-schedules/{sid}     → Update
DELETE /tenants/{tid}/vms/{id}/backup-schedules/{sid}     → Delete
```

Schedules create live backups for running guests; retention deletes older backups automatically.

### Clone
```
POST /tenants/{tid}/vms/{id}/clone
Body: { "name": "clone-vm", "project_id": "uuid", "cloud_init_identity": {...} }
```

### Export / Import
```
POST /tenants/{tid}/vms/{id}/export/start       → Start export
GET  /tenants/{tid}/vms/{id}/export/download     → Download export file
GET  /tenants/{tid}/vms/{id}/export/status        → Export status

POST /tenants/{tid}/vms/import                    → Start import
POST /tenants/{tid}/vms/{id}/import/finalize      → Finalize import
```

### Stats
```
GET /tenants/{tid}/vms/{id}/stats → { "cpu_percent": 12.5, "memory_used_mb": 512, ... }
```

### Secondary NICs
```
POST /tenants/{tid}/vms/{id}/add-nic      → Body: { "pool_id": "uuid" }
POST /tenants/{tid}/vms/{id}/remove-nic   → Body: { "pool_id": "uuid" }
```

## Tenants

```
GET    /tenants              → List tenants
POST   /tenants              → Create tenant (optional body field: admin_user_id)
GET    /tenants/{id}         → Get tenant
PATCH  /tenants/{id}         → Update tenant (optional: admin_user_id, public_ip_allocation_ids, notification_email, …)
DELETE /tenants/{id}         → Delete tenant
GET    /tenants/{id}/users   → List tenant members
POST   /tenants/{id}/users   → Add user to tenant
GET    /tenants/{id}/public-ips → List public IPs assigned to this tenant
PATCH  /tenants/{id}/notification-email → Set tenant notification inbox (tenant admin+)
```

`admin_user_id` (optional UUID) on create/update assigns an existing active user as `tenant_admin` (creates or upgrades membership). Omit on update to leave memberships unchanged. Creating/editing superadmin stays `super_admin` member. Admin UI: **Admin → Tenants** → Tenant Admin dropdown on create and edit.

## Projects

```
GET    /tenants/{tid}/projects          → List projects
POST   /tenants/{tid}/projects          → Create project
GET    /tenants/{tid}/projects/{id}     → Get project
PATCH  /tenants/{tid}/projects/{id}     → Update project
DELETE /tenants/{tid}/projects/{id}     → Delete project
```

## IP Pools (IPAM) & Tenant Networks

```
GET    /ip-pools                         → List pools (?tenant_id= scopes visibility; external CIDR/gateway hidden for tenants)
POST   /ip-pools                         → Create **external/public** pool only (superadmin)
GET    /ip-pools/public/available        → Free public IPs for tenant assignment (superadmin)
GET    /ip-pools/{id}                    → Get pool details
PATCH  /ip-pools/{id}                    → Update pool
DELETE /ip-pools/{id}                    → Delete pool
GET    /ip-pools/{id}/allocations        → List IP allocations
POST   /ip-pools/{id}/allocate           → Allocate IP
POST   /ip-pools/{id}/release            → Release IP

GET    /tenants/{tid}/networks           → List tenant NAT/Internal networks
POST   /tenants/{tid}/networks           → Create NAT/Internal (optional CIDR/DHCP)
PATCH  /tenants/{tid}/networks/{pool_id} → Update name/DNS/DHCP
DELETE /tenants/{tid}/networks/{pool_id} → Delete tenant network
```

VM create accepts optional `ip_allocation_id` to pin a specific reserved public IP (together with `ip_pool_id`).

## Port Forwarding

```
GET    /port-forwards         → List rules
POST   /port-forwards         → Create rule
DELETE /port-forwards/{id}    → Delete rule
```

## Storage

### Storage Pools
```
GET    /storage-pools         → List pools (?tenant_id=… includes assigned + shared)
POST   /storage-pools         → Create pool (optional tenant_id)
PATCH  /storage-pools/{id}    → Update pool (incl. tenant assignment)
DELETE /storage-pools/{id}    → Delete pool
```

`tenant_id` on a pool scopes it to that tenant; `null` means shared across tenants.

### Cloud Images
```
GET    /cloud-images          → List images
POST   /cloud-images          → Create/import image (by URL)
POST   /cloud-images/upload   → Upload image file
GET    /cloud-images/{id}     → Get image details
POST   /cloud-images/{id}/retry-download → Retry failed download
DELETE /cloud-images/{id}     → Delete image
```

Recommended public image URLs and panel steps: [Cloud Images](Cloud-Images.md).

## VM Templates

```
GET    /vm-templates          → List templates
POST   /vm-templates          → Create template
GET    /vm-templates/{id}     → Get template
PATCH  /vm-templates/{id}     → Update template
DELETE /vm-templates/{id}     → Delete template
```

## Reverse Proxy

```
GET    /reverse-proxy         → List rules
POST   /reverse-proxy         → Create rule
PATCH  /reverse-proxy/{id}    → Update rule
DELETE /reverse-proxy/{id}    → Delete rule
POST   /reverse-proxy/{id}/ssl → Request SSL for domain
```

## Load Balancer

```
GET    /load-balancers              → List load balancers
POST   /load-balancers              → Create load balancer
GET    /load-balancers/{id}         → Get load balancer details
PATCH  /load-balancers/{id}         → Update load balancer
DELETE /load-balancers/{id}         → Delete load balancer
POST   /load-balancers/{id}/targets → Add VM target
DELETE /load-balancers/{id}/targets/{tid} → Remove target
```

### Load Balancer Create Payload
```json
{
  "name": "web-lb",
  "protocol": "http",
  "algorithm": "round_robin",
  "listen_port": 80,
  "health_check_path": "/",
  "health_check_interval": 10,
  "targets": [
    { "vm_id": "uuid", "target_port": 8080, "weight": 1 },
    { "vm_id": "uuid", "target_port": 8080, "weight": 2 }
  ]
}
```

Supported protocols: `http`, `https`, `tcp`
Supported algorithms: `round_robin`, `least_conn`, `ip_hash`

## Cluster (Multi-Node)

```
GET    /cluster/nodes              → List registered nodes
POST   /cluster/nodes              → Add node (connects immediately)
PATCH  /cluster/nodes/{id}         → Update node (URL, API key, etc.)
DELETE /cluster/nodes/{id}         → Remove node from cluster
POST   /cluster/nodes/{id}/sync    → Manually sync a single node
POST   /cluster/sync-all           → Sync all active nodes
GET    /cluster/overview           → Aggregated overview (all nodes + VMs)
```

### Add Node Payload
```json
{
  "name": "node-2",
  "description": "Production server 2",
  "url": "https://192.168.1.100",
  "api_key": "qi_abc123..."
}
```

### Overview Response
The `/cluster/overview` endpoint returns aggregated data across all nodes:
- Total/online nodes
- Total/running VMs across all nodes
- Aggregate vCPU, RAM, Storage usage
- Full VM list with `node_name` for each VM

Nodes are automatically synced every 2 minutes via Celery Beat.

## Tasks

```
GET    /tasks                 → List tasks (paginated)
GET    /tasks/{id}            → Get task details
POST   /tasks/{id}/cancel     → Cancel task
```

## Audit Logs

```
GET    /audit-logs            → List logs (paginated, filterable)
GET    /audit-logs/{id}       → Get log entry
GET    /audit-logs/export     → Export as CSV or JSON
```

## Users (Superadmin)

```
GET    /users                 → List all users
GET    /users/{id}            → Get user
POST   /users                 → Create user
PATCH  /users/{id}            → Update user
DELETE /users/{id}            → Delete user
```

## API Keys

```
GET    /api-keys              → List API keys
POST   /api-keys              → Create API key (optional tenant_id; required for non-superadmin)
DELETE /api-keys/{id}         → Revoke API key
DELETE /api-keys/{id}/permanent → Permanently delete revoked key
```

UI: **User Profile → API Keys**. Authenticate with `X-API-Key: qi_…`. Keys inherit the owner's role. Superadmin global keys (`tenant_id` null) can act across tenants; tenant-scoped keys are limited to that tenant. Tenant admins/users must create scoped keys for a tenant they belong to.

## Notifications

```
GET    /notifications/preferences     → Get notification prefs
PATCH  /notifications/preferences     → Update prefs
GET    /notifications/smtp            → Get SMTP settings
PATCH  /notifications/smtp            → Update SMTP settings
POST   /notifications/smtp/test       → Send test email
```

## System (Superadmin)

```
GET    /system/generate-password       → Generate secure password
GET    /system/debug                   → Full system diagnostics
POST   /system/retry-stuck-tasks       → Reset stuck tasks
POST   /system/cleanup                 → Cleanup orphaned resources
POST   /system/cleanup-libvirt         → Cleanup libvirt domains
GET    /system/networks                → List host networks/bridges
```

### VM backup inventory & storage
```
GET    /system/vm-backups                → All VM backups (?backend=internal|local|ftp|s3)
GET    /system/vm-backups/summary        → Counts by backend / status
DELETE /system/vm-backups/{id}           → Superadmin delete (local + FTP/S3 + DB row)
GET    /system/backup-storage            → Local/FTP/S3 config (secrets masked)
PUT    /system/backup-storage            → Update backup storage backend
POST   /system/backup-storage/test       → Test FTP/S3 connectivity
```

### Database backups
```
GET    /system/db-backups              → List DB dumps
POST   /system/db-backups              → Create dump
GET    /system/db-backups/{id}/download → Download dump file
POST   /system/db-backups/import       → Import dump
DELETE /system/db-backups/{id}         → Delete dump
```

See [Backups](Backups.md).

### SSL Management
```
GET    /system/ssl/status              → SSL certificate status
POST   /system/ssl/test               → Test domain readiness
POST   /system/ssl/setup              → Request Let's Encrypt cert
POST   /system/ssl/regenerate-config   → Regenerate Nginx config
POST   /system/ssl/disable            → Disable SSL
```

### Security / Egress Filtering
```
GET    /system/security/egress-rules           → Get egress rules
PUT    /system/security/egress-rules           → Update egress rules
POST   /system/security/egress-rules/add-port  → Add allowed port
DELETE /system/security/egress-rules/remove-port → Remove port
GET    /system/security/egress-log             → Blocked traffic log
```

## Error Responses

All errors follow this format:
```json
{
  "detail": "Error description"
}
```

Common HTTP status codes:
- `400` – Bad request / validation error
- `401` – Not authenticated
- `403` – Forbidden (insufficient permissions)
- `404` – Resource not found
- `422` – Validation error (Pydantic)
- `502` – Host agent unreachable
