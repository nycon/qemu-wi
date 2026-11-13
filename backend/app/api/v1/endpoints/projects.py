"""
Project management endpoints within a tenant.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import RequireTenantRole
from app.models.tenant import Project, ProjectUser
from app.models.user import User, UserRole
from app.schemas.tenant import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.audit_service import create_audit_log

router = APIRouter(prefix="/tenants/{tenant_id}/projects", tags=["Projects"])


def _is_tenant_admin(user: User, tenant_id: uuid.UUID) -> bool:
    return any(
        membership.tenant_id == tenant_id
        and membership.is_active
        and membership.role in (UserRole.TENANT_ADMIN, UserRole.SUPER_ADMIN)
        for membership in user.tenant_memberships
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    tenant_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all projects in a tenant."""
    query = select(Project).where(
        Project.tenant_id == tenant_id,
        Project.is_active.is_(True),
    )

    if not current_user.is_superadmin and not _is_tenant_admin(current_user, tenant_id):
        assignment_ids = (
            await db.execute(
                select(ProjectUser.project_id)
                .join(Project, Project.id == ProjectUser.project_id)
                .where(
                    ProjectUser.user_id == current_user.id,
                    Project.tenant_id == tenant_id,
                    Project.is_active.is_(True),
                )
            )
        ).scalars().all()
        # If the user has project assignments, treat them as an allow-list.
        # Users without assignments keep tenant-wide visibility (simple installs).
        if assignment_ids:
            query = query.where(Project.id.in_(assignment_ids))

    result = await db.execute(query.order_by(Project.name))
    return result.scalars().all()


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    tenant_id: uuid.UUID,
    body: ProjectCreate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new project in a tenant."""
    # Check uniqueness within tenant
    existing = await db.execute(
        select(Project).where(
            Project.tenant_id == tenant_id,
            Project.name == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project name already taken in this tenant",
        )

    project = Project(
        tenant_id=tenant_id,
        **body.model_dump(),
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    await create_audit_log(
        db,
        action="project_created",
        resource_type="project",
        resource_id=project.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Project created: {project.name}",
    )

    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.READONLY))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific project."""
    if not current_user.is_superadmin and not _is_tenant_admin(current_user, tenant_id):
        has_any_project_assignments = (
            await db.execute(
                select(ProjectUser.id)
                .join(Project, Project.id == ProjectUser.project_id)
                .where(
                    ProjectUser.user_id == current_user.id,
                    Project.tenant_id == tenant_id,
                    Project.is_active.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if has_any_project_assignments:
            has_access = (
                await db.execute(
                    select(ProjectUser.id).where(
                        ProjectUser.user_id == current_user.id,
                        ProjectUser.project_id == project_id,
                    )
                )
            ).scalar_one_or_none()
            if not has_access:
                raise HTTPException(status_code=403, detail="No access to this project")

    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    body: ProjectUpdate,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a project."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    db.add(project)
    await db.flush()
    await db.refresh(project)

    await create_audit_log(
        db,
        action="project_updated",
        resource_type="project",
        resource_id=project.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Project updated: {project.name}",
        metadata=update_data,
    )

    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(RequireTenantRole(UserRole.TENANT_ADMIN))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Soft-delete a project."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.is_active = False
    db.add(project)

    await create_audit_log(
        db,
        action="project_deleted",
        resource_type="project",
        resource_id=project.id,
        actor_id=current_user.id,
        actor_email=current_user.email,
        tenant_id=tenant_id,
        description=f"Project deactivated: {project.name}",
    )
