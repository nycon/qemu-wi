"""
API Key management endpoints.

API keys allow programmatic/external access to the API without
browser-based JWT login. Keys are hashed (like passwords) and
the plaintext is shown exactly once at creation.

Usage:
    curl -H "X-API-Key: qi_abc12345..." https://panel.example.com/api/v1/tenants
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.security import hash_password, verify_password
from app.models.api_key import APIKey
from app.models.user import TenantUser, User
from app.schemas.api_key import APIKeyCreate, APIKeyCreatedResponse, APIKeyListResponse, APIKeyResponse
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/api-keys", tags=["API Keys"])

# Prefix for API keys (makes them identifiable)
API_KEY_PREFIX = "qi_"
API_KEY_LENGTH = 48  # 48 random hex chars + prefix


def generate_api_key() -> str:
    """Generate a cryptographically secure API key."""
    return API_KEY_PREFIX + secrets.token_hex(API_KEY_LENGTH)


@router.get("", response_model=APIKeyListResponse)
async def list_api_keys(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all API keys for the current user."""
    result = await db.execute(
        select(APIKey).where(
            APIKey.user_id == current_user.id,
        ).order_by(APIKey.created_at.desc())
    )
    keys = list(result.scalars().all())

    total = await db.execute(
        select(func.count(APIKey.id)).where(APIKey.user_id == current_user.id)
    )

    return APIKeyListResponse(items=keys, total=total.scalar() or 0)


@router.post("", response_model=APIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: APIKeyCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Create a new API key.

    IMPORTANT: The returned `api_key` value is shown only once!
    Store it securely – it cannot be retrieved again.
    """
    # Limit: max 10 keys per user
    count_result = await db.execute(
        select(func.count(APIKey.id)).where(
            APIKey.user_id == current_user.id,
            APIKey.is_active.is_(True),
        )
    )
    if (count_result.scalar() or 0) >= 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 10 active API keys per user",
        )

    # Tenant scope: non-superadmin keys MUST be scoped; superadmin may create global keys.
    tenant_id = body.tenant_id
    if tenant_id is None and not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant_id is required for API keys (non-superadmin)",
        )

    if tenant_id is not None:
        if current_user.is_superadmin:
            pass
        else:
            membership = next(
                (
                    m
                    for m in (current_user.tenant_memberships or [])
                    if m.tenant_id == tenant_id and m.is_active
                ),
                None,
            )
            if membership is None:
                # Memberships may not be loaded eagerly on all auth paths — check DB
                result = await db.execute(
                    select(TenantUser).where(
                        TenantUser.user_id == current_user.id,
                        TenantUser.tenant_id == tenant_id,
                        TenantUser.is_active.is_(True),
                    )
                )
                if result.scalar_one_or_none() is None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="No access to the selected tenant",
                    )

    # Generate the key
    plaintext_key = generate_api_key()
    key_hash = hash_password(plaintext_key)
    key_prefix = plaintext_key[:12]  # "qi_" + first 9 hex chars

    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    api_key = APIKey(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        key_hash=key_hash,
        key_prefix=key_prefix,
        tenant_id=tenant_id,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    await create_audit_log(
        db,
        action="api_key_created",
        resource_type="api_key",
        resource_id=api_key.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"API key created: {body.name} ({key_prefix}...)",
    )

    # Return with the plaintext key (only time it's visible!)
    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        description=api_key.description,
        key_prefix=api_key.key_prefix,
        tenant_id=api_key.tenant_id,
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        api_key=plaintext_key,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Revoke (deactivate) an API key."""
    result = await db.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == current_user.id,
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    api_key.is_active = False
    db.add(api_key)

    await create_audit_log(
        db,
        action="api_key_revoked",
        resource_type="api_key",
        resource_id=api_key.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"API key revoked: {api_key.name} ({api_key.key_prefix}...)",
    )


@router.delete("/{key_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Permanently delete a revoked API key."""
    result = await db.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == current_user.id,
        )
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    if api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an active key. Revoke it first.",
        )

    key_name = api_key.name
    key_prefix = api_key.key_prefix
    await db.delete(api_key)

    await create_audit_log(
        db,
        action="api_key_deleted",
        resource_type="api_key",
        resource_id=key_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        description=f"API key deleted: {key_name} ({key_prefix}...)",
    )
