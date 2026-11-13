"""
Task/Job monitoring endpoints.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import PaginationParams, get_current_user
from app.models.task import Task, TaskStatus
from app.models.tenant import Project, ProjectUser
from app.models.user import User
from app.models.vm import VirtualMachine
from app.schemas.task import TaskListResponse, TaskResponse

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    pagination: Annotated[PaginationParams, Depends()],
    task_status: TaskStatus | None = Query(None, alias="status"),
    tenant_id: uuid.UUID | None = None,
):
    """List tasks. Superadmins see all, users see their own."""
    query = select(Task)

    if not current_user.is_superadmin:
        assigned_vm_ids_subq = (
            select(VirtualMachine.id)
            .where(
                VirtualMachine.assigned_user_id == current_user.id,
                VirtualMachine.is_deleted.is_(False),
            )
            .subquery()
        )
        project_vm_ids_subq = (
            select(VirtualMachine.id)
            .join(Project, Project.id == VirtualMachine.project_id)
            .where(
                VirtualMachine.is_deleted.is_(False),
                Project.is_active.is_(True),
                VirtualMachine.project_id.in_(
                    select(ProjectUser.project_id).where(ProjectUser.user_id == current_user.id)
                ),
            )
            .subquery()
        )
        query = query.where(
            or_(
                Task.user_id == current_user.id,
                (
                    (Task.resource_type == "virtual_machine")
                    & (
                        Task.resource_id.in_(select(assigned_vm_ids_subq.c.id))
                        | Task.resource_id.in_(select(project_vm_ids_subq.c.id))
                    )
                ),
            )
        )

    if task_status:
        query = query.where(Task.status == task_status)
    if tenant_id:
        query = query.where(Task.tenant_id == tenant_id)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()

    # Fetch page
    query = query.order_by(Task.created_at.desc())
    query = query.offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    tasks = result.scalars().all()

    return TaskListResponse(
        items=tasks,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific task."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Access control
    if not current_user.is_superadmin and task.user_id != current_user.id:
        has_vm_access = False
        if task.resource_type == "virtual_machine" and task.resource_id:
            vm = await db.get(VirtualMachine, task.resource_id)
            has_project_access = False
            if vm and not vm.is_deleted:
                has_project_access = (
                    await db.execute(
                        select(ProjectUser.id)
                        .join(Project, Project.id == ProjectUser.project_id)
                        .where(
                            ProjectUser.user_id == current_user.id,
                            ProjectUser.project_id == vm.project_id,
                            Project.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none() is not None
            has_vm_access = bool(
                vm
                and not vm.is_deleted
                and (vm.assigned_user_id == current_user.id or has_project_access)
            )
        if not has_vm_access:
            raise HTTPException(status_code=403, detail="No access to this task")

    return task


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Cancel a queued task."""
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if not current_user.is_superadmin and task.user_id != current_user.id:
        has_vm_access = False
        if task.resource_type == "virtual_machine" and task.resource_id:
            vm = await db.get(VirtualMachine, task.resource_id)
            has_project_access = False
            if vm and not vm.is_deleted:
                has_project_access = (
                    await db.execute(
                        select(ProjectUser.id)
                        .join(Project, Project.id == ProjectUser.project_id)
                        .where(
                            ProjectUser.user_id == current_user.id,
                            ProjectUser.project_id == vm.project_id,
                            Project.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none() is not None
            has_vm_access = bool(
                vm
                and not vm.is_deleted
                and (vm.assigned_user_id == current_user.id or has_project_access)
            )
        if not has_vm_access:
            raise HTTPException(status_code=403, detail="No access to this task")

    if task.status != TaskStatus.QUEUED:
        raise HTTPException(
            status_code=409,
            detail="Only tasks with status 'queued' can be cancelled",
        )

    task.status = TaskStatus.CANCELLED
    db.add(task)
    await db.flush()
    await db.refresh(task)

    return task
