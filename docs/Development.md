# Development Guide

## Prerequisites

- Python 3.12+
- Node.js 18+ and npm
- Docker and Docker Compose
- PostgreSQL 16 (via Docker or local)
- Redis 7 (via Docker or local)
- libvirt-dev (for host agent development)

## Project Structure

```
qemu-interface/
├── backend/                    # FastAPI Backend
│   ├── app/
│   │   ├── api/v1/
│   │   │   ├── endpoints/      # Route handlers (one file per resource)
│   │   │   └── router.py       # Main API router (registers all endpoints)
│   │   ├── core/
│   │   │   ├── config.py       # Settings (Pydantic BaseSettings)
│   │   │   ├── database.py     # SQLAlchemy async engine + session
│   │   │   ├── dependencies.py # Auth dependencies, pagination, RBAC
│   │   │   └── security.py     # JWT, password hashing, token creation
│   │   ├── models/             # SQLAlchemy ORM models
│   │   │   ├── user.py         # User, TenantUser
│   │   │   ├── vm.py           # VirtualMachine, VMDisk
│   │   │   ├── task.py         # Task (async jobs)
│   │   │   ├── network.py      # IPPool (+ tenant_id), IPAllocation, PortForward
│   │   │   ├── …               # network_service.py: auto-CIDR, public-IP assign/sync, tenant NAT/Internal
│   │   │   └── storage.py      # StoragePool, CloudImage
│   │   ├── schemas/            # Pydantic request/response schemas
│   │   ├── services/           # Business logic layer
│   │   │   ├── vm_service.py   # VM creation, cloning logic
│   │   │   ├── audit_service.py # Audit log helper
│   │   │   └── email_service.py # SMTP notifications
│   │   └── workers/            # Celery tasks
│   │       ├── celery_app.py   # Celery configuration
│   │       ├── vm_tasks.py     # VM provisioning, lifecycle tasks
│   │       ├── storage_tasks.py # Image download tasks
│   │       └── backup_scheduler.py # Scheduled backup execution
│   ├── scripts/
│   │   └── seed.py             # Initial data seeder
│   ├── alembic/                # Database migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                   # React Frontend
│   ├── src/
│   │   ├── pages/              # Page components
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── VMListPage.tsx
│   │   │   ├── VMCreatePage.tsx
│   │   │   ├── VMDetailPage.tsx
│   │   │   ├── ConsolePage.tsx
│   │   │   └── admin/          # Admin-only pages
│   │   ├── components/
│   │   │   └── layout/         # Sidebar, TopBar, AppLayout
│   │   ├── services/
│   │   │   └── api.ts          # Axios API client (all endpoints)
│   │   ├── store/              # Zustand state management
│   │   │   ├── authStore.ts    # Auth state, login/logout
│   │   │   └── tenantStore.ts  # Current tenant selection
│   │   └── types/index.ts      # TypeScript interfaces
│   ├── package.json
│   └── Dockerfile
├── host-agent/                 # Host Agent
│   ├── agent/
│   │   ├── main.py             # FastAPI app (all endpoints in one file)
│   │   └── config.py           # Agent settings
│   ├── services/
│   │   ├── libvirt_service.py  # libvirt wrapper
│   │   ├── storage_service.py  # Disk management
│   │   └── cloudinit_service.py # Cloud-init ISO generation
│   └── requirements.txt
├── deploy/nginx/               # Nginx config
├── docker-compose.yml          # Base compose file
├── install.sh                  # Production installer
└── docs/                       # Documentation (this wiki)
```

## Local Development Setup

### 1. Start infrastructure (DB + Redis)

```bash
docker compose up -d db redis
```

### 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment
export DATABASE_URL="postgresql+asyncpg://qemu:devpassword@localhost:5432/qemu_interface"
export REDIS_URL="redis://localhost:6379/0"
export SECRET_KEY="dev-secret-key-change-in-production"
export JWT_SECRET_KEY="dev-jwt-secret"
export HOST_AGENT_URL="https://localhost:8443"
export HOST_AGENT_TOKEN="dev-agent-token"
export PYTHONPATH=/path/to/backend

# Run migrations
alembic upgrade head

# Seed initial data
python -m scripts.seed
# Optional: ADMIN_PASSWORD='…' python -m scripts.seed
# Without ADMIN_PASSWORD a strong password is auto-generated and printed.

# Start development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3. Celery Worker

```bash
cd backend
source venv/bin/activate
celery -A app.workers.celery_app worker -l info -B
```

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### 5. Host Agent (requires libvirt)

```bash
cd host-agent
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export AGENT_TOKEN="dev-agent-token"
uvicorn agent.main:app --reload --host 0.0.0.0 --port 8443
```

## Key Patterns

### Adding a new API endpoint

1. **Model** (if new table): Create in `backend/app/models/`
2. **Schema**: Create request/response in `backend/app/schemas/`
3. **Endpoint**: Create handler in `backend/app/api/v1/endpoints/`
4. **Router**: Register in `backend/app/api/v1/router.py`
5. **Frontend API**: Add to `frontend/src/services/api.ts`
6. **Frontend page**: Create in `frontend/src/pages/`
7. **Route**: Add to `frontend/src/App.tsx`
8. **Sidebar**: Add nav entry in `frontend/src/components/layout/Sidebar.tsx`

### Adding a Host Agent endpoint

1. Add endpoint to `host-agent/agent/main.py`
2. Add proxy endpoint in `backend/app/api/v1/endpoints/` (usually `system.py` or `vms.py`)
3. Add frontend API call
4. Add UI if needed

### Database Migrations

```bash
cd backend

# Auto-generate migration from model changes
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### Authentication in endpoints

```python
from app.core.dependencies import get_current_user, require_superadmin, RequireTenantRole
from app.models.user import User, UserRole

# Any authenticated user
async def endpoint(current_user: Annotated[User, Depends(get_current_user)]):
    ...

# Superadmin only
async def endpoint(current_user: Annotated[User, Depends(require_superadmin)]):
    ...

# Tenant role required
async def endpoint(current_user: Annotated[User, Depends(RequireTenantRole(UserRole.ADMIN))]):
    ...
```

### Frontend API pattern

```typescript
// In services/api.ts
export const myApi = {
  list: () => api.get<{ items: MyType[]; total: number }>('/my-resource').then(r => r.data),
  create: (data: CreatePayload) => api.post<MyType>('/my-resource', data).then(r => r.data),
  delete: (id: string) => api.delete(`/my-resource/${id}`).then(r => r.data),
};
```

### Frontend page pattern

```typescript
export function MyPage() {
  const [items, setItems] = useState<MyType[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const data = await myApi.list();
      setItems(data.items);
    } catch {
      toast.error('Failed to load');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { loadData(); }, []);

  // ... render
}
```

## Documentation (MkDocs)

Docs live in `docs/` and are configured by `mkdocs.yml`.

```bash
pip install mkdocs
mkdocs serve          # http://127.0.0.1:8000
mkdocs build --strict # output in site/
```

GitHub Actions are disabled for this repository. Build docs locally with `mkdocs build` / `mkdocs serve` if needed.

## Tech Decisions

| Decision | Rationale |
|---|---|
| FastAPI | Async, auto-docs, Pydantic validation, WebSocket support |
| SQLAlchemy 2.0 async | Type-safe ORM with native async support |
| React + TypeScript | Type safety, component reuse, large ecosystem |
| TailwindCSS | Utility-first, no CSS file management, dark mode built-in |
| Zustand | Simpler than Redux, minimal boilerplate |
| Celery + Redis | Reliable async task execution with scheduling |
| Docker Compose | Easy deployment, reproducible environments |
| Host Agent (separate) | Needs root + libvirt access, can't run in Docker |
| JWT (not sessions) | Stateless, works with API keys, mobile-friendly |
