"""
Notification dispatcher – bridges between events and the email service.

Called from Celery tasks (sync context) and async API endpoints.
Tenant-scoped events prefer tenants.notification_email; otherwise VM creator.
Checks user notification preferences when falling back to a user inbox.
"""

import asyncio
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings


def _get_sync_session() -> Session:
    """Create a synchronous DB session for Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    if "postgresql://" in sync_url and "+psycopg2" not in sync_url:
        sync_url = sync_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = create_engine(sync_url)
    return SyncSession(engine)


def _check_pref(session: Session, user_id, pref_field: str) -> bool:
    """Check if a user has a specific notification enabled (sync)."""
    from app.models.notification import NotificationPreference
    result = session.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    prefs = result.scalar_one_or_none()
    if not prefs:
        # Default: most notifications are on
        return pref_field != "notify_login_new_ip"
    return getattr(prefs, pref_field, True)


def _get_user_email(session: Session, user_id) -> Optional[str]:
    """Get user email by ID (sync)."""
    from app.models.user import User
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
    user = session.get(User, uid)
    return user.email if user and user.is_active else None


def _get_vm(session: Session, vm_id: str):
    from app.models.vm import VirtualMachine
    return session.get(VirtualMachine, uuid.UUID(vm_id))


def _get_tenant_notification_email(session: Session, tenant_id) -> Optional[str]:
    from app.models.tenant import Tenant
    tid = uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id
    tenant = session.get(Tenant, tid)
    if not tenant or not tenant.is_active:
        return None
    email = (tenant.notification_email or "").strip()
    return email or None


def _recipients_for_vm_event(
    session: Session,
    vm_id: str,
    pref_field: str,
) -> list[str]:
    """Tenant notification email first; else creator if prefs allow. Deduped."""
    vm = _get_vm(session, vm_id)
    if not vm:
        return []

    recipients: list[str] = []
    tenant_email = _get_tenant_notification_email(session, vm.tenant_id)
    if tenant_email:
        recipients.append(tenant_email.lower())

    if vm.created_by:
        creator_email = _get_user_email(session, vm.created_by)
        if creator_email and _check_pref(session, vm.created_by, pref_field):
            lowered = creator_email.lower()
            if lowered not in recipients:
                recipients.append(lowered)

    return recipients


def _run_async(coro):
    """Run async function from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(coro)
        else:
            loop.run_until_complete(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()


# ─── Sync dispatchers (for Celery tasks) ────────────────────────────────────


def dispatch_vm_error(vm_id: str, vm_name: str, error_msg: str, tenant_name: str = ""):
    """Send VM error notification (called from Celery)."""
    if not settings.SMTP_ENABLED:
        return

    session = _get_sync_session()
    try:
        from app.services.email_service import notify_vm_error

        for email in _recipients_for_vm_event(session, vm_id, "notify_vm_error"):
            _run_async(notify_vm_error(email, vm_name, vm_id, error_msg, tenant_name))
    except Exception as e:
        print(f"[Notification] VM error dispatch failed: {e}")
    finally:
        session.close()


def dispatch_vm_created(vm_id: str, vm_name: str, ip_address: str = "", tenant_name: str = ""):
    """Send VM created notification (called from Celery)."""
    if not settings.SMTP_ENABLED:
        return

    session = _get_sync_session()
    try:
        from app.services.email_service import notify_vm_created

        for email in _recipients_for_vm_event(session, vm_id, "notify_vm_created"):
            _run_async(notify_vm_created(email, vm_name, vm_id, ip_address, tenant_name))
    except Exception as e:
        print(f"[Notification] VM created dispatch failed: {e}")
    finally:
        session.close()


def dispatch_backup_completed(vm_id: str, vm_name: str, backup_name: str, file_size: str = ""):
    """Send backup completed notification (called from Celery)."""
    if not settings.SMTP_ENABLED:
        return

    session = _get_sync_session()
    try:
        from app.services.email_service import notify_backup_completed

        for email in _recipients_for_vm_event(session, vm_id, "notify_backup_completed"):
            _run_async(notify_backup_completed(email, vm_name, backup_name, file_size))
    except Exception as e:
        print(f"[Notification] Backup completed dispatch failed: {e}")
    finally:
        session.close()


def dispatch_backup_failed(vm_id: str, vm_name: str, backup_name: str, error_msg: str):
    """Send backup failed notification (called from Celery)."""
    if not settings.SMTP_ENABLED:
        return

    session = _get_sync_session()
    try:
        from app.services.email_service import notify_backup_failed

        for email in _recipients_for_vm_event(session, vm_id, "notify_backup_failed"):
            _run_async(notify_backup_failed(email, vm_name, backup_name, error_msg))
    except Exception as e:
        print(f"[Notification] Backup failed dispatch failed: {e}")
    finally:
        session.close()
