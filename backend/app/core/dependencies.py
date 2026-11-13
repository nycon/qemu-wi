"""
FastAPI dependencies for authentication, authorization, and common patterns.

Supports three authentication methods:
  1. JWT Bearer token – Authorization: Bearer <jwt>
  2. JWT httpOnly cookie – qi_access (browser sessions; not readable by JS)
  3. API Key – X-API-Key: qi_<key> (optionally scoped to a tenant)
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth_cookies import get_access_token_from_request
from app.core.database import get_db
from app.core.security import decode_token, verify_password
from app.models.user import TenantUser, User, UserRole

security_scheme = HTTPBearer(auto_error=False)  # auto_error=False to allow API key / cookie fallback


def get_api_key_tenant_scope(user: User) -> uuid.UUID | None:
    """Return the API-key tenant scope, if the request was authenticated via a scoped key."""
    return getattr(user, "_api_key_tenant_id", None)


def enforce_api_key_tenant_scope(user: User, tenant_id: uuid.UUID) -> None:
    """Reject cross-tenant access when the caller used a tenant-scoped API key."""
    scoped = get_api_key_tenant_scope(user)
    if scoped is not None and scoped != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key is scoped to a different tenant",
        )


async def get_current_user(
    request: Request,
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> User:
    """
    Extract and validate the current user from JWT token, cookie, or API key.

    Priority:
      1. X-API-Key header (for programmatic access)
      2. Authorization: Bearer <jwt>
      3. qi_access httpOnly cookie (browser)
    """

    # --- Method 1: API Key ---
    if x_api_key:
        from app.models.api_key import APIKey

        if not x_api_key.startswith("qi_"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key (must start with 'qi_')",
            )

        # Find matching key by prefix (first 12 chars)
        key_prefix = x_api_key[:12]
        result = await db.execute(
            select(APIKey).where(
                APIKey.key_prefix == key_prefix,
                APIKey.is_active.is_(True),
            )
        )
        api_keys = list(result.scalars().all())

        matched_key = None
        for ak in api_keys:
            if verify_password(x_api_key, ak.key_hash):
                matched_key = ak
                break

        if not matched_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
            )

        # Check expiry
        if matched_key.expires_at:
            expires = matched_key.expires_at
            if hasattr(expires, "tzinfo") and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="API key has expired",
                )

        # Update last_used_at
        matched_key.last_used_at = datetime.now(timezone.utc)
        db.add(matched_key)

        # Load user
        user_result = await db.execute(
            select(User)
            .options(selectinload(User.tenant_memberships).selectinload(TenantUser.tenant))
            .where(User.id == matched_key.user_id)
        )
        user = user_result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key user not found or disabled",
            )

        # Enforce tenant scope on the authenticated principal
        user._api_key_tenant_id = matched_key.tenant_id  # type: ignore[attr-defined]
        user._auth_via_api_key = True  # type: ignore[attr-defined]
        return user

    # --- Method 2/3: JWT Bearer or httpOnly cookie ---
    raw_token: str | None = None
    if credentials and credentials.credentials:
        raw_token = credentials.credentials
    else:
        raw_token = get_access_token_from_request(request)

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required (Bearer token, session cookie, or X-API-Key)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(raw_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token contains no user ID",
        )

    # Check if token is blacklisted (refresh token revocation)
    jti = payload.get("jti")
    if jti:
        from app.core.redis_client import is_token_blacklisted
        if await is_token_blacklisted(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
            )

    result = await db.execute(
        select(User)
        .options(selectinload(User.tenant_memberships).selectinload(TenantUser.tenant))
        .where(User.id == uuid.UUID(user_id))
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    user._api_key_tenant_id = None  # type: ignore[attr-defined]
    user._auth_via_api_key = False  # type: ignore[attr-defined]
    return user


async def require_superadmin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the current user to be a super admin."""
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-admin permission required",
        )
    # Scoped API keys must not gain global superadmin powers across tenants
    if get_api_key_tenant_scope(current_user) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant-scoped API key cannot perform global admin actions",
        )
    return current_user


class RequireTenantRole:
    """
    Dependency that checks if the user has a specific role (or higher) in the given tenant.

    Usage:
        @router.get("/tenants/{tenant_id}/vms")
        async def list_vms(
            tenant_id: uuid.UUID,
            user: User = Depends(RequireTenantRole(UserRole.USER)),
        ):
    """

    ROLE_HIERARCHY = {
        UserRole.SUPER_ADMIN: 100,
        UserRole.TENANT_ADMIN: 50,
        UserRole.USER: 10,
        UserRole.READONLY: 1,
    }

    def __init__(self, minimum_role: UserRole):
        self.minimum_role = minimum_role

    async def __call__(
        self,
        tenant_id: uuid.UUID,
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        enforce_api_key_tenant_scope(current_user, tenant_id)

        # Super admins bypass tenant role checks (unless API key is scoped — handled above)
        if current_user.is_superadmin:
            return current_user

        # Find user's role in the specified tenant
        membership = None
        for m in current_user.tenant_memberships:
            if m.tenant_id == tenant_id and m.is_active:
                membership = m
                break

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No access to this tenant",
            )

        user_level = self.ROLE_HIERARCHY.get(membership.role, 0)
        required_level = self.ROLE_HIERARCHY.get(self.minimum_role, 0)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Minimum role '{self.minimum_role.value}' required",
            )

        return current_user


# Common query parameters
class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(25, ge=1, le=100, description="Entries per page"),
    ):
        self.page = page
        self.page_size = page_size
        self.offset = (page - 1) * page_size
