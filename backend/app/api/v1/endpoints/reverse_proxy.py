"""
Reverse Proxy management endpoints.
"""

import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.reverse_proxy import ReverseProxyRule, SSLMode
from app.models.user import User
from app.schemas.reverse_proxy import (
    ProxyRuleCreate,
    ProxyRuleListResponse,
    ProxyRuleResponse,
    ProxyRuleUpdate,
)
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/reverse-proxy", tags=["Reverse Proxy"])


async def _sync_proxy_to_host_agent(db: AsyncSession):
    """Fetch all active rules and push the full config to the host agent."""
    result = await db.execute(
        select(ReverseProxyRule)
        .where(ReverseProxyRule.is_active.is_(True))
        .order_by(ReverseProxyRule.domain)
    )
    rules = list(result.scalars().all())

    payload = [
        {
            "domain": r.domain,
            "target_ip": r.target_ip,
            "target_port": r.target_port,
            "ssl_enabled": r.ssl_enabled,
            "ssl_mode": r.ssl_mode.value if isinstance(r.ssl_mode, SSLMode) else r.ssl_mode,
            "websocket_support": r.websocket_support,
        }
        for r in rules
    ]

    try:
        async with httpx.AsyncClient(
            base_url=settings.HOST_AGENT_URL,
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            verify=AGENT_SSL_VERIFY,
            timeout=30.0,
        ) as client:
            resp = await client.post("/api/v1/proxy/apply", json={"rules": payload})
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Host agent proxy sync failed: {resp.text}",
                )
            try:
                body = resp.json()
            except Exception:
                body = {}
            if isinstance(body, dict) and body.get("status") not in (None, "success"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Host agent proxy sync failed: {body.get('message') or body}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Host agent unreachable: {e}")


@router.get("", response_model=ProxyRuleListResponse)
async def list_proxy_rules(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all reverse proxy rules (superadmin sees all, users see own tenant)."""
    if current_user.is_superadmin:
        result = await db.execute(
            select(ReverseProxyRule).order_by(ReverseProxyRule.domain)
        )
    else:
        from app.models.user import TenantUser
        memberships = list((await db.execute(
            select(TenantUser).where(TenantUser.user_id == current_user.id)
        )).scalars().all())
        if not memberships:
            return ProxyRuleListResponse(items=[], total=0)
        tenant_ids = [m.tenant_id for m in memberships]
        result = await db.execute(
            select(ReverseProxyRule)
            .where(ReverseProxyRule.tenant_id.in_(tenant_ids))
            .order_by(ReverseProxyRule.domain)
        )

    rules = list(result.scalars().all())
    return ProxyRuleListResponse(items=rules, total=len(rules))


@router.post("", response_model=ProxyRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_proxy_rule(
    body: ProxyRuleCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new reverse proxy rule."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage proxy rules")

    # Check domain uniqueness
    existing = (await db.execute(
        select(ReverseProxyRule).where(ReverseProxyRule.domain == body.domain)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Domain '{body.domain}' already has a proxy rule")

    # Resolve tenant: VM first, then first membership (admins can belong to many tenants)
    from app.models.user import TenantUser
    from app.models.vm import VirtualMachine

    tenant_id = None
    if body.vm_id:
        vm = (await db.execute(
            select(VirtualMachine).where(VirtualMachine.id == body.vm_id)
        )).scalar_one_or_none()
        if vm:
            tenant_id = vm.tenant_id

    if not tenant_id:
        tu = (await db.execute(
            select(TenantUser)
            .where(TenantUser.user_id == current_user.id)
            .order_by(TenantUser.created_at.asc())
            .limit(1)
        )).scalar_one_or_none()
        tenant_id = tu.tenant_id if tu else None

    if not tenant_id:
        raise HTTPException(status_code=400, detail="Could not determine tenant")

    rule = ReverseProxyRule(
        tenant_id=tenant_id,
        vm_id=body.vm_id,
        domain=body.domain.strip().lower(),
        target_ip=body.target_ip.strip(),
        target_port=body.target_port,
        ssl_mode=SSLMode(body.ssl_mode),
        ssl_enabled=False,
        websocket_support=body.websocket_support,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)

    # Sync to host agent
    await _sync_proxy_to_host_agent(db)

    await create_audit_log(
        db,
        action="proxy_rule_created",
        resource_type="reverse_proxy",
        resource_id=rule.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Proxy rule created: {rule.domain} → {rule.target_ip}:{rule.target_port}",
    )

    return rule


@router.patch("/{rule_id}", response_model=ProxyRuleResponse)
async def update_proxy_rule(
    rule_id: uuid.UUID,
    body: ProxyRuleUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a reverse proxy rule."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage proxy rules")

    rule = (await db.execute(
        select(ReverseProxyRule).where(ReverseProxyRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Proxy rule not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == "ssl_mode" and value is not None:
            setattr(rule, key, SSLMode(value))
        else:
            setattr(rule, key, value)

    await db.flush()
    await db.refresh(rule)

    # Sync to host agent
    await _sync_proxy_to_host_agent(db)

    await create_audit_log(
        db,
        action="proxy_rule_updated",
        resource_type="reverse_proxy",
        resource_id=rule.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=rule.tenant_id,
        description=f"Proxy rule updated: {rule.domain}",
    )

    return rule


@router.delete("/{rule_id}")
async def delete_proxy_rule(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a reverse proxy rule."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage proxy rules")

    rule = (await db.execute(
        select(ReverseProxyRule).where(ReverseProxyRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Proxy rule not found")

    domain = rule.domain
    tenant_id = rule.tenant_id

    await db.delete(rule)
    await db.flush()

    # Sync to host agent (will remove the config)
    await _sync_proxy_to_host_agent(db)

    await create_audit_log(
        db,
        action="proxy_rule_deleted",
        resource_type="reverse_proxy",
        resource_id=rule_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Proxy rule deleted: {domain}",
    )

    return {"status": "success", "message": f"Proxy rule for '{domain}' deleted"}


@router.post("/{rule_id}/ssl")
async def request_ssl(
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Request an SSL certificate for a proxy rule via Let's Encrypt."""
    if not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only admins can manage proxy rules")

    rule = (await db.execute(
        select(ReverseProxyRule).where(ReverseProxyRule.id == rule_id)
    )).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Proxy rule not found")

    try:
        async with httpx.AsyncClient(
            base_url=settings.HOST_AGENT_URL,
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            verify=AGENT_SSL_VERIFY,
            timeout=120.0,
        ) as client:
            resp = await client.post(
                "/api/v1/proxy/ssl",
                json={"domain": rule.domain},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"SSL request failed: {resp.text}")
            result = resp.json()
            if isinstance(result, dict) and result.get("status") in ("error", "failed", "warning"):
                raise HTTPException(
                    status_code=502,
                    detail=f"SSL request failed: {result.get('message') or result}",
                )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Host agent unreachable: {e}")

    # Update rule
    rule.ssl_enabled = True
    rule.ssl_mode = SSLMode.AUTO
    await db.flush()

    # Re-sync with SSL enabled
    await _sync_proxy_to_host_agent(db)

    await create_audit_log(
        db,
        action="proxy_ssl_enabled",
        resource_type="reverse_proxy",
        resource_id=rule.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=rule.tenant_id,
        description=f"SSL certificate requested for {rule.domain}",
    )

    return {"status": "success", "domain": rule.domain, "message": result.get("message", "SSL enabled")}
