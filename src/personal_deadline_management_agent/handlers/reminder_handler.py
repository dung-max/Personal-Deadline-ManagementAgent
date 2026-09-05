"""Reminder HTTP handler (application boundary).

Receives HTTP requests, validates inputs via Pydantic schemas,
delegates use-case execution to ReminderModule, and returns API responses.
Does not contain business rules and does not bypass ReminderModule.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from ..dependencies import get_reminder_module
from ..modules.reminder_module import ReminderModule
from ..schemas.reminder import (
    ReminderCreateRequest,
    ReminderResponseData,
    ReminderUpdateRequest,
)
from ..schemas.task import SuccessResponse

router = APIRouter(prefix="/api/v1", tags=["reminders"])


@router.post(
    "/tasks/{taskId}/reminders",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[ReminderResponseData],
)
def create_reminder(
    taskId: UUID,
    payload: ReminderCreateRequest,
    module: ReminderModule = Depends(get_reminder_module),
) -> SuccessResponse[ReminderResponseData]:
    reminder = module.create_reminder(
        task_id=taskId,
        remind_at=payload.remind_at,
    )
    return SuccessResponse(
        message="Reminder created successfully",
        data=ReminderResponseData.from_domain(reminder),
    )


@router.get(
    "/tasks/{taskId}/reminders",
    response_model=SuccessResponse[list[ReminderResponseData]],
)
def list_task_reminders(
    taskId: UUID,
    module: ReminderModule = Depends(get_reminder_module),
) -> SuccessResponse[list[ReminderResponseData]]:
    reminders = module.list_reminders_by_task(taskId)
    return SuccessResponse(
        message="Reminders retrieved successfully",
        data=[ReminderResponseData.from_domain(r) for r in reminders],
    )


@router.patch(
    "/reminders/{reminderId}",
    response_model=SuccessResponse[ReminderResponseData],
)
def update_reminder(
    reminderId: UUID,
    payload: ReminderUpdateRequest,
    module: ReminderModule = Depends(get_reminder_module),
) -> SuccessResponse[ReminderResponseData]:
    update_kwargs = {}
    if "remind_at" in payload.model_fields_set:
        update_kwargs["remind_at"] = payload.remind_at
    if "status" in payload.model_fields_set:
        update_kwargs["status"] = payload.status

    reminder = module.update_reminder(reminderId, **update_kwargs)
    return SuccessResponse(
        message="Reminder updated successfully",
        data=ReminderResponseData.from_domain(reminder),
    )


@router.delete(
    "/reminders/{reminderId}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_reminder(
    reminderId: UUID,
    module: ReminderModule = Depends(get_reminder_module),
) -> Response:
    module.delete_reminder(reminderId)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
