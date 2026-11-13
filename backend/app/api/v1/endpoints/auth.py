"""
Authentication endpoints: login, register, refresh, change password, 2FA.
"""

import io
import base64
import uuid
from typing import Annotated

import pyotp
import qrcode
import qrcode.constants
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_cookies import (
    clear_auth_cookies,
    get_refresh_token_from_request,
    set_auth_cookies,
)
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.security import (
    create_2fa_temp_token,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    TwoFactorDisableRequest,
    TwoFactorSetupResponse,
    TwoFactorStatusResponse,
    TwoFactorVerifyLoginRequest,
    TwoFactorVerifyRequest,
    UpdateProfileRequest,
    UserSshKeyCreate,
)
from app.schemas.user import UserProfileResponse, UserResponse, UserSshKeyItem
from app.services.audit_service import create_audit_log
from app.services import user_ssh_keys as ssh_keys_svc

router = APIRouter(prefix="/auth", tags=["Authentication"])

AUTH_LOG_FILE = "/app/logs/backend-auth.log"


def _log_auth_failure(ip: str, username: str):
    """Write failed login to file for fail2ban monitoring."""
    from datetime import datetime, timezone
    try:
        import os
        os.makedirs(os.path.dirname(AUTH_LOG_FILE), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with open(AUTH_LOG_FILE, "a") as f:
            f.write(f"{ts} LOGIN_FAILED from {ip} user={username}\n")
    except Exception:
        pass


def _issue_tokens(user: User) -> TokenResponse:
    """Issue access + refresh tokens for a fully authenticated user."""
    extra_claims = {"email": user.email, "is_superadmin": user.is_superadmin}
    access_token = create_access_token(str(user.id), extra_claims=extra_claims)
    refresh_token = create_refresh_token(str(user.id))
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        requires_2fa=False,
    )


def _attach_auth_cookies(
    response: Response,
    request: Request,
    tokens: TokenResponse,
) -> TokenResponse:
    """Persist browser session in httpOnly cookies (body tokens remain for API clients)."""
    if tokens.access_token and tokens.refresh_token and not tokens.requires_2fa:
        set_auth_cookies(response, request, tokens.access_token, tokens.refresh_token)
    return tokens


async def _consume_token_jti(payload: dict, *, ttl_seconds: int) -> None:
    """Mark a JWT as single-use via Redis blacklist."""
    jti = payload.get("jti")
    if not jti:
        return
    from app.core.redis_client import blacklist_token

    await blacklist_token(jti, max(ttl_seconds, 1))


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Authenticate user and return JWT tokens (or request 2FA code)."""
    result = await db.execute(
        select(User).where(User.username == body.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        client_ip = request.client.host if request.client else "unknown"
        await create_audit_log(
            db,
            action="login_failed",
            resource_type="auth",
            actor_ip=client_ip,
            description=f"Failed login for '{body.username}'",
            status="failure",
        )
        _log_auth_failure(client_ip, body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    # --- 2FA check ---
    if user.totp_enabled and user.totp_secret:
        # If TOTP code is provided in the login request, verify it immediately
        if body.totp_code:
            totp = pyotp.TOTP(user.totp_secret)
            if not totp.verify(body.totp_code, valid_window=1):
                await create_audit_log(
                    db,
                    action="login_2fa_failed",
                    resource_type="auth",
                    actor_id=user.id,
                    actor_email=user.email,
                    actor_ip=request.client.host if request.client else None,
                    description="Invalid 2FA code during login",
                    status="failure",
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid 2FA code",
                )
            # 2FA code valid → issue tokens
        else:
            # No TOTP code provided → return temp token for 2FA step
            temp_token = create_2fa_temp_token(str(user.id))
            return TokenResponse(
                access_token="",
                refresh_token="",
                requires_2fa=True,
                temp_token=temp_token,
            )

    client_ip = request.client.host if request.client else "unknown"

    await create_audit_log(
        db,
        action="login_success",
        resource_type="auth",
        actor_id=user.id,
        actor_email=user.email,
        actor_ip=client_ip,
        description="Successful login",
    )

    # Email notification: login from new IP (fire and forget)
    try:
        from app.core.config import settings
        if settings.SMTP_ENABLED:
            from app.models.notification import NotificationPreference
            prefs_result = await db.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user.id)
            )
            prefs = prefs_result.scalar_one_or_none()
            should_notify = prefs.notify_login_new_ip if prefs else False
            if should_notify:
                from app.services.email_service import notify_login_new_ip
                user_agent = request.headers.get("user-agent", "")
                await notify_login_new_ip(user.email, user.username, client_ip, user_agent)
    except Exception:
        pass  # Never block login on notification failure

    return _attach_auth_cookies(response, request, _issue_tokens(user))


@router.post("/verify-2fa", response_model=TokenResponse)
async def verify_2fa_login(
    request: Request,
    response: Response,
    body: TwoFactorVerifyLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Complete 2FA login: validate temp token + TOTP code → issue tokens."""
    try:
        payload = decode_token(body.temp_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired temp token",
        )

    if payload.get("type") != "2fa_pending":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    # Single-use: reject already-consumed temp tokens
    jti = payload.get("jti")
    if jti:
        from app.core.redis_client import is_token_blacklisted
        if await is_token_blacklisted(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Temp token already used",
            )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or 2FA not active",
        )

    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        await create_audit_log(
            db,
            action="login_2fa_failed",
            resource_type="auth",
            actor_id=user.id,
            actor_email=user.email,
            actor_ip=request.client.host if request.client else None,
            description="Invalid 2FA code during login",
            status="failure",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid 2FA code",
        )

    # Consume temp token so it cannot be replayed
    await _consume_token_jti(payload, ttl_seconds=5 * 60)

    await create_audit_log(
        db,
        action="login_success",
        resource_type="auth",
        actor_id=user.id,
        actor_email=user.email,
        actor_ip=request.client.host if request.client else None,
        description="Successful login (2FA)",
    )

    return _attach_auth_cookies(response, request, _issue_tokens(user))


# ── 2FA Setup / Management ──────────────────────────────────────

@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def setup_2fa(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Generate a new TOTP secret and QR code for 2FA setup."""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled. Please disable first.",
        )

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    otpauth_uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="QEMU Interface",
    )

    # Generate QR code as base64 PNG
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(otpauth_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    # Store secret temporarily (not yet enabled!)
    current_user.totp_secret = secret
    db.add(current_user)

    return TwoFactorSetupResponse(
        secret=secret,
        otpauth_uri=otpauth_uri,
        qr_code_base64=f"data:image/png;base64,{qr_base64}",
    )


@router.post("/2fa/enable", response_model=TwoFactorStatusResponse)
async def enable_2fa(
    body: TwoFactorVerifyRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Verify TOTP code and enable 2FA for the current user."""
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled",
        )

    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please call /2fa/setup first",
        )

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code. Please try again.",
        )

    current_user.totp_enabled = True
    db.add(current_user)

    await create_audit_log(
        db,
        action="2fa_enabled",
        resource_type="user",
        resource_id=current_user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description="Two-factor authentication enabled",
    )

    # Email notification: 2FA enabled
    try:
        from app.core.config import settings
        if settings.SMTP_ENABLED:
            from app.services.email_service import notify_2fa_changed
            await notify_2fa_changed(current_user.email, current_user.username, True)
    except Exception:
        pass

    return TwoFactorStatusResponse(enabled=True)


@router.post("/2fa/disable", response_model=TwoFactorStatusResponse)
async def disable_2fa(
    body: TwoFactorDisableRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Disable 2FA (requires current password + current TOTP code)."""
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled",
        )

    # Verify password before disabling
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is incorrect",
        )

    if not current_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA secret missing",
        )

    totp = pyotp.TOTP(current_user.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid 2FA code",
        )

    current_user.totp_enabled = False
    current_user.totp_secret = None
    db.add(current_user)

    await create_audit_log(
        db,
        action="2fa_disabled",
        resource_type="user",
        resource_id=current_user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description="Two-factor authentication disabled",
    )

    # Email notification: 2FA disabled
    try:
        from app.core.config import settings
        if settings.SMTP_ENABLED:
            from app.services.email_service import notify_2fa_changed
            await notify_2fa_changed(current_user.email, current_user.username, False)
    except Exception:
        pass

    return TwoFactorStatusResponse(enabled=False)


@router.get("/2fa/status", response_model=TwoFactorStatusResponse)
async def get_2fa_status(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Check whether 2FA is enabled for the current user."""
    return TwoFactorStatusResponse(enabled=current_user.totp_enabled)


# ── Existing endpoints (unchanged) ──────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register a new user account (disabled unless ALLOW_PUBLIC_REGISTRATION=true)."""
    from app.core.config import settings

    if not settings.ALLOW_PUBLIC_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled. Ask an administrator to create your account.",
        )

    existing = await db.execute(
        select(User).where((User.email == body.email) | (User.username == body.username))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email or username already taken",
        )

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    await create_audit_log(
        db,
        action="user_registered",
        resource_type="user",
        resource_id=user.id,
        actor_id=user.id,
        actor_email=user.email,
        description=f"New user registered: {user.username}",
    )

    return user


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Refresh an access token using a refresh token (body or httpOnly cookie)."""
    raw_refresh = body.refresh_token or get_refresh_token_from_request(request)
    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )

    try:
        payload = decode_token(raw_refresh)
    except ValueError:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No valid refresh token",
        )

    # Check if this refresh token has been revoked
    jti = payload.get("jti")
    if jti:
        from app.core.redis_client import is_token_blacklisted
        if await is_token_blacklisted(jti):
            clear_auth_cookies(response)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
            )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    # Blacklist the old refresh token (single-use rotation)
    if jti:
        from app.core.config import settings
        ttl = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
        await _consume_token_jti(payload, ttl_seconds=ttl)

    extra_claims = {"email": user.email, "is_superadmin": user.is_superadmin}
    access_token = create_access_token(str(user.id), extra_claims=extra_claims)
    new_refresh_token = create_refresh_token(str(user.id))

    tokens = TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )
    return _attach_auth_cookies(response, request, tokens)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    body: RefreshRequest = RefreshRequest(),
):
    """Logout: revoke the refresh token and clear httpOnly cookies."""
    raw_refresh = body.refresh_token or get_refresh_token_from_request(request)
    clear_auth_cookies(response)
    if not raw_refresh:
        return

    try:
        payload = decode_token(raw_refresh)
    except ValueError:
        return

    if payload.get("jti"):
        from app.core.config import settings
        ttl = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400
        await _consume_token_jti(payload, ttl_seconds=ttl)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Change the current user's password."""
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.hashed_password = hash_password(body.new_password)
    db.add(current_user)

    await create_audit_log(
        db,
        action="password_changed",
        resource_type="user",
        resource_id=current_user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description="Password changed",
    )


def _profile_response(user: User) -> UserProfileResponse:
    base = UserResponse.model_validate(user)
    keys = [UserSshKeyItem(**k) for k in ssh_keys_svc.list_keys(user)]
    return UserProfileResponse(**base.model_dump(), ssh_keys=keys)


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get the current authenticated user's profile (incl. SSH keys)."""
    return _profile_response(current_user)


@router.patch("/me", response_model=UserProfileResponse)
async def update_current_user_profile(
    body: UpdateProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update the current user's profile (name, email)."""
    changes = []

    if body.full_name is not None and body.full_name != current_user.full_name:
        current_user.full_name = body.full_name
        changes.append(f"Name → {body.full_name}")

    if body.email is not None and body.email != current_user.email:
        existing = await db.execute(
            select(User).where(User.email == body.email, User.id != current_user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email address is already in use",
            )
        current_user.email = body.email
        changes.append(f"E-Mail → {body.email}")

    if changes:
        db.add(current_user)
        await create_audit_log(
            db,
            action="profile_updated",
            resource_type="user",
            resource_id=current_user.id,
            actor_id=current_user.id,
            actor_email=current_user.email,
            description=f"Profile updated: {', '.join(changes)}",
        )

    return _profile_response(current_user)


@router.get("/me/ssh-keys", response_model=list[UserSshKeyItem])
async def list_my_ssh_keys(
    current_user: Annotated[User, Depends(get_current_user)],
):
    """List SSH public keys for the current user (decrypted for owner)."""
    return [UserSshKeyItem(**k) for k in ssh_keys_svc.list_keys(current_user)]


@router.post("/me/ssh-keys", response_model=UserSshKeyItem, status_code=status.HTTP_201_CREATED)
async def add_my_ssh_key(
    body: UserSshKeyCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Add an SSH public key (stored encrypted at rest)."""
    entry = ssh_keys_svc.add_key(current_user, public_key=body.public_key, name=body.name)
    db.add(current_user)
    await create_audit_log(
        db,
        action="ssh_key_added",
        resource_type="user",
        resource_id=current_user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"SSH key added: {entry['name']} ({entry['fingerprint']})",
    )
    return UserSshKeyItem(**entry)


@router.delete("/me/ssh-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_ssh_key(
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete one of the current user's SSH public keys."""
    ssh_keys_svc.delete_key(current_user, str(key_id))
    db.add(current_user)
    await create_audit_log(
        db,
        action="ssh_key_deleted",
        resource_type="user",
        resource_id=current_user.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"SSH key deleted: {key_id}",
    )
    return None
