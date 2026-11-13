"""
FastAPI application entry point.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine, Base

# Import all models so metadata is populated
import app.models  # noqa: F401


async def _auto_migrate():
    """Automatically create/update database schema on startup.

    All Enum() columns use values_callable=lambda x: [e.value for e in x]
    so PostgreSQL enum labels always use the .value (lowercase).
    This avoids Python 3.12 str(StrEnum) issues with asyncpg.
    """
    try:
        # Step 1: Drop any wrongly-cased enum types from previous broken migrations
        # so create_all can recreate them correctly with .value labels
        async with engine.begin() as conn:
            for enum_name in ("pooltype", "ipversion"):
                result = await conn.execute(text(
                    "SELECT enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON e.enumtypid = t.oid "
                    f"WHERE t.typname = '{enum_name}' ORDER BY e.enumsortorder"
                ))
                vals = [r[0] for r in result.fetchall()]
                # If enum exists with WRONG case (uppercase from old migrations),
                # drop it so create_all recreates it with correct .value labels
                if vals and not vals[0].islower():
                    print(f"[Auto-Migration] Dropping wrongly-cased {enum_name} enum...")
                    # Check if any table column uses this enum
                    for tbl, col in [("ip_pools", "pool_type"), ("ip_pools", "ip_version"),
                                     ("ip_allocations", "status")]:
                        has = (await conn.execute(text(
                            "SELECT 1 FROM information_schema.columns "
                            f"WHERE table_name = '{tbl}' AND column_name = '{col}'"
                        ))).scalar()
                        if has:
                            await conn.execute(text(
                                f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE VARCHAR(20)"))
                    await conn.execute(text(f"DROP TYPE IF EXISTS {enum_name}"))
                    print(f"[Auto-Migration] {enum_name} dropped (will be recreated).")

        # Step 2: create_all – creates enum types (with .value labels) and new tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Step 3: Add missing columns to existing tables (for upgrades)
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ip_pools'"
            ))
            cols = {r[0] for r in result.fetchall()}

            if cols:  # table exists
                if "pool_type" not in cols:
                    print("[Auto-Migration] ip_pools: adding pool_type column...")
                    await conn.execute(text(
                        "ALTER TABLE ip_pools ADD COLUMN pool_type VARCHAR(20) "
                        "NOT NULL DEFAULT 'nat'"
                    ))
                    await conn.execute(text(
                        "ALTER TABLE ip_pools ALTER COLUMN pool_type "
                        "TYPE pooltype USING pool_type::pooltype"
                    ))
                if "libvirt_network_name" not in cols:
                    print("[Auto-Migration] ip_pools: adding libvirt_network_name...")
                    await conn.execute(text(
                        "ALTER TABLE ip_pools ADD COLUMN libvirt_network_name VARCHAR(255)"
                    ))
                if "dhcp_start" not in cols:
                    print("[Auto-Migration] ip_pools: adding dhcp_start column...")
                    await conn.execute(text(
                        "ALTER TABLE ip_pools ADD COLUMN IF NOT EXISTS dhcp_start VARCHAR(50)"
                    ))
                if "dhcp_end" not in cols:
                    print("[Auto-Migration] ip_pools: adding dhcp_end column...")
                    await conn.execute(text(
                        "ALTER TABLE ip_pools ADD COLUMN IF NOT EXISTS dhcp_end VARCHAR(50)"
                    ))
                if "tenant_id" not in cols:
                    print("[Auto-Migration] ip_pools: adding tenant_id column...")
                    await conn.execute(text(
                        "ALTER TABLE ip_pools "
                        "ADD COLUMN IF NOT EXISTS tenant_id UUID "
                        "REFERENCES tenants(id) ON DELETE SET NULL"
                    ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_ip_pools_tenant_id "
                    "ON ip_pools (tenant_id)"
                ))

        # Step 3b: Add secondary_interfaces to virtual_machines
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'virtual_machines'"
            ))
            vm_cols = {r[0] for r in result.fetchall()}
            if vm_cols and "secondary_interfaces" not in vm_cols:
                print("[Auto-Migration] virtual_machines: adding secondary_interfaces...")
                await conn.execute(text(
                    "ALTER TABLE virtual_machines ADD COLUMN secondary_interfaces JSONB DEFAULT '[]'"
                ))

        # Step 3c: Add 2FA columns to users table
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users'"
            ))
            user_cols = {r[0] for r in result.fetchall()}
            if user_cols:
                if "totp_secret" not in user_cols:
                    print("[Auto-Migration] users: adding totp_secret column...")
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)"
                    ))
                if "totp_enabled" not in user_cols:
                    print("[Auto-Migration] users: adding totp_enabled column...")
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT false"
                    ))
                if "ssh_public_keys_enc" not in user_cols:
                    print("[Auto-Migration] users: adding ssh_public_keys_enc column...")
                    await conn.execute(text(
                        "ALTER TABLE users ADD COLUMN ssh_public_keys_enc TEXT"
                    ))

        # Step 3c2: Tenant notification email
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tenants'"
            ))
            tenant_cols = {r[0] for r in result.fetchall()}
            if tenant_cols and "notification_email" not in tenant_cols:
                print("[Auto-Migration] tenants: adding notification_email column...")
                await conn.execute(text(
                    "ALTER TABLE tenants ADD COLUMN notification_email VARCHAR(255)"
                ))

        # Step 3d: Add 'internal' to pooltype enum if missing
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'pooltype'"
            ))
            labels = {r[0] for r in result.fetchall()}
            if labels and "internal" not in labels:
                print("[Auto-Migration] Adding 'internal' to pooltype enum...")
                await conn.execute(text(
                    "ALTER TYPE pooltype ADD VALUE IF NOT EXISTS 'internal'"
                ))

        # Step 3e: Add backup/restore to tasktype enum if missing
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'tasktype'"
            ))
            task_labels = {r[0] for r in result.fetchall()}
            if task_labels:
                for val in ("backup_create", "backup_restore"):
                    if val not in task_labels:
                        print(f"[Auto-Migration] Adding '{val}' to tasktype enum...")
                        await conn.execute(text(
                            f"ALTER TYPE tasktype ADD VALUE IF NOT EXISTS '{val}'"
                        ))

        # Step 3f: Create sslmode enum + reverse_proxy_rules table if missing
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'reverse_proxy_rules'"
            ))
            if not result.scalar():
                print("[Auto-Migration] Creating sslmode enum and reverse_proxy_rules table...")
                # Create enum type first
                await conn.execute(text(
                    "DO $$ BEGIN "
                    "  CREATE TYPE sslmode AS ENUM ('none', 'auto', 'custom'); "
                    "EXCEPTION WHEN duplicate_object THEN NULL; "
                    "END $$"
                ))
                # Create table
                await conn.execute(text("""
                    CREATE TABLE reverse_proxy_rules (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                        vm_id UUID REFERENCES virtual_machines(id) ON DELETE SET NULL,
                        domain VARCHAR(255) NOT NULL UNIQUE,
                        target_ip VARCHAR(50) NOT NULL,
                        target_port INTEGER NOT NULL DEFAULT 80,
                        ssl_mode sslmode NOT NULL DEFAULT 'none',
                        ssl_enabled BOOLEAN NOT NULL DEFAULT false,
                        websocket_support BOOLEAN NOT NULL DEFAULT false,
                        custom_nginx_config TEXT,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_by UUID NOT NULL REFERENCES users(id),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_reverse_proxy_rules_domain ON reverse_proxy_rules(domain)"
                ))
                print("[Auto-Migration] reverse_proxy_rules table created.")

        # Step 4: Ensure api_keys table exists (created by create_all above)
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'api_keys'"
            ))
            if not result.scalar():
                print("[Auto-Migration] api_keys table will be created by create_all...")
                await conn.run_sync(Base.metadata.create_all)

        # Step 5: Add vm_export/vm_import to tasktype enum if missing
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'tasktype'"
            ))
            task_labels = {r[0] for r in result.fetchall()}
            for val in ("vm_export", "vm_import"):
                if task_labels and val not in task_labels:
                    print(f"[Auto-Migration] Adding '{val}' to tasktype enum...")
                    await conn.execute(text(
                        f"ALTER TYPE tasktype ADD VALUE IF NOT EXISTS '{val}'"
                    ))

        # Step 6: Add disk management columns to vm_disks if missing
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'vm_disks'"
            ))
            disk_cols = {r[0] for r in result.fetchall()}
            if disk_cols:
                if "name" not in disk_cols:
                    print("[Auto-Migration] vm_disks: adding name column...")
                    await conn.execute(text(
                        "ALTER TABLE vm_disks ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT 'data'"
                    ))
                if "target_dev" not in disk_cols:
                    print("[Auto-Migration] vm_disks: adding target_dev column...")
                    await conn.execute(text(
                        "ALTER TABLE vm_disks ADD COLUMN target_dev VARCHAR(10) NOT NULL DEFAULT 'vdb'"
                    ))
                if "status" not in disk_cols:
                    print("[Auto-Migration] vm_disks: adding status column...")
                    await conn.execute(text(
                        "DO $$ BEGIN "
                        "  CREATE TYPE diskstatus AS ENUM "
                        "    ('creating','available','attached','detaching','error','deleting'); "
                        "EXCEPTION WHEN duplicate_object THEN NULL; "
                        "END $$"
                    ))
                    await conn.execute(text(
                        "ALTER TABLE vm_disks ADD COLUMN status diskstatus NOT NULL DEFAULT 'attached'"
                    ))

        # Step 7: Storage pool tenant assignment (NULL = shared/global)
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'storage_pools'"
            ))
            pool_cols = {r[0] for r in result.fetchall()}
            if pool_cols and "tenant_id" not in pool_cols:
                print("[Auto-Migration] storage_pools: adding tenant_id column...")
                # IF NOT EXISTS: API + worker may race on startup
                await conn.execute(text(
                    "ALTER TABLE storage_pools "
                    "ADD COLUMN IF NOT EXISTS tenant_id UUID "
                    "REFERENCES tenants(id) ON DELETE SET NULL"
                ))
            if pool_cols:
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_storage_pools_tenant_id "
                    "ON storage_pools (tenant_id)"
                ))

        # Step 8: VM backup remote storage columns
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'vm_backups'"
            ))
            backup_cols = {r[0] for r in result.fetchall()}
            if backup_cols:
                if "storage_backend" not in backup_cols:
                    print("[Auto-Migration] vm_backups: adding storage_backend...")
                    await conn.execute(text(
                        "ALTER TABLE vm_backups "
                        "ADD COLUMN IF NOT EXISTS storage_backend VARCHAR(20) "
                        "NOT NULL DEFAULT 'internal'"
                    ))
                if "remote_path" not in backup_cols:
                    print("[Auto-Migration] vm_backups: adding remote_path...")
                    await conn.execute(text(
                        "ALTER TABLE vm_backups "
                        "ADD COLUMN IF NOT EXISTS remote_path VARCHAR(1024)"
                    ))

        # Step 9: LB target IP mode (nat vs public)
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'lb_targets'"
            ))
            lb_cols = {r[0] for r in result.fetchall()}
            if lb_cols and "ip_mode" not in lb_cols:
                print("[Auto-Migration] lb_targets: adding ip_mode...")
                await conn.execute(text(
                    "ALTER TABLE lb_targets "
                    "ADD COLUMN IF NOT EXISTS ip_mode VARCHAR(20) "
                    "NOT NULL DEFAULT 'nat'"
                ))
            if lb_cols and "preferred_ip" not in lb_cols:
                print("[Auto-Migration] lb_targets: adding preferred_ip...")
                await conn.execute(text(
                    "ALTER TABLE lb_targets "
                    "ADD COLUMN IF NOT EXISTS preferred_ip VARCHAR(45)"
                ))

        # Step 10: LB-level public VIP (one IP in front of many NAT backends)
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'load_balancers'"
            ))
            lb_table_cols = {r[0] for r in result.fetchall()}
            if lb_table_cols and "public_ip" not in lb_table_cols:
                print("[Auto-Migration] load_balancers: adding public_ip...")
                await conn.execute(text(
                    "ALTER TABLE load_balancers "
                    "ADD COLUMN IF NOT EXISTS public_ip VARCHAR(45)"
                ))

        print("[Auto-Migration] Schema up-to-date.")

        # Scrub any guest passwords that were previously stored in JSONB userdata
        await _scrub_stored_guest_passwords()
    except Exception as e:
        print(f"[Auto-Migration] WARNING: {e}")
        import traceback
        traceback.print_exc()
        print("[Auto-Migration] App will continue – some features may not work.")


async def _scrub_stored_guest_passwords() -> None:
    """One-shot cleanup: remove plaintext guest passwords from VM userdata in DB."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from app.core.database import async_session_factory
    from app.models.vm import VirtualMachine
    from app.services.cloud_init_sanitize import (
        cloud_init_userdata_needs_sanitize,
        sanitize_cloud_init_userdata,
    )

    async with async_session_factory() as session:
        result = await session.execute(
            select(VirtualMachine).where(VirtualMachine.cloud_init_userdata.isnot(None))
        )
        vms = list(result.scalars().all())
        scrubbed = 0
        for vm in vms:
            if not cloud_init_userdata_needs_sanitize(vm.cloud_init_userdata):
                continue
            vm.cloud_init_userdata = sanitize_cloud_init_userdata(vm.cloud_init_userdata)
            flag_modified(vm, "cloud_init_userdata")
            scrubbed += 1
        if scrubbed:
            await session.commit()
            print(f"[Auto-Migration] Scrubbed guest passwords from {scrubbed} VM userdata record(s).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Startup
    print(f"{settings.APP_NAME} v{settings.APP_VERSION} starting...")
    await _auto_migrate()
    yield
    # Shutdown
    print(f"{settings.APP_NAME} shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="QEMU/KVM Cloud-Init Virtualisierungs-Panel – API",
    docs_url=None,
    redoc_url=None,
    # Served by an explicit route below so we can require auth when docs are gated.
    openapi_url=None,
    lifespan=lifespan,
)

# --- Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.ALLOWED_HOSTS != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.ALLOWED_HOSTS,
    )

# --- Routes ---

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


# --- Self-hosted Swagger UI (no CDN dependency) ---
# Exposed only when DEBUG or EXPOSE_API_DOCS is true (not public by default).

import pathlib
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

_static_dir = pathlib.Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/api/static", StaticFiles(directory=str(_static_dir)), name="static")


def _api_docs_enabled() -> bool:
    return bool(settings.DEBUG or settings.EXPOSE_API_DOCS)


from fastapi import Depends, Header, HTTPException  # noqa: E402
from app.core.dependencies import get_current_user  # noqa: E402
from app.core.security import decode_token  # noqa: E402
from app.models.user import User  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402


@app.get("/api/openapi.json", include_in_schema=False)
async def openapi_json(authorization: str | None = Header(default=None)):
    """
    OpenAPI schema.
    Public only when DEBUG/EXPOSE_API_DOCS is enabled; otherwise requires Bearer JWT
    (superadmin) so the in-panel API docs viewer keeps working.
    """
    if _api_docs_enabled():
        return app.openapi()

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = decode_token(authorization[7:])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        import uuid as _uuid
        user_id = _uuid.UUID(str(payload.get("sub")))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    async with async_session_factory() as db:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
    if not user or not user.is_active or not user.is_superadmin:
        raise HTTPException(status_code=403, detail="API docs require superadmin")
    return app.openapi()


@app.get("/api/docs", include_in_schema=False)
async def custom_swagger_ui():
    if not _api_docs_enabled():
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail="API docs disabled. Set EXPOSE_API_DOCS=true or DEBUG=true to enable.",
        )
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title=f"{settings.APP_NAME} - API Docs",
        swagger_js_url="/api/static/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/api/static/swagger-ui/swagger-ui.css",
    )


@app.get("/api/redoc", include_in_schema=False)
async def custom_redoc():
    if not _api_docs_enabled():
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail="API docs disabled. Set EXPOSE_API_DOCS=true or DEBUG=true to enable.",
        )
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title=f"{settings.APP_NAME} - API Docs",
        redoc_js_url="/api/static/swagger-ui/redoc.standalone.js",
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "service": settings.APP_NAME,
    }
