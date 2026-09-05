"""Task HTTP handler (application boundary).

Receives HTTP requests, validates inputs via Pydantic schemas,
delegates use-case execution to TaskModule, and returns API responses.
Does not contain business rules and does not bypass TaskModule.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from ..dependencies import get_task_module
from ..modules.task_module import TaskModule
from ..schemas.task import (
    SuccessResponse,
    TaskCreateRequest,
    TaskResponseData,
    TaskUpdateRequest,
)

router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[TaskResponseData],
)
def create_task(
    payload: TaskCreateRequest,
    module: TaskModule = Depends(get_task_module),
) -> SuccessResponse[TaskResponseData]:
    task = module.create_task(
        task_name=payload.task_name,
        description=payload.description,
        deadline=payload.deadline,
        priority=payload.priority,
    )
    return SuccessResponse(
        message="Task created successfully",
        data=TaskResponseData.from_domain(task),
    )


@router.get(
    "/tasks/{taskId}",
    response_model=SuccessResponse[TaskResponseData],
)
def get_task(
    taskId: UUID,
    module: TaskModule = Depends(get_task_module),
) -> SuccessResponse[TaskResponseData]:
    task = module.get_task(taskId)
    return SuccessResponse(
        message="Task retrieved successfully",
        data=TaskResponseData.from_domain(task),
    )


@router.get(
    "/tasks",
    response_model=SuccessResponse[list[TaskResponseData]],
)
def list_tasks(
    module: TaskModule = Depends(get_task_module),
) -> SuccessResponse[list[TaskResponseData]]:
    tasks = module.list_tasks()
    return SuccessResponse(
        message="Tasks retrieved successfully",
        data=[TaskResponseData.from_domain(t) for t in tasks],
    )


@router.patch(
    "/tasks/{taskId}",
    response_model=SuccessResponse[TaskResponseData],
)
def update_task(
    taskId: UUID,
    payload: TaskUpdateRequest,
    module: TaskModule = Depends(get_task_module),
) -> SuccessResponse[TaskResponseData]:
    update_kwargs = {}
    if "task_name" in payload.model_fields_set:
        update_kwargs["task_name"] = payload.task_name
    if "description" in payload.model_fields_set:
        update_kwargs["description"] = payload.description
    if "deadline" in payload.model_fields_set:
        update_kwargs["deadline"] = payload.deadline
    if "priority" in payload.model_fields_set:
        update_kwargs["priority"] = payload.priority
    if "status" in payload.model_fields_set:
        update_kwargs["status"] = payload.status

    task = module.update_task(taskId, **update_kwargs)
    return SuccessResponse(
        message="Task updated successfully",
        data=TaskResponseData.from_domain(task),
    )


@router.delete(
    "/tasks/{taskId}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_task(
    taskId: UUID,
    module: TaskModule = Depends(get_task_module),
) -> Response:
    module.delete_task(taskId)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
