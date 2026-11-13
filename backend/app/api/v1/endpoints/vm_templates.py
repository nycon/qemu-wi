"""
VM Template / Preset management endpoints.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_superadmin
from app.models.user import User
from app.models.vm_template import VMTemplate
from app.schemas.vm_template import VMTemplateCreate, VMTemplateResponse, VMTemplateUpdate
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/vm-templates", tags=["VM Templates"])


@router.get("", response_model=list[VMTemplateResponse])
async def list_templates(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all available VM templates (global + user's tenant templates)."""
    # Get user's tenant IDs
    tenant_ids = [tu.tenant_id for tu in (current_user.tenant_memberships or [])]

    # Show: global templates + templates from user's tenants
    conditions = [VMTemplate.is_global.is_(True)]
    if tenant_ids:
        conditions.append(VMTemplate.tenant_id.in_(tenant_ids))

    # Superadmins see everything
    if current_user.is_superadmin:
        result = await db.execute(
            select(VMTemplate)
            .where(VMTemplate.is_active.is_(True))
            .order_by(VMTemplate.is_global.desc(), VMTemplate.name)
        )
    else:
        result = await db.execute(
            select(VMTemplate)
            .where(VMTemplate.is_active.is_(True), or_(*conditions))
            .order_by(VMTemplate.is_global.desc(), VMTemplate.name)
        )

    return result.scalars().all()


@router.get("/{template_id}", response_model=VMTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific VM template."""
    template = await db.get(VMTemplate, template_id)
    if not template or not template.is_active:
        raise HTTPException(status_code=404, detail="Template not found")
    if current_user.is_superadmin or template.is_global:
        return template
    tenant_ids = [
        tu.tenant_id
        for tu in (current_user.tenant_memberships or [])
        if getattr(tu, "is_active", True)
    ]
    if template.tenant_id not in tenant_ids:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("", response_model=VMTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: VMTemplateCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new VM template."""
    # Only superadmins can create global templates
    if body.is_global and not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only superadmins can create global templates")

    # Determine tenant_id
    tenant_id = None
    if not body.is_global:
        tenant_ids = [tu.tenant_id for tu in (current_user.tenant_memberships or [])]
        if not tenant_ids:
            raise HTTPException(status_code=400, detail="No tenant assigned")
        tenant_id = tenant_ids[0]

    template = VMTemplate(
        tenant_id=tenant_id,
        created_by=current_user.id,
        name=body.name,
        description=body.description,
        icon=body.icon,
        vcpus=body.vcpus,
        ram_mb=body.ram_mb,
        disk_size_gb=body.disk_size_gb,
        cloud_image_id=body.cloud_image_id,
        default_user=body.default_user,
        packages=body.packages,
        runcmd=body.runcmd,
        timezone=body.timezone,
        keyboard_layout=body.keyboard_layout,
        locale=body.locale,
        ssh_keys=body.ssh_keys,
        is_global=body.is_global,
    )
    db.add(template)

    await create_audit_log(
        db,
        action="vm_template_created",
        resource_type="vm_template",
        resource_id=template.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"VM template created: '{body.name}' ({body.vcpus} vCPU, {body.ram_mb} MB RAM, {body.disk_size_gb} GB)"
            + (" [global]" if body.is_global else ""),
    )

    await db.flush()
    await db.refresh(template)
    return template


@router.patch("/{template_id}", response_model=VMTemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    body: VMTemplateUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a VM template."""
    template = await db.get(VMTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Check ownership
    if not current_user.is_superadmin and template.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if body.is_global is not None and body.is_global and not current_user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only superadmins can create global templates")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)

    await create_audit_log(
        db,
        action="vm_template_updated",
        resource_type="vm_template",
        resource_id=template.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=template.tenant_id,
        description=f"VM template updated: '{template.name}'",
    )

    await db.flush()
    await db.refresh(template)
    return template


@router.delete("/{template_id}")
async def delete_template(
    template_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a VM template."""
    template = await db.get(VMTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if not current_user.is_superadmin and template.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    name = template.name
    await db.delete(template)

    await create_audit_log(
        db,
        action="vm_template_deleted",
        resource_type="vm_template",
        resource_id=template_id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=template.tenant_id,
        description=f"VM template deleted: '{name}'",
    )

    return {"status": "success", "message": f"Template '{name}' deleted"}
