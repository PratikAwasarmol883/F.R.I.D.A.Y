import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from friday.api.dependencies import get_current_user
from friday.api.schemas import (
    ResearchResultOut,
    SourceOut,
    TaskCreate,
    TaskEventOut,
    TaskEventsOut,
    TaskOut,
    TaskResultOut,
    ToolUseOut,
    Usage,
)
from friday.core.config import Settings, get_settings
from friday.domain.task_state import SAFE_MESSAGES, TaskErrorCode, TaskStatus, TaskType
from friday.persistence.database import get_db_session
from friday.persistence.models import Task
from friday.services.auth_service import CurrentUser
from friday.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])

IdempotencyKey = Annotated[
    str | None, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]


def _error_message(code: str | None) -> str | None:
    try:
        return SAFE_MESSAGES[TaskErrorCode(code)] if code else None
    except ValueError:
        return None


def to_task_out(task: Task, answer: str | None) -> TaskOut:
    result: TaskResultOut | ResearchResultOut | None = None
    if task.status == TaskStatus.SUCCEEDED.value and task.result and task.type == TaskType.RESEARCH:
        stored = task.result
        result = ResearchResultOut(
            report_id=uuid.UUID(stored["report_id"]),
            usage=Usage(**stored["usage"]),
            sources_count=stored["sources_count"],
        )
    elif task.status == TaskStatus.SUCCEEDED.value and task.result:
        stored = task.result
        result = TaskResultOut(
            conversation_id=uuid.UUID(stored["conversation_id"]),
            message_id=uuid.UUID(stored["message_id"]),
            answer=answer,
            usage=Usage(**stored["usage"]),
            sources=[SourceOut(**item) for item in stored.get("sources", [])],
            tools_used=[ToolUseOut(**item) for item in stored.get("tools_used", [])],
        )
    return TaskOut(
        id=task.id,
        type=task.type,
        status=task.status,
        attempt=task.attempt,
        max_attempts=task.max_attempts,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        cancel_requested=task.status == TaskStatus.CANCEL_REQUESTED.value,
        error_code=task.error_code,
        error_message=_error_message(task.error_code),
        result=result,
    )


@router.post("", response_model=TaskOut, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    body: TaskCreate,
    response: Response,
    idempotency_key: IdempotencyKey = None,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TaskOut:
    created = await TaskService(session, settings).create(
        user,
        TaskType(body.type),
        body.input.model_dump(mode="json"),
        idempotency_key,
    )
    if not created.created:
        response.status_code = status.HTTP_200_OK
    response.headers["Location"] = f"/api/v1/tasks/{created.task.id}"
    return to_task_out(created.task, None)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TaskOut:
    view = await TaskService(session, settings).get(user, task_id)
    return to_task_out(view.task, view.answer)


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task_id: uuid.UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TaskOut:
    result = await TaskService(session, settings).cancel(user, task_id)
    if result.outcome == "cancel_requested":
        response.status_code = status.HTTP_202_ACCEPTED
    return to_task_out(result.task, None)


@router.get("/{task_id}/events", response_model=TaskEventsOut)
async def get_task_events(
    task_id: uuid.UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> TaskEventsOut:
    page = await TaskService(session, settings).events(user, task_id, after_sequence, limit)
    return TaskEventsOut(
        events=[
            TaskEventOut(
                sequence=e.sequence,
                event_type=e.event_type,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in page.events
        ],
        next_after_sequence=page.next_after_sequence,
    )
