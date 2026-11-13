"""
Pydantic schemas for Task/Job tracking.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.task import TaskStatus, TaskType


class TaskResponse(BaseModel):
    id: uuid.UUID
    task_type: TaskType
    status: TaskStatus
    user_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    resource_type: str | None
    resource_id: uuid.UUID | None
    progress_percent: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    logs: str | None
    retry_count: int
    max_retries: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    page: int
    page_size: int
