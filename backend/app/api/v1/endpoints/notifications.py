"""
Notification preferences and SMTP configuration endpoints.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_superadmin
from app.models.notification import NotificationPreference
from app.models.user import User
from app.services.email_service import get_last_smtp_error, send_test_email

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ─── Schemas ─────────────────────────────────────────────────────────────────


class NotificationPrefsResponse(BaseModel):
    notify_vm_error: bool = True
    notify_vm_created: bool = True
    notify_backup_completed: bool = True
    notify_backup_failed: bool = True
    notify_quota_warning: bool = True
    notify_login_new_ip: bool = False
    notify_2fa_changed: bool = True

    model_config = {"from_attributes": True}


class NotificationPrefsUpdate(BaseModel):
    notify_vm_error: bool | None = None
    notify_vm_created: bool | None = None
    notify_backup_completed: bool | None = None
    notify_backup_failed: bool | None = None
    notify_quota_warning: bool | None = None
    notify_login_new_ip: bool | None = None
    notify_2fa_changed: bool | None = None


class SMTPStatusResponse(BaseModel):
    enabled: bool
    host: str
    port: int
    from_email: str
    from_name: str
    use_tls: bool
    allow_invalid_cert: bool
    configured: bool


class SMTPUpdateRequest(BaseModel):
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_tls: bool | None = None
    smtp_allow_invalid_cert: bool | None = None
    smtp_enabled: bool | None = None


class TestEmailRequest(BaseModel):
    email: EmailStr


# ─── User Notification Preferences ──────────────────────────────────────────


async def _get_or_create_prefs(db: AsyncSession, user: User) -> NotificationPreference:
    """Get or create notification preferences for a user."""
    result = await db.execute(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()
    if not prefs:
        prefs = NotificationPreference(user_id=user.id)
        db.add(prefs)
        await db.flush()
        await db.refresh(prefs)
    return prefs


@router.get("/preferences", response_model=NotificationPrefsResponse)
async def get_notification_preferences(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get current user's notification preferences."""
    prefs = await _get_or_create_prefs(db, current_user)
    return prefs


@router.patch("/preferences", response_model=NotificationPrefsResponse)
async def update_notification_preferences(
    body: NotificationPrefsUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update current user's notification preferences."""
    prefs = await _get_or_create_prefs(db, current_user)
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(prefs, field, value)
    db.add(prefs)
    await db.flush()
    await db.refresh(prefs)
    return prefs


# ─── SMTP Configuration (superadmin only) ───────────────────────────────────


@router.get("/smtp", response_model=SMTPStatusResponse)
async def get_smtp_status(
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Get current SMTP configuration (superadmin only). Passwords are not returned."""
    return SMTPStatusResponse(
        enabled=settings.SMTP_ENABLED,
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        from_email=settings.SMTP_FROM_EMAIL,
        from_name=settings.SMTP_FROM_NAME,
        use_tls=settings.SMTP_USE_TLS,
        allow_invalid_cert=settings.SMTP_ALLOW_INVALID_CERT,
        configured=bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL),
    )


@router.patch("/smtp", response_model=SMTPStatusResponse)
async def update_smtp_settings(
    body: SMTPUpdateRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Update SMTP settings at runtime (superadmin only).
    Changes are applied immediately but not persisted to .env file.
    """
    update_data = body.model_dump(exclude_unset=True)
    field_map = {
        "smtp_host": "SMTP_HOST",
        "smtp_port": "SMTP_PORT",
        "smtp_user": "SMTP_USER",
        "smtp_password": "SMTP_PASSWORD",
        "smtp_from_email": "SMTP_FROM_EMAIL",
        "smtp_from_name": "SMTP_FROM_NAME",
        "smtp_use_tls": "SMTP_USE_TLS",
        "smtp_allow_invalid_cert": "SMTP_ALLOW_INVALID_CERT",
        "smtp_enabled": "SMTP_ENABLED",
    }
    for key, value in update_data.items():
        attr = field_map.get(key)
        if attr:
            setattr(settings, attr, value)

    return SMTPStatusResponse(
        enabled=settings.SMTP_ENABLED,
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        from_email=settings.SMTP_FROM_EMAIL,
        from_name=settings.SMTP_FROM_NAME,
        use_tls=settings.SMTP_USE_TLS,
        allow_invalid_cert=settings.SMTP_ALLOW_INVALID_CERT,
        configured=bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL),
    )


@router.post("/smtp/test")
async def test_smtp(
    body: TestEmailRequest,
    current_user: Annotated[User, Depends(require_superadmin)],
):
    """Send a test email to verify SMTP configuration (superadmin only)."""
    if not settings.SMTP_ENABLED:
        raise HTTPException(status_code=400, detail="SMTP is not enabled")
    if not settings.SMTP_HOST:
        raise HTTPException(status_code=400, detail="SMTP host is not configured")
    if not settings.SMTP_FROM_EMAIL:
        raise HTTPException(status_code=400, detail="SMTP from email is not configured")

    success = await send_test_email(body.email)
    if success:
        return {"status": "sent", "message": f"Test email sent to {body.email}"}
    else:
        error_detail = get_last_smtp_error()
        if error_detail:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send test email: {error_detail}",
            )
        raise HTTPException(status_code=500, detail="Failed to send test email. Check SMTP settings and server logs.")
