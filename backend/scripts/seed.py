"""
Seed script: Creates initial super admin, default tenant, project, and storage pool.

Usage:
    python -m scripts.seed
    ADMIN_PASSWORD='…' python -m scripts.seed   # optional override
"""

import asyncio
import secrets
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select, text
from app.core.database import async_session_factory, engine, Base
from app.core.security import hash_password

# Import ALL models so Base.metadata.create_all creates all tables
import app.models  # noqa: F401 – triggers model registration
from app.models.user import User, TenantUser, UserRole
from app.models.tenant import Tenant, Project
from app.models.storage import StoragePool, StoragePoolType

_WEAK_PASSWORDS = frozenset({
    "admin1234",
    "admin",
    "password",
    "changeme",
    "qemu",
    "qemu1234",
})


def _resolve_admin_password() -> str:
    """Use ADMIN_PASSWORD if set; otherwise generate a strong one."""
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not admin_password:
        admin_password = secrets.token_urlsafe(12)
        print(f"[Seed] ADMIN_PASSWORD not set – generated: {admin_password}")
        print("[Seed] Save this password; it will not be shown again.")
        return admin_password
    if admin_password.lower() in _WEAK_PASSWORDS:
        print("ERROR: ADMIN_PASSWORD is too weak (known default rejected).")
        print("  Set a strong password or omit ADMIN_PASSWORD to auto-generate.")
        sys.exit(1)
    if len(admin_password) < 12:
        print("ERROR: ADMIN_PASSWORD must be at least 12 characters.")
        print("  Or omit ADMIN_PASSWORD to auto-generate a strong one.")
        sys.exit(1)
    return admin_password


async def seed():
    """Create initial data with auto-generated credentials."""
    admin_password = _resolve_admin_password()

    # create_all handles all enum types and tables.
    # NEVER create enums manually – Python 3.12 changed str(Enum.MEMBER)
    # to return the NAME, and asyncpg uses str() for enum values.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[Seed] Schema up-to-date.")

    async with async_session_factory() as db:
        # Check if admin already exists
        result = await db.execute(select(User).where(User.username == "admin"))
        existing_admin = result.scalar_one_or_none()
        if existing_admin:
            existing_admin.hashed_password = hash_password(admin_password)
            if existing_admin.email.endswith(".local"):
                existing_admin.email = "admin@qemu-interface.dev"
            db.add(existing_admin)
            await db.commit()
            print(f"Admin user updated. Login: admin / {admin_password}")
            return

        # Create super admin
        admin = User(
            email="admin@qemu-interface.dev",
            username="admin",
            hashed_password=hash_password(admin_password),
            full_name="System Administrator",
            is_active=True,
            is_superadmin=True,
        )
        db.add(admin)
        await db.flush()

        # Create default tenant
        tenant = Tenant(
            name="Default",
            slug="default",
            description="Default tenant",
            max_vms=50,
            max_vcpus=128,
            max_ram_mb=262144,
            max_storage_gb=2000,
            max_ips=50,
        )
        db.add(tenant)
        await db.flush()

        # Add admin to tenant
        db.add(TenantUser(
            tenant_id=tenant.id,
            user_id=admin.id,
            role=UserRole.SUPER_ADMIN,
        ))

        # Create default project
        project = Project(
            tenant_id=tenant.id,
            name="Default",
            description="Standard-Projekt",
        )
        db.add(project)
        await db.flush()

        # Create default storage pool
        db.add(StoragePool(
            name="default",
            pool_type=StoragePoolType.DIRECTORY,
            path="/var/lib/qemu-interface/storage",
            total_size_gb=500,
            is_default=True,
            description="Standard Storage Pool",
        ))

        await db.commit()
        print(f"\nSeed successful! Login: admin / {admin_password}")


if __name__ == "__main__":
    asyncio.run(seed())
