# Architecture

## System Overview

```
                    ┌─────────────────────────────────────────┐
                    │              User Browser               │
                    └──────────────┬──────────────────────────┘
                                   │ HTTPS (443) / HTTP (80)
                    ┌──────────────▼──────────────────────────┐
                    │         Nginx Reverse Proxy              │
                    │   (SSL termination, routing, WebSocket)  │
                    └──┬───────────┬──────────────────────────┘
                       │           │
            /api/*     │           │  /*
                       ▼           ▼
              ┌────────────┐  ┌─────────────┐
              │  Backend   │  │  Frontend   │
              │  (FastAPI) │  │  (React)    │
              │  Port 8000 │  │  Port 3000  │
              └──┬──┬──────┘  └─────────────┘
                 │  │
        ┌────────┘  └────────┐
        ▼                    ▼
  ┌───────────┐      ┌────────────┐
  │ PostgreSQL│      │   Redis    │
  │  Port 5432│      │  Port 6379 │
  └───────────┘      └──────┬─────┘
                            │
                     ┌──────▼──────┐
                     │ Celery      │
                     │ Worker/Beat │
                     └──────┬──────┘
                            │ HTTPS (8443)
                     ┌──────▼──────────────────┐
                     │   Host Agent             │
                     │   (FastAPI + libvirt)    │
                     │   Runs on bare metal     │
                     │   NOT in Docker          │
                     └──────┬──────────────────┘
                            │
                     ┌──────▼──────┐
                     │  libvirt    │
                     │  QEMU/KVM  │
                     │  VMs        │
                     └─────────────┘
```

## Components

### 1. Nginx Reverse Proxy (Docker)

- **Port**: 80 (HTTP), 443 (HTTPS)
- **Role**: SSL termination, routes `/api/*` to backend, `/*` to frontend, WebSocket proxy for VNC
- **Config**: Generated dynamically by installer based on SSL status
- **Container**: `qemu-interface-proxy`

### 2. Frontend (Docker)

- **Port**: 3000 (internal)
- **Tech**: React 19 + TypeScript + TailwindCSS + Vite
- **Role**: Single-page application, communicates with backend via REST API
- **Container**: `qemu-interface-frontend`
- **Served by**: Its own Nginx inside the container

### 3. Backend API (Docker)

- **Port**: 8000 (internal)
- **Tech**: FastAPI + SQLAlchemy 2.0 + Pydantic v2
- **Role**: REST API, authentication, business logic, database access
- **Container**: `qemu-interface-api`
- **Workers**: 4 uvicorn workers (auto-configured)

### 4. Celery Worker (Docker)

- **Tech**: Celery + Redis
- **Role**: Async task execution (VM provisioning, backups, image downloads)
- **Container**: `qemu-interface-worker`
- **Beat**: Integrated scheduler for periodic tasks (backup schedules)

### 5. PostgreSQL (Docker)

- **Port**: 5432 (internal only)
- **Role**: Primary data store for users, VMs, tenants, tasks, audit logs
- **Container**: `qemu-interface-db`
- **Volume**: `postgres_data` (persistent)

### 6. Redis (Docker)

- **Port**: 6379 (internal only)
- **Role**: Celery message broker, task result backend
- **Container**: `qemu-interface-redis`
- **Volume**: `redis_data` (persistent)

### 7. Host Agent (systemd service)

- **Port**: 8443 (HTTPS with self-signed cert)
- **Tech**: FastAPI + libvirt-python + uvicorn
- **Role**: Executes privileged operations on the host (VM lifecycle, storage, network)
- **Service**: `qemu-interface-agent`
- **Location**: `/opt/qemu-interface/host-agent/`
- **Runs as**: root (needs access to libvirt, iptables, storage)

## Data Flow

### VM Creation
```
Frontend → Backend API → Creates DB record → Celery Task → Host Agent
                                                              │
                                           ┌──────────────────┘
                                           ▼
                                    1. Clone disk image
                                    2. Create cloud-init seed
                                       (incl. qemu-guest-agent on Linux)
                                    3. Define libvirt domain
                                    4. Start VM
                                    5. Return VNC port
```

IP selection: `ip_pool_id` plus optional `ip_allocation_id` for a reserved public address.

### VNC Console
```
Frontend (noVNC) → Nginx (WebSocket upgrade) → Backend (token verify)
     │
     └── WebSocket → Host Agent /websockify → TCP to VM VNC port
```

### Authentication Flow
```
Login → Backend verifies credentials → JWT access token (15min)
                                     → JWT refresh token (7 days)
                                     → Optional: 2FA TOTP verification
```

## Directory Structure

```
/opt/qemu-interface/           # Installation directory
├── backend/                   # FastAPI backend
│   ├── app/
│   │   ├── api/v1/endpoints/  # API route handlers
│   │   ├── core/              # Config, security, database
│   │   ├── models/            # SQLAlchemy models
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── services/          # Business logic
│   │   └── workers/           # Celery tasks
│   ├── scripts/seed.py        # Initial data seeder
│   ├── alembic/               # Database migrations
│   └── Dockerfile
├── frontend/                  # React frontend
│   ├── src/
│   │   ├── pages/             # Page components
│   │   ├── components/        # Shared components
│   │   ├── services/api.ts    # API client
│   │   ├── store/             # Zustand state stores
│   │   └── types/             # TypeScript types
│   └── Dockerfile
├── host-agent/                # Host agent
│   ├── agent/
│   │   ├── main.py            # FastAPI app + all endpoints
│   │   └── config.py          # Agent configuration
│   └── services/              # libvirt, storage, cloud-init
├── deploy/nginx/              # Nginx configuration
├── docker-compose.yml
├── docker-compose.override.yml  # Generated by installer
├── install.sh                 # Main installer
└── docs/                      # This documentation
```

## File Storage Layout

```
/var/lib/qemu-interface/       # Data directory
├── storage/                   # VM disk images
│   ├── <vm-id>/disk.qcow2    # VM primary disk
│   └── <image>.img            # Cloud base images
├── cloud-init/                # Cloud-init seed images
│   └── <vm-id>/seed.img
├── backups/                   # Local VM backup files (internal / keep-local)
│   └── <vm-id>/
├── exports/                   # VM export files
├── vnc-mapping.json           # VNC port mappings
└── port-forwards.json         # Port forwarding rules

/etc/qemu-interface/           # Configuration
├── secrets                    # Generated secrets (JWT, DB pass, etc.)
├── agent-cert.pem             # Host agent TLS certificate
├── agent-key.pem              # Host agent TLS private key
└── egress-rules.json          # Egress filtering config
```

VM backups may also live on FTP or S3 when configured in **Admin → Backups** (see [Backups](Backups.md)). Host Agent port **8443** is locked down to localhost/Docker ranges.

## Cluster Architecture (Multi-Node)

When running multiple QEMU Interface installations, one node acts as the **Cluster Master**
that aggregates data from all other nodes via their public APIs.

```
                    ┌──────────────────────────────┐
                    │       Cluster Master          │
                    │    (any QEMU Interface node)  │
                    │                               │
                    │  Admin > Cluster Dashboard    │
                    │  - Aggregated VM list          │
                    │  - Per-node resource bars      │
                    │  - Health monitoring            │
                    └──────┬───────┬───────┬────────┘
                           │       │       │
                    API-Key│  API  │  API  │
                           │       │       │
                    ┌──────▼──┐ ┌──▼────┐ ┌▼────────┐
                    │ Node 1  │ │Node 2 │ │ Node 3  │
                    │ (self)  │ │(remote│ │(remote) │
                    │         │ │       │ │         │
                    │ VMs     │ │ VMs   │ │ VMs     │
                    └─────────┘ └───────┘ └─────────┘
```

### Key Design Decisions

- **No shared database** – Each node has its own PostgreSQL. The master reads via API.
- **No code changes on remote nodes** – Only requires an API key for authentication.
- **Independent operation** – If the master goes offline, all nodes continue running.
- **Cached data** – Node data is cached locally, so the dashboard loads instantly.
- **Auto-sync** – Celery Beat syncs all nodes every 2 minutes.

### Adding a Node

1. On the **remote node**: Create an API key under **User Profile → API Keys**
2. On the **master node**: Go to Admin > Cluster > Add Node
3. Enter the remote node's URL + API key
4. The master connects immediately and fetches all data

## Network Architecture

**Ownership:** Superadmin manages **public/external** IP pools and assigns concrete IPs to tenants. Tenants create **NAT/Internal** networks under **Networks** when needed (no auto default NAT). On VM create, tenants select one assigned public IP (host address only — pool CIDR is not exposed).

### NAT Network
```
Internet ← Host IP ← virbr0 (192.168.122.0/24) ← VMs
                        │
                   NAT/Masquerade
                        │
              Egress filtering (VM_EGRESS chain)
```

### Internal Network (Isolated)
```
VM ← internal bridge (10.x.x.0/24) → VM
     No internet access, VM-to-VM only
```

### Bridged Network (Public IPs)
```
Internet ← br0 (bridge) ← VMs with public IPs
           Works on most providers and home networks with a standard subnet
           Note: Hetzner uses /32 point-to-point routing where br0 is not possible (NAT only)
```

### Load Balancer
```
Client → Host:listen_port → Nginx upstream (LB) → VM1:port (weight=1)
                                                 → VM2:port (weight=2)
                                                 → VM3:port (weight=1)

Algorithms: round_robin | least_conn | ip_hash
Protocols:  HTTP (Layer 7) | TCP (Layer 4)
```
